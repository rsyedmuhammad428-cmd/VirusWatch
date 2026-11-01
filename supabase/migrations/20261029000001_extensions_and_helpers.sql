-- 0001: extensions and shared helpers.
-- Requires PostgreSQL 15+ (UNIQUE NULLS NOT DISTINCT, security_invoker views). Supabase satisfies this.

create schema if not exists extensions;
create extension if not exists vector with schema extensions;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
