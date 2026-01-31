# claudecode2api-buffer API

Message buffer for Claude Code API. Batches messages, waits for silence, sends as one request.

**Auth:** Basic Auth on all endpoints except `/v1/health`.

---

## Endpoints

### GET /v1/health

No auth.

```bash
curl http://localhost:3856/v1/health
```
```json
{"ok": true}
```

---

### POST /v1/message

Add message to buffer. Resets timer on each call.

```bash
curl -X POST http://localhost:3856/v1/message \
  -u admin:password \
  -H "Content-Type: application/json" \
  -d '{"text": "@alice: добавь борщ в меню"}'
```
```json
{"ok": true}
```

Multiple messages → batched into one Claude request after `BUFFER_TIMEOUT` seconds of silence.

```bash
# Message 1
curl -X POST http://localhost:3856/v1/message \
  -u admin:password \
  -H "Content-Type: application/json" \
  -d '{"text": "@alice: хочу борщ"}'

# Message 2 (timer resets)
curl -X POST http://localhost:3856/v1/message \
  -u admin:password \
  -H "Content-Type: application/json" \
  -d '{"text": "@bob: и салат цезарь"}'

# After 25s silence → both sent as:
# "@alice: хочу борщ\n@bob: и салат цезарь"
```

Messages during Claude processing go to pending buffer, sent automatically after current request completes.

---

### GET /v1/status

Current buffer state.

```bash
curl -u admin:password http://localhost:3856/v1/status
```
```json
{
  "state": "BUFFERING",
  "current_buffer": ["@alice: хочу борщ", "@bob: и салат"],
  "pending_buffer": [],
  "timer_remaining": 18.5,
  "pending_timer_remaining": null,
  "session_id": "68770835-e538-4052-8345-ae6087f2c653"
}
```

**States:**
| State | Meaning |
|-------|---------|
| `IDLE` | Empty, waiting |
| `BUFFERING` | Messages in buffer, timer ticking |
| `PROCESSING` | Claude working, new messages → pending |

---

### GET /v1/response

Poll current Claude response (while `PROCESSING`).

```bash
curl -u admin:password http://localhost:3856/v1/response
```

Active:
```json
{
  "active": true,
  "text": "Добавляю борщ в меню...",
  "tool_calls": [
    {
      "tool": "Bash",
      "input": "cat menu.txt",
      "result": "1. Паста\n2. Салат",
      "timestamp": "2026-01-31T12:00:32Z"
    }
  ],
  "started_at": "2026-01-31T12:00:30Z"
}
```

Idle:
```json
{
  "active": false,
  "text": null,
  "tool_calls": [],
  "started_at": null
}
```

---

### GET /v1/history

All sent batches with responses.

```bash
curl -u admin:password http://localhost:3856/v1/history
```
```json
{
  "batches": [
    {
      "id": 1,
      "messages": ["@alice: хочу борщ", "@bob: и салат"],
      "sent_at": "2026-01-31T12:00:00Z",
      "completed_at": "2026-01-31T12:00:45Z",
      "response": "Добавил борщ и салат в меню.",
      "cancelled": false
    },
    {
      "id": 2,
      "messages": ["@carol: отмена борща"],
      "sent_at": "2026-01-31T12:01:00Z",
      "completed_at": null,
      "response": null,
      "cancelled": true
    }
  ]
}
```

---

### DELETE /v1/session

Reset Claude session. Next request starts fresh context. Buffers and history preserved.

```bash
curl -X DELETE -u admin:password http://localhost:3856/v1/session
```
```json
{"ok": true}
```

---

## Configuration

All via `.env`:

```env
# Server
PORT=3856
BASIC_AUTH_USER=admin
BASIC_AUTH_PASS=password

# Buffer timeout (seconds of silence before sending)
BUFFER_TIMEOUT=25

# Claude Code API connection
CLAUDE_API_URL=https://claudecode2api.example.com
CLAUDE_API_USER=xxx
CLAUDE_API_PASS=xxx

# Working directory for Claude
WORKSPACE_DIR=/home/user/workspace

# System prompt file path
SYSTEM_PROMPT_FILE=/config/system-prompt.md

# Tool restrictions (JSON arrays)
CLAUDE_TOOLS=["Bash"]
CLAUDE_ALLOWED_TOOLS=["Bash"]
```

**Tool examples:**

```env
# Read-only
CLAUDE_TOOLS=["Read","Glob","Grep"]
CLAUDE_ALLOWED_TOOLS=["Read","Glob","Grep"]

# Git only
CLAUDE_TOOLS=["Bash"]
CLAUDE_ALLOWED_TOOLS=["Bash(git:*)"]

# MCP tools only
CLAUDE_TOOLS=[]
CLAUDE_ALLOWED_TOOLS=["mcp__myserver__search"]

# Full access (omit both → --dangerously-skip-permissions)
```

---

## Flow diagram

```
POST /v1/message ──→ buffer ──[25s silence]──→ Claude API
POST /v1/message ──→ buffer ──┘                   │
                                                   │ SSE stream
POST /v1/message ──→ pending_buffer                │
POST /v1/message ──→ pending_buffer ──┐            │
                                      │            ▼
                          [25s silence + done] ──→ Claude API
```

---

## Docker

```bash
# Start
docker compose up -d

# Logs
docker logs -f claudecode2api-buffer

# Restart after .env change
docker compose up -d
```
