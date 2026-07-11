#!/usr/bin/env bash
# Phase 1 baseline matrix: every suite x {single, refine, best_of_n} x
# {general, coder}, always single first (never trust a loop without the
# baseline — see docs/ai-loops.md). Recorded in runs.db as each combo runs.
#
# Requires loopkit on PATH (or LOOPKIT_BIN set) and LOOPKIT_BASE_URL reachable
# (mini's Ollama endpoint over the tailnet; default http://mini:11434/v1).
set -euo pipefail

SUITES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOPKIT="${LOOPKIT_BIN:-loopkit}"
SUITES=(extraction citation-qa structured-output coding)
STRATEGIES=(single refine best_of_n)
WORKERS=(general coder)

for suite in "${SUITES[@]}"; do
  for worker in "${WORKERS[@]}"; do
    for strategy in "${STRATEGIES[@]}"; do
      echo "== ${suite} :: ${strategy} :: ${worker} ==" >&2
      "$LOOPKIT" eval "${SUITES_DIR}/${suite}.jsonl" --strategy "$strategy" --worker "$worker"
    done
  done
done

echo "done — run 'loopkit stats --matrix' or 'loopkit summary' to view results" >&2
