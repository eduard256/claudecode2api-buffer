# claudecode2api-buffer

Message buffer for [claudecode2api](https://github.com/eduard256/claudecode2api). Accumulates messages, waits for silence, sends them as a single batch to Claude Code API while maintaining a persistent session.

```
User A ──► POST /v1/message ──┐
                               ├──► buffer ──[25s silence]──► Claude Code API
User B ──► POST /v1/message ──┘         ▲                         │
                                         │                         │ SSE stream
User C ──► POST /v1/message ──► pending  │                         │
                                         └─────── [done] ◄────────┘
```

## Quick start

```bash
git clone https://github.com/eduard256/claudecode2api-buffer.git
cd claudecode2api-buffer
cp .env.example .env
# Edit .env with your claudecode2api credentials
docker compose up -d
```

First run auto-creates `config/system-prompt.md` from the example template. Edit it to fit your use case.

Verify:

```bash
curl http://localhost:3856/v1/health
# {"ok": true}
```

Send a message:

```bash
curl -X POST http://localhost:3856/v1/message \
  -u admin:password \
  -H "Content-Type: application/json" \
  -d '{"text": "@alice: hello world"}'
```

## How it works

Three states, two buffers, two timers:

```
          message              timer expired           claude done
 IDLE ──────────► BUFFERING ──────────────► PROCESSING ──────────► IDLE
                    │    ▲                      │
                    │    │ reset timer           │ message
                    └────┘                      ▼
                                          pending_buffer
                                          pending_timer
```

**IDLE** — nothing happening.

**BUFFERING** — messages accumulate in buffer. Each new message resets the timer. When timer expires, entire buffer is sent to Claude as one prompt (messages joined with `\n`).

**PROCESSING** — Claude is working. New messages go to `pending_buffer` with its own timer. When Claude finishes:
- Pending empty → back to IDLE.
- Pending has messages, timer still running → wait for it.
- Pending timer expired while Claude was still active → cancel Claude, send pending immediately.

Session ID persists to disk (`/data/session.json`), so Claude remembers conversation context across restarts. Everything else lives in memory.

## Configuration

All settings via `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3856` | Server port |
| `BASIC_AUTH_USER` | `admin` | API username |
| `BASIC_AUTH_PASS` | `password` | API password |
| `BUFFER_TIMEOUT` | `25` | Seconds of silence before sending |
| `CLAUDE_API_URL` | `http://localhost:9876` | claudecode2api URL |
| `CLAUDE_API_USER` | | claudecode2api username |
| `CLAUDE_API_PASS` | | claudecode2api password |
| `WORKSPACE_DIR` | `/home/user/menu-workspace` | Claude working directory |
| `SYSTEM_PROMPT_FILE` | `/config/system-prompt.md` | System prompt path |
| `CLAUDE_TOOLS` | `["Bash"]` | Visible tools (JSON array) |
| `CLAUDE_ALLOWED_TOOLS` | `["Bash"]` | Auto-approved tools (JSON array) |

### Tool restriction examples

```env
# Read-only access
CLAUDE_TOOLS=["Read","Glob","Grep"]
CLAUDE_ALLOWED_TOOLS=["Read","Glob","Grep"]

# Bash limited to git commands
CLAUDE_TOOLS=["Bash"]
CLAUDE_ALLOWED_TOOLS=["Bash(git:*)"]

# Only MCP tools
CLAUDE_TOOLS=[]
CLAUDE_ALLOWED_TOOLS=["mcp__myserver__search"]
```

## API

Full reference with curl examples: [API.md](API.md)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/v1/health` | no | Health check |
| POST | `/v1/message` | yes | Add message to buffer |
| GET | `/v1/status` | yes | Buffer state, timers, contents |
| GET | `/v1/response` | yes | Poll current Claude response |
| GET | `/v1/history` | yes | Completed batch history |
| DELETE | `/v1/session` | yes | Reset Claude session |

## Project structure

```
app/
├── main.py           # FastAPI app, logging, lifespan
├── api.py            # HTTP endpoints
├── buffer.py         # State machine, timers, dispatch
├── claude_client.py  # SSE streaming client for claudecode2api
├── config.py         # ENV configuration
└── models.py         # Pydantic models, state enum
```

## Updating

```bash
git pull
docker compose up -d --build
```

Your `.env` and `config/system-prompt.md` are gitignored — updates only touch code and `.example` templates.
