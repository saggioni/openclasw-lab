# openclaw-lab

Laboratório para rodar OpenClaw em uma VPS Ubuntu 24.04 LTS usando Gemini via API com roteamento inteligente de:

- `key gratuita` vs `key paga`
- `flash` separado para `free` e `paid`, além de `pro` para tarefas complexas
- fallback automático quando a key gratuita bater limite/cota

## Ideia central (roteador)

Em vez de o OpenClaw chamar a API Gemini diretamente, ele chama um serviço local (`router`) na VPS.

Fluxo:

- `OpenClaw` -> `Router local` -> `Gemini API`
- `Router local` decide `qual key` + `qual modelo`
- `Router local` registra estado em `SQLite` (persistente em disco)

Isso permite:

- economizar custo (usar `free + flash` quando possível)
- usar `paid + pro` em tarefas complexas
- sobreviver a reboot sem perder cooldown/estado

## O que existe neste repositório

- `router/router.py`: API local HTTP (Python stdlib) com fallback Gemini
- `.env.example`: configuração das duas keys e modelos
- `deploy/systemd/openclaw-router.service`: serviço `systemd`
- `install.sh`: instalador para Ubuntu 24.04 (rodando como `root`)
- `skills/local-llm-router/SKILL.md`: skill antiga de exemplo para LLM local (Ollama)
- `scripts/model_router.sh`: script antigo de roteamento local (Ollama)

## Regras de roteamento (implementadas)

- `chat_rapido`, `resumo`, `classificacao`, `extracao`
  - tenta `gemini_free + flash` (ex.: `gemini-2.5-flash`)
  - fallback: `gemini_paid + flash`
  - fallback final: `gemini_paid + pro`

- `codigo`, `debug`
  - padrão: `gemini_paid + flash`
  - fallback: `gemini_free + flash`
  - fallback final: `gemini_paid + pro`
  - pode inverter via `CODE_TASKS_USE_FREE_FIRST=true`

- `raciocinio_complexo`, `contexto_longo`, `decisao_importante`
  - tenta `gemini_paid + pro`
  - fallback: `gemini_paid + flash`
  - fallback final: `gemini_free + flash`

## Persistência (para não perder nada no reboot)

- `.env` com chaves: `/opt/openclaw-router/.env`
- banco SQLite: `/var/lib/openclaw-router/state.db`
- serviço: `systemd` (`openclaw-router.service`)
- logs: `journalctl -u openclaw-router`

O router salva:

- logs de tentativa/sucesso/erro
- erro de cota da key gratuita
- cooldown temporário da key gratuita (para evitar insistir após `429`)

## Passo a passo (Ubuntu 24.04 LTS, usando `root`)

### 1) Clonar este repositório na VPS

```bash
git clone <SEU_REPO> /root/openclaw-lab
cd /root/openclaw-lab
```

### 2) Rodar o instalador

```bash
bash install.sh
```

Isso instala `python3`, `sqlite3`, copia a aplicação para `/opt/openclaw-router` e instala o serviço `systemd`.

### 3) Configurar as duas keys e modelos Gemini

```bash
nano /opt/openclaw-router/.env
```

Preencher no mínimo:

- `GEMINI_API_KEY_FREE`
- `GEMINI_API_KEY_PAID`
- `GEMINI_MODEL_FLASH_FREE` (ex.: `gemini-2.5-flash`)
- `GEMINI_MODEL_FLASH_PAID` (ex.: `gemini-3-flash-preview`)
- `GEMINI_MODEL_PRO` (use o pro para tarefas complexas)

Ajuste opcional:

- `CODE_TASKS_USE_FREE_FIRST=false`
- `FREE_COOLDOWN_SECONDS=3600`

### 4) Subir o serviço

```bash
systemctl start openclaw-router
systemctl status openclaw-router --no-pager
```

### 5) Testar saúde do serviço

```bash
curl -sS http://127.0.0.1:8787/healthz
```

### 6) Testar roteamento (sem prompt grande)

```bash
curl -sS "http://127.0.0.1:8787/route?task_type=resumo"
curl -sS "http://127.0.0.1:8787/route?task_type=raciocinio_complexo"
```

### 7) Testar geração real

```bash
curl -sS http://127.0.0.1:8787/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "task_type": "resumo",
    "prompt": "Resuma em 3 bullets: a importância de backups em servidores.",
    "max_output_tokens": 200
  }'
```

## Integração com OpenClaw

Faça o OpenClaw chamar o router local ao invés de chamar Gemini direto.

Endpoint principal:

- `POST http://127.0.0.1:8787/generate`

Payload mínimo:

```json
{
  "task_type": "codigo",
  "prompt": "Explique este erro de stack trace..."
}
```

`task_type` sugeridos:

- `chat_rapido`
- `resumo`
- `codigo`
- `debug`
- `planejamento`
- `raciocinio_complexo`
- `contexto_longo`

## Troubleshooting rápido

- Serviço não sobe:
  - `journalctl -u openclaw-router -n 200 --no-pager`
- Erro `403/401`:
  - revisar keys e permissões da API
- Erro `429` na key gratuita:
  - esperado; router coloca `gemini_free` em cooldown e usa `paid`
- Porta ocupada:
  - mudar `ROUTER_PORT` no `.env` e reiniciar o serviço

## Observação sobre o modelo “mais atual”

Os nomes de modelos Gemini mudam com o tempo. Por isso o router lê `GEMINI_MODEL_FLASH_FREE`, `GEMINI_MODEL_FLASH_PAID` e `GEMINI_MODEL_PRO` do `.env`, em vez de fixar isso no código.
