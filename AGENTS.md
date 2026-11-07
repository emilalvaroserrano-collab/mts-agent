# AGENTS.md

MTS support voice agent (LiveKit Agents, Python) in `src/`, plus a Next.js web console in `web/` that deploys to Vercel.

## Tooling

- Package manager: **`uv`** — not pip. Run Python commands with `uv run <command>`.
- Agent entrypoint: **`src/agent.py`**. The `Dockerfile` runs `uv run src/agent.py start` — keep it as the entrypoint.
- Format: `uv run ruff format src tests`
- Lint: `uv run ruff check src tests`
- Tests: `uv run pytest` (5 tests in `tests/test_mts_tools.py`; they use an in-memory fake MTS API, no network)
- Ruff config is in `pyproject.toml` (line-length 88; `E501`, `W291`, `RUF001` ignored).

## Environment

- `.env` (gitignored): LiveKit credentials — `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
- `.env.local` (gitignored): `MTS_SUPER_ADMIN_KEY`, `MTS_API_BASE_URL`, `MTS_OPERATOR_EMAIL_ALLOWLIST`, `CSR_ENABLE_GUEST_ACCESS`.
- `agent.py` loads `.env` **then** `.env.local`, so `.env.local` overrides. Template: `.env.example`. Never commit real keys.

## Local development

1. `uv sync`
2. `uv run python src/agent.py download-files` — downloads ai-coustics noise-cancellation models (VAD/turn detection run server-side)
3. Terminal 1: `uv run python src/agent.py dev` — connects to LiveKit Cloud; use a non-production project to avoid affecting traffic
4. Terminal 2: `cd web && npm ci && npm run dev`, then open `http://localhost:3000` with `CSR_ENABLE_GUEST_ACCESS=true` in `.env.local`
5. Terminal-only chat: `uv run python src/agent.py console`

## Deploy boundaries

### Web app (`web/`) — Vercel

The Next.js app in `web/` deploys to Vercel:

1. Create a Vercel project with **Root Directory `web`** and Framework Preset **Next.js**.
2. Set these **server environment variables** in Vercel:
   - `LIVEKIT_URL`
   - `LIVEKIT_API_KEY`
   - `LIVEKIT_API_SECRET`
   - `CSR_ENABLE_GUEST_ACCESS=true` — only if you want anonymous visitors to start support calls
3. **Do NOT** set `MTS_SUPER_ADMIN_KEY` on Vercel — that belongs to the Python worker only.
4. Connect the GitHub repo and deploy. The token route at `/api/token` issues short-lived guest tokens; it ignores client-supplied identity, room, and metadata.
5. Protect or rate-limit the public token endpoint before enabling it on an unrestricted public domain — each call consumes LiveKit resources.

### Python worker — LiveKit Cloud (separate deployment)

Deploy the Python worker via the `Dockerfile` to LiveKit Cloud — it needs persistent connections and cannot run as a Vercel function. Configure `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `MTS_API_BASE_URL`, and `MTS_SUPER_ADMIN_KEY`. Set `MTS_OPERATOR_EMAIL_ALLOWLIST` only if a trusted backend will issue authenticated operator dispatches. Both deployments must use the same LiveKit project.

## Testing agent behavior

- `lk agent simulate text --scenarios scenarios.yaml` — access-control simulations; needs LiveKit credentials and a text-capable test model. Run before and after changing instructions, tools, or dispatch behavior.
- `lk agent debugger start` → `lk agent debugger say "..."` → `lk agent debugger restart` after every code edit → `lk agent debugger stop` (CLI >= 2.18.8). Add `--logs` to `say` to see tracebacks.
- When you change core agent behavior, write a scenario for it first, then iterate until it passes.

## Code notes

- Tests import `from agent import ...` (the project loads editable via `uv run`; `src/` is the package root per `pyproject.toml`).
- In tests, call registered tools through `_func` (see `tests/test_mts_tools.py`).
- Customer tools require trusted dispatch metadata with both `mts_customer_id` and `email`; a spoken email never authenticates. Operator tools additionally require `MTS_OPERATOR_EMAIL_ALLOWLIST` and `"mts_operator": true` in dispatch metadata — clients must not supply either.
- See `README.md` for the full run/deploy guide and LiveKit docs (`lk docs`).
