# Notifications API (Phase 6)

Base path `/api/v1`. `/me/notifications*` endpoints run as role `authenticated` (Supabase JWT
required); `/notifications/unsubscribe` is public (the one-time token is the credential);
`/webhooks/resend` is verified by Svix signature headers, not a user token. Errors use the
standard envelope `{"error": {"code", "message", "request_id"}}`.

**Status: written but not yet run** as an HTTP server (same FastAPI limitation as
`docs/api_surveillance.md`); the SQL, schedule arithmetic and sender-selection logic behind
every endpoint are tested directly (`tests/notifications/`, `tests/db/test_notifications_sql.py`).

| Endpoint | Auth | Purpose |
|---|---|---|
| `PUT /me/notifications/preferences` | required | Save digest/alert schedule and consent |
| `GET /me/notifications` | required | Caller's own settings + recent delivery history |
| `POST /me/notifications/test` | required | Send a safe test email (no surveillance data) |
| `POST /notifications/unsubscribe` | public (token) | Unsubscribe by one-time token |
| `POST /webhooks/resend` | Svix signature | Resend delivery-event webhook |

## `PUT /me/notifications/preferences`

Requires `consent: true` when `enabled: true` (`422 consent_required` otherwise), and a verified
email (`409 email_not_verified` if the account's email hasn't been confirmed). Returns the next
scheduled delivery time in UTC plus the caller's own local time/timezone.

## `GET /me/notifications`

Returns the caller's own preferences (or `null` if they've never saved any) plus recent delivery
history. Never returns an email's subject body or HTML/text content -- only delivery metadata
(status, attempt count, timestamps, a truncated error summary).

## `POST /me/notifications/test`

Proves the preferences -> sender -> delivery-tracking pipeline end to end. The email sent is a
fixed, content-free confirmation (`TEST_SUBJECT`/`TEST_TEXT` in `app/api/v1/notifications.py`) --
it never carries a real surveillance figure, so this endpoint can never be how a public-health
claim reaches a user by accident. `mode` reports whether the send was `live`, redirected to a
dev inbox, or suppressed, per `app.notifications.sender.choose_sender`'s fail-closed policy
(see `docs/PHASE6_REPORT.md`).

## `POST /notifications/unsubscribe`

Always returns `204`, even for an unknown or already-used token, so the endpoint cannot be used
to probe which tokens exist.

## `POST /webhooks/resend`

Verifies `svix-id`/`svix-timestamp`/`svix-signature` headers against
`settings.resend_webhook_secret` before trusting the body (`app/notifications/webhook.py`).
Rejects with `401 invalid_webhook_signature` or `400 invalid_webhook_payload` rather than
recording an unverified event.

## Samples

`docs/samples/notifications.json` -- hand-built but schema-accurate (see the file's own
`_note`): request/response pairs for all five endpoints above, including the `422`/`409`/`401`/
`400` error shapes.
