-- 0010: configurable digest schedule and a delivery state machine with hard duplicate protection.
--
-- Defaults: 09:00 Asia/Karachi (UTC+05:00). Weekly digests go out on Monday, monthly on day 1.
-- Users may change time, weekday, day of month and timezone (validated against the IANA database in
-- the API). Duplicate protection is the pre-existing UNIQUE (user_id, deduplication_key): the key
-- encodes the delivery period (digest) or the analysis result (alert), so a retry or a second
-- scheduler instance cannot create a second email for the same thing.

alter table public.notification_preferences
  alter column timezone set default 'Asia/Karachi',
  add column delivery_time time not null default '09:00'
    check (date_part('second', delivery_time) = 0),
  add column weekly_day smallint not null default 1
    check (weekly_day between 1 and 7),          -- ISO weekday: 1 = Monday ... 7 = Sunday
  add column monthly_day smallint not null default 1
    check (monthly_day between 1 and 28);         -- 29-31 do not exist in every month

-- 'queued' becomes 'pending' so the state names match the product vocabulary. No rows exist yet
-- in a fresh project; the UPDATE keeps the migration safe if any do.
alter table public.notification_history drop constraint notification_history_status_check;
update public.notification_history set status = 'pending' where status = 'queued';
alter table public.notification_history
  alter column status set default 'pending',
  add constraint notification_history_status_check check (status in
    ('pending', 'sending', 'sent', 'delivered', 'bounced', 'complained',
     'failed', 'skipped', 'suppressed')),
  add column scheduled_for  timestamptz not null default now(),
  add column sent_at        timestamptz,
  add column last_event_type text,
  add column last_event_at  timestamptz;

create index notification_history_due_idx
  on public.notification_history (scheduled_for) where status = 'pending';
create index notification_history_stuck_idx
  on public.notification_history (attempted_at) where status = 'sending';
