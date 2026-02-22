# openclaw-lab

Lab project to run OpenClaw on an Ubuntu 24.04 LTS VPS using Gemini via API with smart routing for:

- `free key` vs `paid key`
- separate `flash` models for `free` and `paid`
- `pro` for complex tasks
- automatic fallback when the free key hits quota/rate limits

## Core Idea (Router)

Instead of calling Gemini directly, OpenClaw calls a local router service on the VPS.

Flow:

- `OpenClaw` -> `Local Router` -> `Gemini API`
- `Local Router` decides `which key` + `which model`
- `Local Router` stores state in `SQLite` (persistent on disk)

Benefits:

- lower cost (use `free + flash` whenever possible)
- better quality for complex tasks (`paid + pro`)
- survives reboots without losing cooldown/state

## Repository Contents

- `router/router.py`: local HTTP API (Python stdlib) with Gemini fallback routing
- `.env.example`: configuration template for keys and model names
- `deploy/systemd/openclaw-router.service`: `systemd` service
- `install.sh`: Ubuntu 24.04 installer (root/lab mode)
- `skills/local-llm-router/SKILL.md`: legacy example skill for local LLM routing (Ollama)
- `scripts/model_router.sh`: legacy local LLM routing helper (Ollama)

## Routing Rules (Implemented)

- `quick_chat`, `summary`, `classification`, `extraction`
  - try `gemini_free + flash` first (for example `gemini-2.5-flash`)
  - fallback: `gemini_paid + flash`
  - final fallback: `gemini_paid + pro`

- `code`, `debug`
  - default: `gemini_paid + flash`
  - fallback: `gemini_free + flash`
  - final fallback: `gemini_paid + pro`
  - can be inverted with `CODE_TASKS_USE_FREE_FIRST=true`

- `complex_reasoning`, `long_context`, `important_decision`
  - try `gemini_paid + pro`
  - fallback: `gemini_paid + flash`
  - final fallback: `gemini_free + flash`

Note:
- Portuguese task type aliases are still accepted for backward compatibility (`resumo`, `codigo`, `raciocinio_complexo`, etc.).

## Persistence (Reboot-Safe)

- Keys file: `/opt/openclaw-router/.env`
- SQLite state DB: `/var/lib/openclaw-router/state.db`
- Service: `systemd` (`openclaw-router.service`)
- Logs: `journalctl -u openclaw-router`

The router persists:

- request success/error logs
- free key quota/rate-limit errors
- free key cooldown state (to avoid repeated `429` retries)

## Step-by-Step (Ubuntu 24.04 LTS, root)

### 1) Clone this repository on the VPS

```bash
git clone <YOUR_REPO_URL> /root/openclaw-lab
cd /root/openclaw-lab
```

### 2) Run the installer

```bash
bash install.sh
```

This installs `python3`, `sqlite3`, copies the application to `/opt/openclaw-router`, and installs the `systemd` service.

### 3) Configure Gemini keys and models

```bash
nano /opt/openclaw-router/.env
```

Minimum required values:

- `GEMINI_API_KEY_FREE`
- `GEMINI_API_KEY_PAID`
- `GEMINI_MODEL_FLASH_FREE` (for example `gemini-2.5-flash`)
- `GEMINI_MODEL_FLASH_PAID` (for example `gemini-3-flash-preview`)
- `GEMINI_MODEL_PRO` (for complex tasks)

Optional tuning:

- `CODE_TASKS_USE_FREE_FIRST=false`
- `FREE_COOLDOWN_SECONDS=3600`

### 4) Start the service

```bash
systemctl start openclaw-router
systemctl status openclaw-router --no-pager
```

### 5) Health check

```bash
curl -sS http://127.0.0.1:8787/healthz
```

### 6) Test routing (no API generation)

```bash
curl -sS "http://127.0.0.1:8787/route?task_type=summary"
curl -sS "http://127.0.0.1:8787/route?task_type=complex_reasoning"
```

### 7) Test real generation

```bash
curl -sS http://127.0.0.1:8787/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "task_type": "summary",
    "prompt": "Summarize in 3 bullets why backups are important on servers.",
    "max_output_tokens": 200
  }'
```

## OpenClaw Integration

Make OpenClaw call the local router instead of calling Gemini directly.

Primary endpoint:

- `POST http://127.0.0.1:8787/generate`

Minimal payload:

```json
{
  "task_type": "code",
  "prompt": "Explain this stack trace error..."
}
```

Suggested `task_type` values:

- `quick_chat`
- `summary`
- `code`
- `debug`
- `planning`
- `complex_reasoning`
- `long_context`

## Troubleshooting

- Service does not start:
  - `journalctl -u openclaw-router -n 200 --no-pager`
- `403/401` errors:
  - check API keys and Gemini API access
- `429` on the free key:
  - expected behavior; the router puts `gemini_free` in cooldown and falls back to `paid`
- Port already in use:
  - change `ROUTER_PORT` in `.env` and restart the service

## Model Name Changes

Gemini model names change over time. The router reads `GEMINI_MODEL_FLASH_FREE`, `GEMINI_MODEL_FLASH_PAID`, and `GEMINI_MODEL_PRO` from `.env` instead of hardcoding them.
