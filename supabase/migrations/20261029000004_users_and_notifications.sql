-- 0004: users, roles, notification preferences/history, email webhook events.

-- Admin roles live in a table only the backend can read: never in request JSON, never in
-- user-editable JWT metadata.
create table public.user_roles (
  user_id    uuid not null references auth.users(id) on delete cascade,
  role       text not null check (role in ('admin')),
  granted_at timestamptz not null default now(),
  granted_by uuid references auth.users(id) on delete set null,
  primary key (user_id, role)
);

create table public.profiles (
  id                 uuid primary key references auth.users(id) on delete cascade,
  preferred_name     text check (preferred_name is null or char_length(preferred_name) <= 80),
  preferred_country  text check (preferred_country is null or preferred_country ~ '^[A-Z]{2}$'),
  preferred_region   text check (preferred_region is null or char_length(preferred_region) <= 120),
  preferred_language text not null default 'en' check (preferred_language ~ '^[a-z]{2,3}(-[A-Za-z0-9]+)?$'),
  timezone           text not null default 'UTC',   -- validated against the IANA database in the API
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();

-- Create the profile row automatically when a user signs up.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id) values (new.id) on conflict (id) do nothing;
  return new;
end;
$$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();
insert into public.profiles (id) select id from auth.users on conflict (id) do nothing;

create table public.notification_preferences (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null unique references auth.users(id) on delete cascade,
  -- Delivery address. Must equal the verified auth email (enforced by the API, which sets
  -- email_verified_at from auth.users.email_confirmed_at). Clients cannot write this table.
  email                 text not null check (position('@' in email) > 1),
  email_verified_at     timestamptz,
  consent_given_at      timestamptz,
  consent_text_version  text,
  enabled               boolean not null default false,     -- opt-in, never default-on
  digest_enabled        boolean not null default true,
  alerts_enabled        boolean not null default false,
  preferred_geography_id uuid references public.geographies(id) on delete set null,
  selected_diseases     text[] not null default '{}',
  selected_indicators   text[] not null default '{}',
  -- Only severities the analysis method actually defines.
  minimum_alert_severity text not null default 'possible_anomaly'
                          check (minimum_alert_severity in ('elevated', 'possible_anomaly')),
  digest_frequency      text not null default 'weekly'
                          check (digest_frequency in ('daily', 'weekly', 'monthly')),
  timezone              text not null default 'UTC',
  last_digest_at        timestamptz,
  unsubscribe_token     uuid not null default gen_random_uuid() unique,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint notification_enabled_requires_consent
    check (not enabled or (consent_given_at is not null and email_verified_at is not null))
);
create trigger notification_preferences_set_updated_at before update on public.notification_preferences
  for each row execute function public.set_updated_at();

create table public.notification_history (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references auth.users(id) on delete cascade,
  notification_type   text not null check (notification_type in ('digest', 'alert', 'test')),
  -- Deterministic key (e.g. alert:<analysis_result_id>, digest:<user>:<period>) => no duplicates.
  deduplication_key   text not null,
  subject             text not null,
  provider_message_id text,
  status              text not null default 'queued' check (status in
                        ('queued', 'sending', 'sent', 'delivered', 'bounced', 'complained',
                         'failed', 'skipped', 'suppressed')),
  attempt_count       integer not null default 0 check (attempt_count >= 0),
  attempted_at        timestamptz,
  delivered_at        timestamptz,
  error_summary       text,
  analysis_result_id  uuid references public.analysis_results(id) on delete set null,
  created_at          timestamptz not null default now(),
  constraint notification_history_dedup unique (user_id, deduplication_key)
);
create index notification_history_user_created_idx
  on public.notification_history (user_id, created_at desc);
create index notification_history_provider_msg_idx
  on public.notification_history (provider_message_id) where provider_message_id is not null;

-- Resend webhook events. webhook_event_id (the Svix message id) makes processing idempotent.
create table public.email_events (
  id                  uuid primary key default gen_random_uuid(),
  webhook_event_id    text not null unique,
  provider_message_id text,
  event_type          text not null,
  payload             jsonb not null default '{}'::jsonb,
  received_at         timestamptz not null default now()
);
create index email_events_message_idx on public.email_events (provider_message_id);
