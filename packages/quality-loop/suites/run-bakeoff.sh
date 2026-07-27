#!/usr/bin/env bash
# Off-hours model bake-off: run each candidate and incumbent through the same
# suites with the single-strategy baseline, recording rows in quality-loop's runs.db.
# Requires qloop on PATH (or QLOOP_BIN / LOOPKIT_BIN) and base URL reachable.
set -euo pipefail

SUITES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QLOOP="${QLOOP_BIN:-${LOOPKIT_BIN:-qloop}}"
read -ra SUITES <<< "${SUITES:-extraction citation-qa structured-output coding}"

# Ollama's native root, derived from the /v1 URL qloop talks to. Used only to
# ask which models are actually on disk before a multi-hour run starts.
BASE_URL="${QUALITY_LOOP_BASE_URL:-${LOOPKIT_BASE_URL:-http://mini:11434/v1}}"
OLLAMA_ROOT="${BASE_URL%/v1}"

MODELS=(
  "qwen3.6:35b-a3b-mtp-q4_K_M"
  "qwen3-coder-next:latest"
)

if [ "$#" -gt 0 ]; then
  MODELS+=("$@")
else
  # Challengers. Provisioning does not pull these (they are not in
  # ollama_base_models or ollama_heavy_models) - a missing one is skipped below.
  MODELS+=(
    "glm-4.7-flash:latest"
  )
fi

cat >&2 <<'WARN'
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
WARNING: this bake-off loads non-resident models on mini. With
OLLAMA_MAX_LOADED_MODELS=2, each non-resident model EVICTS one warm model.
Run this off-hours, not during interactive work.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
WARN

# Snapshot what is on disk once. Without this a missing challenger kills the
# whole run under `set -e` after hours of completed work.
INSTALLED="$(curl -fsS --max-time 10 "${OLLAMA_ROOT}/api/tags" 2>/dev/null || true)"
if [ -z "$INSTALLED" ]; then
  echo "WARN: could not reach ${OLLAMA_ROOT}/api/tags - skipping presence checks" >&2
fi

skipped=()
for model in "${MODELS[@]}"; do
  if [ -n "$INSTALLED" ] && ! printf '%s' "$INSTALLED" | grep -qF "\"$model\""; then
    echo "SKIP: ${model} is not on mini - pull it first: ollama pull ${model}" >&2
    skipped+=("$model")
    continue
  fi
  for suite in "${SUITES[@]}"; do
    echo "== ${suite} :: single :: ${model} ==" >&2
    "$QLOOP" eval "${SUITES_DIR}/${suite}.jsonl" --strategy single --worker "$model"
  done
done

if [ "${#skipped[@]}" -gt 0 ]; then
  echo "skipped (not installed): ${skipped[*]}" >&2
fi
echo "done - run 'qloop stats --matrix' and 'qloop summary' to compare results" >&2
