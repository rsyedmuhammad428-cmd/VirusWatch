# User profile API (Phase 2, enhanced Phase 6)

Base path `/api/v1`. `GET /me` runs as database role `authenticated` (`user_conn`), with the
caller's own claims attached to the connection -- row-level security guarantees only the
caller's own `profiles` row can ever match, independent of the query's `WHERE` clause. Errors
use the standard envelope `{"error": {"code", "message", "request_id"}}`.

**Status: written but not yet run.** Same sandbox limitation as `docs/api_surveillance.md`
(FastAPI could not be installed here).

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /me` | required (Supabase JWT) | Current user's id, email, admin flag, and saved profile |

## Auth

`Authorization: Bearer <Supabase JWT>`. A missing or invalid token, or a token for an anonymous
session, is rejected by `require_user` with `403 forbidden` ("A registered account is
required."). There is no separate login endpoint here -- authentication itself is Supabase Auth
(see the Lovable integration guide, `docs/FRONTEND_INTEGRATION.md`, for the token flow).

## Response shape

`MeOut`: `id` (UUID), `email`, `is_admin` (looked up from `public.user_roles`, never trusted
from the JWT claims directly), `profile` (nullable `ProfileOut`: `preferred_name`,
`preferred_country`, `preferred_region`, `preferred_language`, `timezone`, `updated_at`). A
`null` profile means the user exists but has never saved profile settings yet -- it is not an
error.

## Samples

`docs/samples/me.json` -- hand-built but schema-accurate (see the file's own `_note`): one
example with a saved profile, one with `profile: null`.

## Not yet built

There is no `PUT /me` (editing the profile) in this phase; profile rows are currently written
only by the signup/onboarding flow this backend does not own. If the frontend needs to let a
user edit their profile directly, say so and a `PUT /me` endpoint can be added.
