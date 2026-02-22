---
name: local-llm-router
description: Escolhe o melhor modelo LLM local (ex: Ollama) para cada tarefa no OpenClaw com heurísticas de tipo de tarefa, custo de latência e fallback quando um modelo não estiver disponível.
---

# Local LLM Router

Use esta skill quando o usuário quiser rodar modelos locais e escolher automaticamente o melhor modelo para cada situação.

## Objetivo

Selecionar um modelo local por tarefa com foco em:

- menor latência aceitável
- qualidade suficiente para o tipo de tarefa
- fallback automático

## Workflow

1. Identificar o tipo principal da tarefa:
   - `chat_rapido`
   - `resumo`
   - `codigo`
   - `debug`
   - `planejamento`
   - `raciocinio_complexo`
   - `contexto_longo`
2. Verificar modelos instalados localmente (`ollama list`)
3. Escolher modelo preferido por tipo
4. Aplicar fallback se indisponível
5. Informar decisão ao usuário (modelo + motivo)

## Mapa de roteamento (heurística inicial)

- `chat_rapido`, `resumo` -> `qwen2.5:3b`
- `codigo`, `debug` -> `qwen2.5-coder:7b`
- `planejamento` -> `qwen2.5:7b`
- `raciocinio_complexo`, `contexto_longo` -> `qwen2.5:14b`

## Fallback sugerido

Se o modelo preferido não existir, tentar nesta ordem:

1. `qwen2.5:7b`
2. `qwen2.5:3b`
3. qualquer modelo disponível no `ollama list`

## Regras práticas

- Se a VPS for fraca (<= 8 GB RAM), preferir modelos `3b` e `7b`
- Para tarefas iterativas de coding, priorizar latência (modelo coder menor)
- Para resposta final importante, considerar reexecutar em modelo mais forte
- Sempre explicitar quando houve fallback

## Comandos úteis

```bash
ollama list
ollama show qwen2.5:7b
ollama run qwen2.5:3b "teste"
```

## Implementação local

Se o repositório tiver `scripts/model_router.sh`, use-o para obter a sugestão de modelo antes de invocar o Ollama.

