"""SQL used by the notification repository (bound parameters only; no string interpolation).

Executed against a real PostgreSQL in tests/db/test_notifications_sql.py and checked against the
repository's parameters by tests/test_sql_contract.py.
"""

from __future__ import annotations

LIST_PREFERENCES = """
select user_id::text                          as user_id,
       email                                  as email,
       enabled                                as enabled,
       digest_enabled                         as digest_enabled,
       alerts_enabled                         as alerts_enabled,
       digest_frequency                       as frequency,
       timezone                               as timezone,
       to_char(delivery_time, 'HH24:MI')      as local_time,
       weekly_day                             as weekly_day,
       monthly_day                            as monthly_day,
       minimum_alert_severity                 as minimum_alert_severity,
       consent_given_at                       as consent_given_at,
       email_verified_at                      as email_verified_at,
       last_digest_at                         as last_digest_at,
       array_to_json(selected_diseases)::text as selected_diseases,
       preferred_geography_id::text           as preferred_geography_id,
       unsubscribe_token::text                as unsubscribe_token
  from public.notification_preferences
 where enabled
 order by user_id
"""

# One row per new email. A conflict means the same period/analysis was already queued or sent:
# nothing is inserted and no id is returned. This statement is the duplicate guard.
ENQUEUE = """
insert into public.notification_history
       (user_id, notification_type, deduplication_key, subject, status, scheduled_for,
        analysis_result_id)
values (cast(:user_id as uuid), :notification_type, :deduplication_key, :subject, 'pending',
        cast(:scheduled_for as timestamptz), cast(:analysis_result_id as uuid))
on conflict (user_id, deduplication_key) do nothing
returning id::text as id
"""

# Atomically takes due rows; SKIP LOCKED lets two scheduler instances run without sharing a row.
CLAIM_DUE = """
with picked as (
  select id
    from public.notification_history
   where status = 'pending' and scheduled_for <= cast(:now as timestamptz)
   order by scheduled_for, id
   limit cast(:batch_size as integer)
   for update skip locked
)
update public.notification_history h
   set status = 'sending',
       attempt_count = h.attempt_count + 1,
       attempted_at = cast(:now as timestamptz)
  from picked, public.notification_preferences p
 where h.id = picked.id and p.user_id = h.user_id
returning h.id::text                          as id,
          h.user_id::text                     as user_id,
          h.notification_type                 as notification_type,
          h.deduplication_key                 as deduplication_key,
          h.analysis_result_id::text          as analysis_result_id,
          h.attempt_count                     as attempt_count,
          p.email                             as email,
          p.timezone                          as timezone,
          p.unsubscribe_token::text           as unsubscribe_token,
          p.preferred_geography_id::text      as preferred_geography_id,
          array_to_json(p.selected_diseases)::text as selected_diseases,
          p.enabled                           as enabled
"""

MARK_SENT = """
update public.notification_history
   set status = 'sent', provider_message_id = :provider_message_id, subject = :subject,
       sent_at = cast(:now as timestamptz), error_summary = null
 where id = cast(:id as uuid) and status = 'sending'
returning id::text as id
"""

# Transient failures return to 'pending' with a later scheduled_for until max_attempts is reached.
MARK_FAILED = """
update public.notification_history
   set status = case when cast(:retryable as boolean)
                          and attempt_count < cast(:max_attempts as integer)
                     then 'pending' else 'failed' end,
       scheduled_for = case when cast(:retryable as boolean)
                                 and attempt_count < cast(:max_attempts as integer)
                            then cast(:retry_at as timestamptz) else scheduled_for end,
       error_summary = left(:error, 200)
 where id = cast(:id as uuid) and status = 'sending'
returning status as status
"""

MARK_SKIPPED = """
update public.notification_history
   set status = 'skipped', error_summary = left(:reason, 200)
 where id = cast(:id as uuid) and status = 'sending'
returning id::text as id
"""

TOUCH_LAST_DIGEST = """
update public.notification_preferences
   set last_digest_at = cast(:at as timestamptz)
 where user_id = cast(:user_id as uuid)
returning user_id::text as user_id
"""

INSERT_EVENT = """
insert into public.email_events (webhook_event_id, provider_message_id, event_type, payload)
values (:webhook_event_id, :provider_message_id, :event_type, cast(:payload as jsonb))
on conflict (webhook_event_id) do nothing
returning id::text as id
"""

FIND_FOR_UPDATE = """
select id::text as id, status as status
  from public.notification_history
 where provider_message_id = :provider_message_id
 for update
"""

SET_STATUS = """
update public.notification_history
   set status = :status, last_event_type = :event_type,
       last_event_at = cast(:at as timestamptz),
       delivered_at = case when :status = 'delivered' then cast(:at as timestamptz)
                           else delivered_at end
 where id = cast(:id as uuid)
returning id::text as id
"""

EVENTS_FOR_MESSAGE = """
select event_type as event_type, received_at as received_at
  from public.email_events
 where provider_message_id = :provider_message_id
 order by received_at, id
"""

# Flagged results from the last window that are alertable by status and review state. Staleness
# and the small-sample test are computed here so the planner stays free of SQL.
ALERT_CANDIDATES = """
select a.id::text                       as analysis_result_id,
       a.status                         as status,
       a.review_status                  as review_status,
       a.disease                        as disease,
       a.geography_id::text             as geography_id,
       a.indicator                      as indicator,
       (a.latest_observation_end is null
        or a.latest_observation_end < cast(:today as date) - cast(:stale_days as integer))
                                        as stale,
       exists (select 1 from public.observations o
                where o.id = any(a.input_observation_ids)
                  and o.reporting_period_end = a.latest_observation_end
                  and 'small_denominator' = any(o.quality_flags))
                                        as small_sample
  from public.analysis_results a
 where a.analysis_type = 'anomaly_detection'
   and a.status in ('elevated', 'possible_anomaly')
   and a.created_at >= cast(:since as timestamptz)
 order by a.created_at, a.id
"""

# Latest anomaly-detection result per series, restricted to the user's selections.
DIGEST_ITEMS = """
select distinct on (a.geography_id, a.disease, a.pathogen, a.indicator, a.surveillance_stream)
       g.country_name                    as geography,
       a.disease                         as disease,
       a.indicator                       as indicator,
       a.surveillance_stream             as stream,
       a.status                          as status,
       a.latest_observation_end          as latest_observation_end,
       a.observed_value::text            as observed_value,
       a.baseline_value::text            as baseline_value,
       (select o.unit from public.observations o
         where o.geography_id = a.geography_id and o.disease = a.disease
           and o.indicator = a.indicator limit 1) as unit
  from public.analysis_results a
  join public.geographies g on g.id = a.geography_id
 where a.analysis_type = 'anomaly_detection'
   and a.review_status in ('auto', 'approved')
   and (cast(:geography_id as uuid) is null or a.geography_id = cast(:geography_id as uuid))
   and (jsonb_array_length(cast(:diseases as jsonb)) = 0
        or a.disease in (select jsonb_array_elements_text(cast(:diseases as jsonb))))
 order by a.geography_id, a.disease, a.pathogen, a.indicator, a.surveillance_stream,
          a.analysis_period_end desc, a.created_at desc
"""

STUCK_SENDING = """
select id::text as id, attempted_at as attempted_at
  from public.notification_history
 where status = 'sending' and attempted_at < cast(:before as timestamptz)
 order by attempted_at
"""

ALERT_ITEM = """
select g.country_name                    as geography,
       a.disease                         as disease,
       a.indicator                       as indicator,
       a.surveillance_stream             as stream,
       a.status                          as status,
       a.latest_observation_end          as latest_observation_end,
       a.observed_value::text            as observed_value,
       a.baseline_value::text            as baseline_value,
       (select o.unit from public.observations o
         where o.geography_id = a.geography_id and o.disease = a.disease
           and o.indicator = a.indicator limit 1) as unit
  from public.analysis_results a
  join public.geographies g on g.id = a.geography_id
 where a.id = cast(:analysis_result_id as uuid)
"""

AUTH_EMAIL = """
select email as email, email_confirmed_at as confirmed_at
  from auth.users
 where id = cast(:user_id as uuid)
"""

# The email address always comes from auth.users (never from the request). consent_given_at and
# the consent text version are written once and never overwritten by later saves.
UPSERT_PREFERENCES = """
insert into public.notification_preferences
       (user_id, email, email_verified_at, enabled, digest_enabled, alerts_enabled,
        digest_frequency, timezone, delivery_time, weekly_day, monthly_day,
        minimum_alert_severity, selected_diseases, consent_given_at, consent_text_version)
values (cast(:user_id as uuid), :email, cast(:email_verified_at as timestamptz), :enabled,
        :digest_enabled, :alerts_enabled, :frequency, :timezone, cast(:delivery_time as time),
        cast(:weekly_day as smallint), cast(:monthly_day as smallint), :minimum_alert_severity,
        array(select jsonb_array_elements_text(cast(:selected_diseases as jsonb))),
        case when cast(:consent as boolean) then cast(:now as timestamptz) end,
        case when cast(:consent as boolean) then :consent_version end)
on conflict (user_id) do update
   set email = excluded.email,
       email_verified_at = excluded.email_verified_at,
       enabled = excluded.enabled,
       digest_enabled = excluded.digest_enabled,
       alerts_enabled = excluded.alerts_enabled,
       digest_frequency = excluded.digest_frequency,
       timezone = excluded.timezone,
       delivery_time = excluded.delivery_time,
       weekly_day = excluded.weekly_day,
       monthly_day = excluded.monthly_day,
       minimum_alert_severity = excluded.minimum_alert_severity,
       selected_diseases = excluded.selected_diseases,
       consent_given_at = coalesce(public.notification_preferences.consent_given_at,
                                   excluded.consent_given_at),
       consent_text_version = coalesce(public.notification_preferences.consent_text_version,
                                       excluded.consent_text_version)
returning user_id::text as user_id
"""

# Unsubscribe by the secret per-user token. Pending emails are skipped at send time because
# CLAIM_DUE returns `enabled`.
UNSUBSCRIBE = """
update public.notification_preferences
   set enabled = false
 where unsubscribe_token = cast(:token as uuid)
returning user_id::text as user_id
"""

# One user's own settings row, with no `enabled` filter (unlike LIST_PREFERENCES, which is only
# for the scheduler) so a user can see "notifications are off" rather than get an empty result.
GET_PREFERENCES = """
select user_id::text                          as user_id,
       email                                  as email,
       enabled                                as enabled,
       digest_enabled                         as digest_enabled,
       alerts_enabled                         as alerts_enabled,
       digest_frequency                       as frequency,
       timezone                               as timezone,
       to_char(delivery_time, 'HH24:MI')      as local_time,
       weekly_day                             as weekly_day,
       monthly_day                            as monthly_day,
       minimum_alert_severity                 as minimum_alert_severity,
       consent_given_at                       as consent_given_at,
       email_verified_at                      as email_verified_at,
       last_digest_at                         as last_digest_at,
       array_to_json(selected_diseases)::text as selected_diseases,
       preferred_geography_id::text           as preferred_geography_id,
       unsubscribe_token::text                as unsubscribe_token
  from public.notification_preferences
 where user_id = cast(:user_id as uuid)
"""

# Recent delivery history for one user, newest first. Never selects the email body (not stored).
HISTORY = """
select id::text                   as id,
       notification_type         as notification_type,
       subject                   as subject,
       status                    as status,
       attempt_count             as attempt_count,
       scheduled_for             as scheduled_for,
       sent_at                   as sent_at,
       delivered_at              as delivered_at,
       error_summary             as error_summary,
       provider_message_id       as provider_message_id
  from public.notification_history
 where user_id = cast(:user_id as uuid)
 order by scheduled_for desc, id desc
 limit cast(:limit as integer)
"""

# A test send writes a final-status row directly (never 'pending'/'sending'), with a
# deduplication key keyed on the timestamp so repeated test sends are never blocked by the
# UNIQUE(user_id, deduplication_key) guard that protects real digests/alerts from duplicates.
INSERT_TEST_SEND = """
insert into public.notification_history
       (user_id, notification_type, deduplication_key, subject, status, scheduled_for,
        attempt_count, sent_at, provider_message_id, error_summary)
values (cast(:user_id as uuid), 'test', :deduplication_key, :subject,
        case when cast(:succeeded as boolean) then 'sent' else 'failed' end,
        cast(:now as timestamptz), 1,
        case when cast(:succeeded as boolean) then cast(:now as timestamptz) end,
        :provider_message_id, :error_summary)
returning id::text as id
"""
