# Connecting a frontend (e.g. Lovable) to VirusWatch

This is the practical guide for wiring a frontend to this backend: CORS, the Supabase JWT auth
flow, environment variables, and the error envelope shape. It assumes the frontend talks to
Supabase Auth directly for login/signup and to this API for everything else.

**Status note:** this backend has never been started as a live HTTP server in the build sandbox
(FastAPI is not installable here -- see `docs/api_surveillance.md`). Everything below describes
what the code in `app/main.py`, `app/config.py` and `app/core/auth.py` actually does, verified by
reading it, not by running a request against it. Test it against a real deployment before
connecting a production frontend.

## 1. CORS

The API's CORS policy (`app/main.py`, `CORSMiddleware`) is built from one setting:

```
ALLOWED_ORIGINS=https://your-app.lovable.app,https://your-custom-domain.com
```

Rules enforced by `Settings.cors_origins` (`app/config.py`), checked at startup:

- Comma-separated, explicit `http://` or `https://` origins only -- `*` is rejected outright.
- In `production` / `staging`, `ALLOWED_ORIGINS` must be set at all, and every origin must be
  `https://` (an `http://` origin raises a startup error in production).
- `allow_credentials=False` -- the API expects a `Bearer` token in the `Authorization` header,
  never a cookie, so there's nothing to set `credentials: "include"` for on the frontend side.
- Allowed methods: `GET, POST, PUT, DELETE, OPTIONS`. Allowed request headers: `Authorization`,
  `Content-Type`, `X-Request-ID`. The response always exposes `X-Request-ID` (echo it back in
  bug reports -- it's also in every error body).

**Action for you:** add your Lovable preview URL(s) and your production domain to
`ALLOWED_ORIGINS` before connecting the frontend. A request from an origin not on that list is
rejected by the browser itself (standard CORS behavior), not by a VirusWatch error code.

## 2. Authentication: Supabase JWT, not a VirusWatch login endpoint

This backend does **not** issue its own tokens or have a `/login` route. Sign-up, sign-in,
password reset and session refresh all happen directly against Supabase Auth from the frontend
(the Supabase JS client, or Lovable's built-in Supabase integration). VirusWatch only verifies
the token Supabase already gave the frontend.

1. Frontend signs the user in via Supabase Auth (`supabase.auth.signInWithPassword`,
   magic link, OAuth, etc.) and gets back a session with an `access_token`.
2. For every authenticated VirusWatch request, send that token:
   ```
   Authorization: Bearer <access_token>
   ```
3. `app/core/auth.py`'s `TokenVerifier` checks the token server-side:
   - Reads the JWT header's `alg` and only accepts an explicit allow-list: `ES256`/`RS256`
     (verified against your Supabase project's JWKS endpoint,
     `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`) or `HS256` (verified against
     `SUPABASE_JWT_SECRET`, the legacy shared-secret signing mode). `alg: none` or anything else
     is rejected.
   - Checks the issuer (`<SUPABASE_URL>/auth/v1`) and expiry.
   - Builds an `AuthenticatedUser` from the verified claims; an anonymous Supabase session (if
     you have anonymous sign-ins enabled) is accepted by the token check but then rejected by
     `require_user` with `403 forbidden` on any endpoint that needs a real account (`/me`,
     `/me/notifications*`).
4. The same verified user id is attached to the database connection as a claim, so
   PostgREST-style row-level security (RLS) -- not application code -- decides what the request
   can see or change (`app/api/deps.py user_conn`).

**Which endpoints need a token:** see the per-area docs --
[`docs/api_surveillance.md`](api_surveillance.md) (all public, no token),
[`docs/api_ai.md`](api_ai.md) (public), [`docs/api_me.md`](api_me.md) (token required),
[`docs/api_notifications.md`](api_notifications.md) (token required except unsubscribe and the
Resend webhook, which use their own credentials instead).

**Session refresh:** handle this the normal Supabase-client way (it auto-refreshes the access
token before expiry); just always read the *current* `access_token` from the Supabase session
when making a VirusWatch request, rather than caching one at login time.

## 3. Frontend environment variables

| Variable | Value | Used for |
|---|---|---|
| `VITE_SUPABASE_URL` (or your framework's equivalent) | Same value as this backend's `SUPABASE_URL` | Supabase Auth client, direct Supabase queries if any |
| `VITE_SUPABASE_ANON_KEY` | Same project's anon/public key | Supabase Auth client |
| `VITE_API_BASE_URL` | This backend's deployed base URL, e.g. `https://api.yourdomain.com/api/v1` | Every VirusWatch API call below |

The backend's own relevant environment variables (set on the API deployment, not the frontend)
are `ALLOWED_ORIGINS`, `SUPABASE_URL`, `SUPABASE_JWT_SECRET` (if using HS256), `SUPABASE_DB_URL`,
and the Phase 5/6 provider keys (`GEMINI_API_KEY`, `RESEND_API_KEY`) -- see `app/config.py` for
the complete, authoritative list; nothing here duplicates defaults that could drift from it.

## 4. The error envelope

Every error response -- whatever endpoint, whatever status code -- has this shape
(`app/core/errors.py`):

```json
{
  "error": {
    "code": "consent_required",
    "message": "Consent is required to enable email delivery.",
    "request_id": "a1b2c3d4-...",
    "details": { "...": "optional, only on some errors" }
  }
}
```

Handle it generically: show `error.message` to the user (or map `error.code` to your own
translated copy if you need localization), and log `error.request_id` for support -- the same id
is also returned as the `X-Request-ID` response header. A `401` response additionally carries a
`WWW-Authenticate: Bearer` header -- useful as a hook to trigger a token refresh or redirect to
login.

Common codes you'll want to handle explicitly: `invalid_token` / `auth_unavailable` (401, re-auth
or show a configuration error), `forbidden` (403, anonymous session hit an endpoint needing a
real account), `consent_required` / `email_not_verified` (422/409, notification preferences
flow), `invalid_question` (422, `/ai/ask` validation).

## 5. Quick integration checklist

1. Add the frontend's origin(s) to `ALLOWED_ORIGINS` on the API deployment.
2. Point the frontend's Supabase client at the same Supabase project as `SUPABASE_URL`.
3. Send `Authorization: Bearer <access_token>` on every call to an authenticated endpoint; send
   nothing extra for the public surveillance/AI endpoints.
4. Render `error.message` / `error.code` from the standard envelope on any non-2xx response.
5. For `/ai/ask`, leave `disease` unset when you want the Phase 8 "what's currently most
   prevalent, how do I protect myself" behavior -- naming a disease explicitly always takes
   priority over the auto-detected signal (see `docs/api_ai.md`).
