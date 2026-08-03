#!/usr/bin/env bash
# Phase 1 baseline matrix: every suite x {single, refine} x
# {general, coder}, always single first (never trust a loop without the
# baseline — see docs/ai-loops.md). Recorded in runs.db as each combo runs.
#
# Requires qloop on PATH (or QLOOP_BIN / LOOPKIT_BIN set) and
# QUALITY_LOOP_BASE_URL (or LOOPKIT_BASE_URL) reachable.
set -euo pipefail

SUITES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QLOOP="${QLOOP_BIN:-${LOOPKIT_BIN:-qloop}}"
read -ra SUITES <<< "${SUITES:-extraction citation-qa structured-output coding}"
read -ra STRATEGIES <<< "${STRATEGIES:-single refine}"
read -ra WORKERS <<< "${WORKERS:-general coder}"

for suite in "${SUITES[@]}"; do
  for worker in "${WORKERS[@]}"; do
    for strategy in "${STRATEGIES[@]}"; do
      echo "== ${suite} :: ${strategy} :: ${worker} ==" >&2
      "$QLOOP" eval "${SUITES_DIR}/${suite}.jsonl" --strategy "$strategy" --worker "$worker"
    done
  done
done

echo "done — run 'qloop stats --matrix' or 'qloop summary' to view results" >&2
