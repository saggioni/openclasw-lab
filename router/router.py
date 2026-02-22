#!/usr/bin/env python3
import json
import logging
import os
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


CONFIG = {
    "host": env_str("ROUTER_HOST", "127.0.0.1"),
    "port": env_int("ROUTER_PORT", 8787),
    "db_path": env_str("ROUTER_DB_PATH", "/var/lib/openclaw-router/state.db"),
    "free_cooldown_seconds": env_int("FREE_COOLDOWN_SECONDS", 3600),
    "http_timeout_seconds": env_int("HTTP_TIMEOUT_SECONDS", 60),
    "gemini_api_base_url": env_str(
        "GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/"),
    "gemini_api_key_free": env_str("GEMINI_API_KEY_FREE"),
    "gemini_api_key_paid": env_str("GEMINI_API_KEY_PAID"),
    # Backward compatibility: if only GEMINI_MODEL_FLASH is set, use it for both free and paid.
    "gemini_model_flash_legacy": env_str("GEMINI_MODEL_FLASH", ""),
    "gemini_model_flash_free": env_str("GEMINI_MODEL_FLASH_FREE", ""),
    "gemini_model_flash_paid": env_str("GEMINI_MODEL_FLASH_PAID", ""),
    "gemini_model_pro": env_str("GEMINI_MODEL_PRO", "gemini-2.5-pro"),
    "code_tasks_use_free_first": env_bool("CODE_TASKS_USE_FREE_FIRST", False),
    "log_level": env_str("LOG_LEVEL", "INFO").upper(),
}

if not CONFIG["gemini_model_flash_free"]:
    CONFIG["gemini_model_flash_free"] = (
        CONFIG["gemini_model_flash_legacy"] or "gemini-2.5-flash"
    )
if not CONFIG["gemini_model_flash_paid"]:
    CONFIG["gemini_model_flash_paid"] = (
        CONFIG["gemini_model_flash_legacy"] or "gemini-3-flash-preview"
    )

logging.basicConfig(
    level=getattr(logging, CONFIG["log_level"], logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("openclaw-router")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS provider_state (
  provider_key TEXT PRIMARY KEY,
  cooldown_until INTEGER,
  last_error_code INTEGER,
  last_error_message TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS request_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT,
  task_type TEXT,
  provider_key TEXT,
  model TEXT,
  status TEXT NOT NULL,
  http_status INTEGER,
  latency_ms INTEGER,
  error_category TEXT,
  error_message TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_request_log_created_at ON request_log(created_at);
"""


@dataclass(frozen=True)
class RouteCandidate:
    provider_key: str  # gemini_free or gemini_paid
    model_tier: str  # flash or pro

    @property
    def api_key(self) -> str:
        if self.provider_key == "gemini_free":
            return CONFIG["gemini_api_key_free"]
        if self.provider_key == "gemini_paid":
            return CONFIG["gemini_api_key_paid"]
        return ""

    @property
    def model_name(self) -> str:
        if self.model_tier == "flash":
            if self.provider_key == "gemini_free":
                return CONFIG["gemini_model_flash_free"]
            return CONFIG["gemini_model_flash_paid"]
        if self.model_tier == "pro":
            return CONFIG["gemini_model_pro"]
        if self.provider_key == "gemini_free":
            return CONFIG["gemini_model_flash_free"]
        return CONFIG["gemini_model_flash_paid"]


def utc_now_ts() -> int:
    return int(time.time())


def iso_utc(ts: Optional[int] = None) -> str:
    return datetime.fromtimestamp(ts or utc_now_ts(), tz=timezone.utc).isoformat()


class StateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_parent_dir()
        self._init_schema()

    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def get_provider_state(self, provider_key: str) -> Dict[str, Optional[object]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT provider_key, cooldown_until, last_error_code, last_error_message, updated_at "
                "FROM provider_state WHERE provider_key = ?",
                (provider_key,),
            ).fetchone()
        if not row:
            return {
                "provider_key": provider_key,
                "cooldown_until": None,
                "last_error_code": None,
                "last_error_message": None,
                "updated_at": None,
            }
        return dict(row)

    def set_provider_state(
        self,
        provider_key: str,
        cooldown_until: Optional[int],
        last_error_code: Optional[int],
        last_error_message: Optional[str],
    ) -> None:
        now = utc_now_ts()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_state(provider_key, cooldown_until, last_error_code, last_error_message, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_key) DO UPDATE SET
                  cooldown_until = excluded.cooldown_until,
                  last_error_code = excluded.last_error_code,
                  last_error_message = excluded.last_error_message,
                  updated_at = excluded.updated_at
                """,
                (provider_key, cooldown_until, last_error_code, last_error_message, now),
            )
            conn.commit()

    def clear_provider_error(self, provider_key: str) -> None:
        state = self.get_provider_state(provider_key)
        self.set_provider_state(
            provider_key=provider_key,
            cooldown_until=state.get("cooldown_until"),
            last_error_code=None,
            last_error_message=None,
        )

    def set_provider_cooldown(self, provider_key: str, seconds: int, error_code: int, error_message: str) -> int:
        until_ts = utc_now_ts() + max(1, seconds)
        self.set_provider_state(provider_key, until_ts, error_code, error_message[:500])
        return until_ts

    def log_request(
        self,
        request_id: str,
        task_type: str,
        provider_key: Optional[str],
        model: Optional[str],
        status: str,
        http_status: Optional[int],
        latency_ms: Optional[int],
        error_category: Optional[str],
        error_message: Optional[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO request_log(
                  request_id, task_type, provider_key, model, status, http_status,
                  latency_ms, error_category, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    task_type,
                    provider_key,
                    model,
                    status,
                    http_status,
                    latency_ms,
                    error_category,
                    (error_message or "")[:1000],
                    utc_now_ts(),
                ),
            )
            conn.commit()


STORE = StateStore(CONFIG["db_path"])


def normalize_task_type(task_type: Optional[str]) -> str:
    value = (task_type or "chat_rapido").strip().lower()
    aliases = {
        "chat": "chat_rapido",
        "quick_chat": "chat_rapido",
        "summary": "resumo",
        "code": "codigo",
        "coding": "codigo",
        "bugfix": "debug",
        "plan": "planejamento",
        "complex": "raciocinio_complexo",
        "long_context": "contexto_longo",
    }
    return aliases.get(value, value)


def build_candidate_chain(task_type: str) -> List[RouteCandidate]:
    code_free_first = CONFIG["code_tasks_use_free_first"]

    if task_type in {"chat_rapido", "resumo", "classificacao", "extracao"}:
        chain = [
            RouteCandidate("gemini_free", "flash"),
            RouteCandidate("gemini_paid", "flash"),
            RouteCandidate("gemini_paid", "pro"),
        ]
    elif task_type in {"codigo", "debug"}:
        if code_free_first:
            chain = [
                RouteCandidate("gemini_free", "flash"),
                RouteCandidate("gemini_paid", "flash"),
                RouteCandidate("gemini_paid", "pro"),
            ]
        else:
            chain = [
                RouteCandidate("gemini_paid", "flash"),
                RouteCandidate("gemini_free", "flash"),
                RouteCandidate("gemini_paid", "pro"),
            ]
    elif task_type in {"raciocinio_complexo", "contexto_longo", "decisao_importante"}:
        chain = [
            RouteCandidate("gemini_paid", "pro"),
            RouteCandidate("gemini_paid", "flash"),
            RouteCandidate("gemini_free", "flash"),
        ]
    else:
        chain = [
            RouteCandidate("gemini_free", "flash"),
            RouteCandidate("gemini_paid", "flash"),
            RouteCandidate("gemini_paid", "pro"),
        ]

    # Remove duplicates while preserving order.
    deduped: List[RouteCandidate] = []
    seen = set()
    for c in chain:
        key = (c.provider_key, c.model_tier)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def is_provider_in_cooldown(provider_key: str) -> Tuple[bool, Optional[int]]:
    state = STORE.get_provider_state(provider_key)
    cooldown_until = state.get("cooldown_until")
    if cooldown_until and int(cooldown_until) > utc_now_ts():
        return True, int(cooldown_until)
    return False, cooldown_until if cooldown_until else None


def choose_candidates(task_type: str) -> Tuple[List[RouteCandidate], List[str]]:
    reasons: List[str] = []
    result: List[RouteCandidate] = []
    for candidate in build_candidate_chain(task_type):
        if not candidate.api_key:
            reasons.append(f"skip {candidate.provider_key}/{candidate.model_tier}: sem key")
            continue
        in_cd, until = is_provider_in_cooldown(candidate.provider_key)
        if in_cd:
            reasons.append(
                f"skip {candidate.provider_key}/{candidate.model_tier}: cooldown ate {iso_utc(until)}"
            )
            continue
        result.append(candidate)
    return result, reasons


def classify_api_error(http_status: int, error_obj: Optional[dict]) -> str:
    msg = ""
    status_name = ""
    if isinstance(error_obj, dict):
        msg = str(error_obj.get("message") or "").lower()
        status_name = str(error_obj.get("status") or "").lower()

    if http_status == 429:
        return "quota_or_rate_limit"
    if "quota" in msg or "rate" in msg or "resource_exhausted" in status_name:
        return "quota_or_rate_limit"
    if 500 <= http_status <= 599:
        return "server_error"
    if http_status in {401, 403}:
        return "auth_or_permission"
    return "api_error"


def extract_text_from_gemini(response_data: dict) -> str:
    candidates = response_data.get("candidates") or []
    texts: List[str] = []
    for candidate in candidates:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text")
            if text:
                texts.append(text)
    return "\n".join(texts).strip()


def gemini_generate(candidate: RouteCandidate, prompt: str, system: str = "", temperature: Optional[float] = None,
                    max_output_tokens: Optional[int] = None) -> Tuple[int, dict]:
    api_key = candidate.api_key
    if not api_key:
        raise RuntimeError(f"API key ausente para {candidate.provider_key}")

    combined_prompt = prompt
    if system.strip():
        combined_prompt = f"[SYSTEM]\n{system.strip()}\n\n[USER]\n{prompt.strip()}"

    payload: Dict[str, object] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": combined_prompt}],
            }
        ]
    }

    generation_config: Dict[str, object] = {}
    if temperature is not None:
        generation_config["temperature"] = temperature
    if max_output_tokens is not None:
        generation_config["maxOutputTokens"] = max_output_tokens
    if generation_config:
        payload["generationConfig"] = generation_config

    body = json.dumps(payload).encode("utf-8")
    url = (
        f"{CONFIG['gemini_api_base_url']}/models/{candidate.model_name}:generateContent"
        f"?key={api_key}"
    )
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    timeout = CONFIG["http_timeout_seconds"]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return resp.getcode(), data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"error": {"message": raw}}
        return exc.code, data


def attempt_generation(request_id: str, task_type: str, prompt: str, system: str, temperature: Optional[float],
                       max_output_tokens: Optional[int]) -> dict:
    candidates, precheck_reasons = choose_candidates(task_type)
    if not candidates:
        raise RuntimeError(
            "Nenhum candidato disponível. Verifique GEMINI_API_KEY_FREE/GEMINI_API_KEY_PAID e cooldown." 
            + (" Prechecks: " + "; ".join(precheck_reasons) if precheck_reasons else "")
        )

    errors = []
    for candidate in candidates:
        start = time.perf_counter()
        http_status = None
        logger.info(
            "request_id=%s task_type=%s trying=%s/%s model=%s",
            request_id,
            task_type,
            candidate.provider_key,
            candidate.model_tier,
            candidate.model_name,
        )
        try:
            http_status, data = gemini_generate(
                candidate,
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            if 200 <= http_status < 300:
                text = extract_text_from_gemini(data)
                if not text:
                    STORE.log_request(
                        request_id,
                        task_type,
                        candidate.provider_key,
                        candidate.model_name,
                        "error",
                        http_status,
                        latency_ms,
                        "empty_response",
                        "Gemini retornou sem texto",
                    )
                    errors.append(
                        {
                            "provider": candidate.provider_key,
                            "model": candidate.model_name,
                            "http_status": http_status,
                            "category": "empty_response",
                            "message": "Gemini retornou sem texto",
                        }
                    )
                    continue

                if candidate.provider_key == "gemini_free":
                    # Limpa erro anterior sem mexer em cooldown ainda vigente (se existir no passado, ok).
                    STORE.clear_provider_error("gemini_free")
                elif candidate.provider_key == "gemini_paid":
                    STORE.clear_provider_error("gemini_paid")

                STORE.log_request(
                    request_id,
                    task_type,
                    candidate.provider_key,
                    candidate.model_name,
                    "success",
                    http_status,
                    latency_ms,
                    None,
                    None,
                )
                return {
                    "request_id": request_id,
                    "ok": True,
                    "task_type": task_type,
                    "text": text,
                    "route": {
                        "provider_key": candidate.provider_key,
                        "model_tier": candidate.model_tier,
                        "model_name": candidate.model_name,
                        "fallback_used": len(errors) > 0,
                        "precheck_skips": precheck_reasons,
                    },
                    "meta": {
                        "http_status": http_status,
                        "latency_ms": latency_ms,
                        "timestamp_utc": iso_utc(),
                    },
                }

            error_obj = data.get("error") if isinstance(data, dict) else None
            category = classify_api_error(http_status, error_obj)
            message = (
                (error_obj or {}).get("message")
                if isinstance(error_obj, dict)
                else f"Erro HTTP {http_status}"
            ) or f"Erro HTTP {http_status}"

            if candidate.provider_key == "gemini_free" and category == "quota_or_rate_limit":
                until = STORE.set_provider_cooldown(
                    "gemini_free",
                    CONFIG["free_cooldown_seconds"],
                    http_status or 429,
                    message,
                )
                message = f"{message} (cooldown free ate {iso_utc(until)})"
            else:
                STORE.set_provider_state(candidate.provider_key, None, http_status, str(message))

            STORE.log_request(
                request_id,
                task_type,
                candidate.provider_key,
                candidate.model_name,
                "error",
                http_status,
                int((time.perf_counter() - start) * 1000),
                category,
                str(message),
            )
            errors.append(
                {
                    "provider": candidate.provider_key,
                    "model": candidate.model_name,
                    "http_status": http_status,
                    "category": category,
                    "message": str(message),
                }
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - start) * 1000)
            STORE.log_request(
                request_id,
                task_type,
                candidate.provider_key,
                candidate.model_name,
                "error",
                http_status,
                latency_ms,
                "client_exception",
                str(exc),
            )
            errors.append(
                {
                    "provider": candidate.provider_key,
                    "model": candidate.model_name,
                    "http_status": http_status,
                    "category": "client_exception",
                    "message": str(exc),
                }
            )

    return {
        "request_id": request_id,
        "ok": False,
        "task_type": task_type,
        "error": "Todos os candidatos falharam",
        "attempts": errors,
        "precheck_skips": precheck_reasons,
        "timestamp_utc": iso_utc(),
    }


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "OpenClawGeminiRouter/0.1"

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        logger.info("http %s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            free_state = STORE.get_provider_state("gemini_free")
            paid_state = STORE.get_provider_state("gemini_paid")
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "openclaw-gemini-router",
                    "timestamp_utc": iso_utc(),
                    "config": {
                        "host": CONFIG["host"],
                        "port": CONFIG["port"],
                        "db_path": CONFIG["db_path"],
                        "gemini_model_flash_free": CONFIG["gemini_model_flash_free"],
                        "gemini_model_flash_paid": CONFIG["gemini_model_flash_paid"],
                        "gemini_model_pro": CONFIG["gemini_model_pro"],
                        "has_free_key": bool(CONFIG["gemini_api_key_free"]),
                        "has_paid_key": bool(CONFIG["gemini_api_key_paid"]),
                    },
                    "provider_state": {
                        "gemini_free": free_state,
                        "gemini_paid": paid_state,
                    },
                },
            )
            return

        if self.path.startswith("/route?"):
            try:
                query = self.path.split("?", 1)[1]
                params = {}
                for pair in query.split("&"):
                    if not pair:
                        continue
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                    else:
                        k, v = pair, ""
                    params[k] = urllib.parse.unquote_plus(v)
                task_type = normalize_task_type(params.get("task_type"))
                candidates, reasons = choose_candidates(task_type)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "task_type": task_type,
                        "candidates": [
                            {
                                "provider_key": c.provider_key,
                                "model_tier": c.model_tier,
                                "model_name": c.model_name,
                            }
                            for c in candidates
                        ],
                        "precheck_skips": reasons,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/generate":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        request_id = f"req-{utc_now_ts()}-{int(time.time_ns() % 1_000_000)}"
        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "json_invalido"})
            return

        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            self._send_json(400, {"ok": False, "error": "campo 'prompt' é obrigatório"})
            return

        task_type = normalize_task_type(payload.get("task_type"))
        system = str(payload.get("system") or "")

        temperature = payload.get("temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": "temperature inválido"})
                return

        max_output_tokens = payload.get("max_output_tokens")
        if max_output_tokens is not None:
            try:
                max_output_tokens = int(max_output_tokens)
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": "max_output_tokens inválido"})
                return

        try:
            result = attempt_generation(
                request_id=request_id,
                task_type=task_type,
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            status_code = 200 if result.get("ok") else 502
            self._send_json(status_code, result)
        except Exception as exc:  # noqa: BLE001
            logger.error("request_id=%s fatal_error=%s", request_id, exc)
            logger.debug(traceback.format_exc())
            STORE.log_request(
                request_id,
                task_type,
                None,
                None,
                "error",
                None,
                None,
                "router_exception",
                str(exc),
            )
            self._send_json(
                500,
                {
                    "ok": False,
                    "request_id": request_id,
                    "error": "router_exception",
                    "message": str(exc),
                    "timestamp_utc": iso_utc(),
                },
            )


def validate_boot_config() -> List[str]:
    warnings = []
    if not CONFIG["gemini_api_key_free"] and not CONFIG["gemini_api_key_paid"]:
        warnings.append("Nenhuma key Gemini configurada (free/paid).")
    if not CONFIG["gemini_model_flash_free"]:
        warnings.append("GEMINI_MODEL_FLASH_FREE vazio.")
    if not CONFIG["gemini_model_flash_paid"]:
        warnings.append("GEMINI_MODEL_FLASH_PAID vazio.")
    if not CONFIG["gemini_model_pro"]:
        warnings.append("GEMINI_MODEL_PRO vazio.")
    return warnings


def main() -> int:
    warnings = validate_boot_config()
    for w in warnings:
        logger.warning(w)

    server = ThreadingHTTPServer((CONFIG["host"], CONFIG["port"]), RouterHandler)
    logger.info(
        "Router iniciado em http://%s:%s (db=%s)",
        CONFIG["host"],
        CONFIG["port"],
        CONFIG["db_path"],
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando por KeyboardInterrupt")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
