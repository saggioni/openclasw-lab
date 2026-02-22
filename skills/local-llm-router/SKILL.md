---
name: local-llm-router
description: Selects the best local LLM model (for example Ollama) for each OpenClaw task using task-type heuristics, latency/cost tradeoffs, and fallback when a model is unavailable.
---

# Local LLM Router

Use this skill when the user wants to run local models and automatically choose the best model for each situation.

## Goal

Select a local model per task with focus on:

- acceptable latency
- enough quality for the task type
- automatic fallback

## Workflow

1. Identify the main task type:
   - `quick_chat`
   - `summary`
   - `code`
   - `debug`
   - `planning`
   - `complex_reasoning`
   - `long_context`
2. Check locally installed models (`ollama list`)
3. Choose the preferred model for the task type
4. Apply fallback if unavailable
5. Report the decision to the user (model + reason)

## Routing Map (Initial Heuristic)

- `quick_chat`, `summary` -> `qwen2.5:3b`
- `code`, `debug` -> `qwen2.5-coder:7b`
- `planning` -> `qwen2.5:7b`
- `complex_reasoning`, `long_context` -> `qwen2.5:14b`

## Suggested Fallback

If the preferred model is not available, try in this order:

1. `qwen2.5:7b`
2. `qwen2.5:3b`
3. any model available in `ollama list`

## Practical Rules

- If the VPS is weak (<= 8 GB RAM), prefer `3b` and `7b` models
- For iterative coding tasks, prioritize latency (smaller coder model)
- For important final answers, consider rerunning with a stronger model
- Always state when a fallback happened

## Useful Commands

```bash
ollama list
ollama show qwen2.5:7b
ollama run qwen2.5:3b "test"
```

## Local Implementation

If the repository contains `scripts/model_router.sh`, use it to get a model suggestion before calling Ollama.
