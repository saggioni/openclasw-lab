#!/usr/bin/env bash
set -euo pipefail

TASK_TYPE="${1:-planejamento}"

preferred_model() {
  case "$1" in
    chat_rapido|resumo) echo "qwen2.5:3b" ;;
    codigo|debug) echo "qwen2.5-coder:7b" ;;
    planejamento) echo "qwen2.5:7b" ;;
    raciocinio_complexo|contexto_longo) echo "qwen2.5:14b" ;;
    *) echo "qwen2.5:7b" ;;
  esac
}

has_ollama() {
  command -v ollama >/dev/null 2>&1
}

model_installed() {
  local model="$1"
  ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Fxq "$model"
}

choose_available_model() {
  local preferred="$1"
  local fallback

  if has_ollama && model_installed "$preferred"; then
    echo "$preferred"
    return 0
  fi

  for fallback in qwen2.5:7b qwen2.5:3b; do
    if has_ollama && model_installed "$fallback"; then
      echo "$fallback"
      return 0
    fi
  done

  if has_ollama; then
    fallback="$(ollama list 2>/dev/null | awk 'NR==2 {print $1}')"
    if [[ -n "$fallback" ]]; then
      echo "$fallback"
      return 0
    fi
  fi

  # Default safe suggestion when ollama is not installed yet.
  echo "$preferred"
}

PREFERRED="$(preferred_model "$TASK_TYPE")"
CHOSEN="$(choose_available_model "$PREFERRED")"

if [[ "$CHOSEN" == "$PREFERRED" ]]; then
  REASON="preferred"
else
  REASON="fallback"
fi

printf 'task_type=%s\npreferred=%s\nchosen=%s\nreason=%s\n' \
  "$TASK_TYPE" "$PREFERRED" "$CHOSEN" "$REASON"
