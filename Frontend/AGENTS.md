<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## VirusWatch architecture rules
- Surveillance data comes only from the external FastAPI backend through `src/lib/api/` (`src/lib/api/endpoints.ts` is the documented endpoint contract). Never render mock, simulated, or invented surveillance data; missing backend values render as "—", never as zero. Rationale: the project spec forbids fabricated statistics and requires clear unavailable states.
- The backend base URL is runtime-configurable (Settings page → localStorage, default `VITE_API_BASE_URL`). Data queries stay disabled until `/health` responds.
- The AI assistant streams via the backend's SSE endpoint `POST /api/ai/ask/stream` (contract documented in `src/lib/api/streaming.ts`); only backend-emitted steps, deltas and citations are rendered, and the client falls back to the plain `/api/ai/ask` endpoint when the stream is unavailable.
- Roles live in `public.user_roles` (admin/analyst/user); the Administration page and sidebar entry check the caller's own roles via RLS-scoped reads, and admin actions stay disabled until backend endpoints define them.
- Auth: Lovable Cloud email/password + Google + Sign in with Lovable; protected routes live under `src/routes/_authenticated/` (managed gate redirects to `/auth`).
