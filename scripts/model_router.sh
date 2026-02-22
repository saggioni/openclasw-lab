#!/usr/bin/env bash
set -euo pipefail

TASK_TYPE="${1:-planning}"

normalize_task_type() {
  case "$1" in
    chat_rapido|chat) echo "quick_chat" ;;
    resumo) echo "summary" ;;
    codigo|coding) echo "code" ;;
    planejamento|plan) echo "planning" ;;
    raciocinio_complexo) echo "complex_reasoning" ;;
    contexto_longo) echo "long_context" ;;
    *) echo "$1" ;;
  esac
}

preferred_model() {
  case "$1" in
    quick_chat|summary) echo "qwen2.5:3b" ;;
    code|debug) echo "qwen2.5-coder:7b" ;;
    planning) echo "qwen2.5:7b" ;;
    complex_reasoning|long_context) echo "qwen2.5:14b" ;;
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

  # Safe default suggestion when Ollama is not installed yet.
  echo "$preferred"
}

TASK_TYPE="$(normalize_task_type "$TASK_TYPE")"
PREFERRED="$(preferred_model "$TASK_TYPE")"
CHOSEN="$(choose_available_model "$PREFERRED")"

if [[ "$CHOSEN" == "$PREFERRED" ]]; then
  REASON="preferred"
else
  REASON="fallback"
fi

printf 'task_type=%s\npreferred=%s\nchosen=%s\nreason=%s\n' \
  "$TASK_TYPE" "$PREFERRED" "$CHOSEN" "$REASON"
