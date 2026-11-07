# MTS Support Call — Sarah CSR

Sarah provides voice and chat support inside the signed-in MTS portal. The Next.js app runs on Vercel; the Python LiveKit worker runs separately. This release adds durable conversation history, server-confirmed account actions, receipts, customer context, billing-source lookup, approved knowledge search, and human escalation on the existing ticket.

Deploy the matching `mts-admin` branch first. Its additive migration and scoped support API are required. See the [portal release and integration guide](https://github.com/emilalvaroserrano-collab/mts-admin/blob/feat/csr-account-workflows/docs/csr-workspace.md).

## Configuration

- Worker: LiveKit credentials and `MTS_API_BASE_URL`. `CSR_REALTIME_MODEL=google/gemini-live` and `CSR_VOICE=Aoede` are defaults; `CSR_AMBIENCE_VOLUME=0.12` controls office ambience.
- Web: the same LiveKit project, `MTS_API_BASE_URL`, and exact comma-separated `NEXT_PUBLIC_MTS_PORTAL_ORIGINS`. No staff/admin API key belongs in the web app.
- Optional guest support: `CSR_ENABLE_GUEST_ACCESS=true`. Guests cannot read account records, history, or billing.
- Optional staff dispatch: set `MTS_OPERATOR_KEY` and `MTS_OPERATOR_EMAIL_ALLOWLIST` only on the worker. The legacy `MTS_SUPER_ADMIN_KEY` name is accepted for staff compatibility. Customer sessions never fall back to a staff credential.

The portal issues a one-time authenticated handoff. The web token route redeems it server-side, receives a short-lived credential bound to that customer and portal session, restores the conversation, and dispatches the worker. Portal logout or session rotation revokes that support credential.

Every account mutation opens a server-generated review sheet. After the customer presses Confirm, the backend claims and executes that exact action once. Catalog price changes invalidate the confirmation. A timeout carries an action ID for reconciliation; do not resubmit an uncertain request.

## Local verification

```sh
uv sync
uv run pytest
uv run ruff check src tests
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run lint:web
npm --prefix web run build
```

Use a non-production LiveKit project for voice simulations and real call tests. No live credentials are needed for the HTTP-transport unit tests. Browser/OS suspension may pause audio; durable history, in-app updates, and server-side push provide continuity after resume.

## Next steps

### Run and deploy your agent

**Get your agent running locally and in production:**

1. **Run locally**: Follow the [Quickstart](#quickstart) section below to set up your environment and test the agent
2. **Deploy to production**: See the [Deploy to production](#deploy-to-production) section for deployment options and best practices

### Quickstart

**Get up and running** so you can start customizing:

1. **Install dependencies:**
   ```console
   uv sync
   ```

2. **Set up your LiveKit credentials:**

   Sign up for [LiveKit Cloud](https://cloud.livekit.io/), then configure your environment. You can either:

   - **Manual setup**: Copy `.env.example` to `.env.local` and fill in:
     - `LIVEKIT_URL`
     - `LIVEKIT_API_KEY`
     - `LIVEKIT_API_SECRET`

   - **Automatic setup** (recommended): Use the [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/):
     ```bash
     lk cloud auth
     lk app env -w -d .env.local
     ```

3. **Download required models:**
   ```console
   uv run python src/agent.py download-files
   ```
   This downloads the model files used by the audio enhancement and noise cancellation plugins. [VAD](https://docs.livekit.io/agents/logic/turns/vad/) and [turn detection](https://docs.livekit.io/agents/logic/turns/turn-detector/) run in LiveKit Inference, so they need no local models.

4. **Test your agent:**
   ```console
   uv run python src/agent.py console
   ```
   This lets you speak to your agent directly in your terminal.

5. **Run for development:**
   ```console
   uv run python src/agent.py dev
   ```
   Use this when connecting to a frontend or telephony. This puts your agent into your LiveKit Cloud project, so use a different project if you don't want to affect production traffic.


## Customize your agent

### MTS portal API tools

Sarah's main CSR system prompt is [src/csr_system_prompt.md](src/csr_system_prompt.md), copied from the supplied brief. `src/agent.py` appends the current deployment's tool and account scope for guest, verified customer, or allowlisted operator sessions. The prompt describes some workflows that this agent cannot execute; only registered tools and verified API results establish current capabilities. There is no separate `KNOWLEDGE.md` loaded in this deployment.

The agent uses the MTS portal API at `MTS_API_BASE_URL` (default:
`https://mts.192-888-702-801.site`). Set `MTS_OPERATOR_KEY` in the local
ignored `.env.local` file and in your LiveKit Cloud deployment secrets. The
staff key is used only for authenticated operator calls. Customer calls use the portal-issued customer-scoped credential as the Bearer token. Never put it in source code,
LiveKit job metadata, browser code, or a participant token.

Customer tools are available only when your trusted backend explicitly
dispatches `mts-agent` with JSON job metadata containing `mts_customer_id` (a positive integer), `email`, `mts_session_token`, and `conversation_id`. The web token route supplies these from a redeemed portal handoff; do not construct them from customer-provided values. The agent checks that the
API record for that ID has the same email before accessing portal data. A
spoken or typed email does not establish access. Example metadata produced
by your backend after authenticating the portal session:

```json
{"mts_customer_id": 42, "email": "customer@example.com"}
```

Customer actions cover the portal account, menu, home, shop, billing,
organization, tickets, orders, services, complaints, and notices. The API provides order creation and hosted payment links, subject to customer permissions and a server-generated confirmation. Creating a service, ticket, or
complaint sends a live request. The agent confirms consequential changes
before calling a write tool.

For authorized staff only, set `MTS_OPERATOR_EMAIL_ALLOWLIST` to a
comma-separated list of operator emails and have your trusted backend add
`"mts_operator": true` to that operator's dispatch metadata. The extra tools
search, create, and select customers. Permanent customer/order deletion is disabled in the CSR API. The backend must authenticate
the operator and control the dispatch metadata; clients must not be allowed
to supply or change it. Operator search and selection bind one customer for
the same portal tools. Keep the allowlist empty for customer-only mode.

`scenarios.yaml` contains access-control scenarios. Run them in CI with
`lk agent simulate text --scenarios scenarios.yaml` after configuring LiveKit
credentials and a text-capable test model. Run `uv run pytest`,
`uv run ruff format src tests`, and `uv run ruff check src tests` locally.

Once your agent is running, enhance it for your use case:

- **Customize AI models**: Your agent uses a voice AI pipeline built on [LiveKit Inference](https://docs.livekit.io/agents/models/inference). More than 50 model providers are supported, including [Realtime models](https://docs.livekit.io/agents/models/realtime).

- **Add tests**: You can add a full test suite to your agent. See the [testing documentation](https://docs.livekit.io/testing/overview/) for more information.

- **Build reliable workflows**: For complex agents, use [tasks and handoffs](https://docs.livekit.io/agents/logic/workflows/) instead of long instruction prompts. This minimizes latency and improves reliability by structuring your agent into focused, reusable components.

### Get help from AI coding assistants

**Supercharge your development** with AI coding assistants that understand LiveKit. This project works seamlessly with [Claude Code](https://claude.com/product/claude-code), [Cursor](https://www.cursor.com/), [Codex](https://openai.com/codex/), and other AI coding tools.

For your convenience, LiveKit offers both a CLI and an [MCP server](https://docs.livekit.io/reference/developer-tools/docs-mcp/) that can be used to browse and search its documentation. The [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) (`lk docs`) works with any coding agent that can run shell commands. Install it for your platform:

**macOS:**

```console
brew install livekit-cli
```

**Linux:**

```console
curl -sSL https://get.livekit.io/cli | bash
```

**Windows:**

```console
winget install LiveKit.LiveKitCLI
```

Check your version with `lk --version` and update if needed. Once installed, your coding agent can search and browse LiveKit documentation directly from the terminal:

```console
lk docs search "voice agents"
lk docs get-page /agents/start/voice-ai-quickstart
```

**Install LiveKit's agent skills**: [Agent skills](https://docs.livekit.io/intro/coding-agents/#agent-skills) teach your coding agent how to build, debug, test, and deploy LiveKit agents, and point it to the latest documentation for API details. From this project's directory, install them for the coding agents on your machine:

```console
lk skills install
```

This also adds the LiveKit Docs MCP server to each coding agent's config. Run `lk skills update` to get the latest skills, and commit `skills-lock.json` along with them.

See the [Using coding agents](https://docs.livekit.io/intro/coding-agents/) guide for more details, including MCP server setup.

**Customize the AI assistant context**: The project includes an [AGENTS.md](AGENTS.md) file that guides AI assistants on how to work with this codebase. **Edit this file** to add your own project-specific context, patterns, and preferences. Learn more at [https://agents.md](https://agents.md).

## Frontend development

If you don't already have a frontend, use the following templates and guides to get started on one:

| Platform | Starter Template | What to customize |
|----------|----------|-------------|
| **Web** | [`livekit-examples/agent-starter-react`](https://github.com/livekit-examples/agent-starter-react) | React & Next.js—customize UI, add features, integrate with your backend |
| **iOS/macOS** | [`livekit-examples/agent-starter-swift`](https://github.com/livekit-examples/agent-starter-swift) | Native apps for iOS, macOS, visionOS—add platform-specific features |
| **Flutter** | [`livekit-examples/agent-starter-flutter`](https://github.com/livekit-examples/agent-starter-flutter) | Cross-platform—customize for Android, iOS, web, desktop |
| **React Native** | [`livekit-examples/voice-assistant-react-native`](https://github.com/livekit-examples/voice-assistant-react-native) | Mobile with Expo—add native modules, customize navigation |
| **Android** | [`livekit-examples/agent-starter-android`](https://github.com/livekit-examples/agent-starter-android) | Kotlin & Jetpack Compose—build Material Design UI |
| **Web Embed** | [`livekit-examples/agent-starter-embed`](https://github.com/livekit-examples/agent-starter-embed) | Widget for any website—customize styling, add to your site |
| **Telephony** | [Documentation](https://docs.livekit.io/telephony/) | Add phone calling—configure SIP, add call routing, customize prompts |

## Observability

LiveKit provides deep session insights for your agents through [Agent Observability](https://docs.livekit.io/testing/observability/). Monitor conversation quality, track latency metrics, and debug agent behavior in production.

## Deploy to production

To deploy your agent to production, you can use the LiveKit CLI:

```console
lk agent create
```

See the [deploying to production](https://docs.livekit.io/deploy/agents/) guide for detailed instructions and optimization tips.

## Join the LiveKit community

Join the [LiveKit Slack Community](https://livekit.io/join-slack) to get help from the LiveKit team and other developers.
