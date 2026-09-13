#!/usr/bin/env bash
# Unattended halogen-flash bring-up on mini (Strix Halo / gfx1151 / 122 Gi).
# Replaces llama-quality on :8090. Idempotent. Does NOT delete the Unsloth XL
# GGUF — that stays on disk as the rollback.
#
# Weights: peonist-ai/halogen-qwen3.8-flash-next (~118 GiB of .hgn).
# Image:   ghcr.io/peonist-ai/halogen-flash-server:0.5.8
set -euo pipefail
export HOME="${HOME:-/home/blewis}"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"
export HF_HOME="${HF_HOME:-/data/models/huggingface}"
export HF_HUB_ENABLE_HF_TRANSFER=1
export HF_XET_HIGH_PERFORMANCE=1

IMAGE="ghcr.io/peonist-ai/halogen-flash-server:0.5.8"
HF_REPO="peonist-ai/halogen-qwen3.8-flash-next"
MODELS_DIR="/data/models/halogen-qwen3.8-flash-next"
CHECKPOINT="${MODELS_DIR}/qwen38-flash-next-w4b.hgn"
OVERLAY="${MODELS_DIR}/qwen38-flash-next-w4b.overlay.hgn"
TOKENIZER="${MODELS_DIR}/tokenizer/tokenizer.json"
CHECKPOINT_BYTES=124068083904
OVERLAY_BYTES=2477677120
ALIAS="qwen3.8-flash-next"
PORT=8090
ENGINE_PORT=8730
QUADLET_DIR="${HOME}/.config/containers/systemd"
QUADLET="${QUADLET_DIR}/halogen-flash.container"
LLAMA_QUALITY="${QUADLET_DIR}/llama-quality.container"
LLAMA_DEEP="${QUADLET_DIR}/llama-deep.container"
LOG="/tmp/halogen-setup.log"
STATE="${HOME}/.local/state/halogen-setup.json"
LOCK="${HOME}/.local/state/halogen-setup.lock"

mkdir -p "$(dirname "$STATE")" "$MODELS_DIR" "$QUADLET_DIR"
exec > >(tee -a "$LOG") 2>&1
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) halogen-setup start pid=$$ ====="

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another halogen-setup is running; exiting"
  exit 0
fi

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "missing $1"; exit 1; }; }
need_cmd curl
need_cmd python3
need_cmd podman
need_cmd systemctl
need_cmd hf

file_size() {
  python3 - "$1" <<'PY'
import os, sys
p = sys.argv[1]
print(os.path.getsize(p) if os.path.isfile(p) else 0)
PY
}

weights_ready() {
  [[ "$(file_size "$CHECKPOINT")" -eq "$CHECKPOINT_BYTES" ]] || return 1
  [[ "$(file_size "$OVERLAY")" -eq "$OVERLAY_BYTES" ]] || return 1
  [[ -f "$TOKENIZER" ]] || return 1
  return 0
}

download_weights() {
  if weights_ready; then
    echo "weights ready at $MODELS_DIR"
    return 0
  fi
  echo "downloading ${HF_REPO} -> ${MODELS_DIR} (resumes if interrupted)"
  echo "have checkpoint=$(file_size "$CHECKPOINT") overlay=$(file_size "$OVERLAY") expected ck=$CHECKPOINT_BYTES ov=$OVERLAY_BYTES"
  hf download "$HF_REPO" --local-dir "$MODELS_DIR"
  if ! weights_ready; then
    echo "download finished but sizes do not match:"
    echo "  checkpoint $(file_size "$CHECKPOINT") want $CHECKPOINT_BYTES"
    echo "  overlay    $(file_size "$OVERLAY") want $OVERLAY_BYTES"
    echo "  tokenizer  $TOKENIZER exists=$([[ -f $TOKENIZER ]] && echo yes || echo no)"
    return 1
  fi
  echo "weights ready at $MODELS_DIR"
}

pull_image() {
  if podman image exists "$IMAGE"; then
    echo "image present: $IMAGE"
    return 0
  fi
  echo "pulling $IMAGE"
  podman pull "$IMAGE"
  podman image exists "$IMAGE"
}

write_quadlet() {
  cat > "$QUADLET" <<EOF
# Managed by halogen-setup.sh — Qwen3.8-Flash-Next halogen-flash on :${PORT}.
# Do not restore llama-quality alongside this; halogen holds most of 122 Gi.
[Unit]
Description=halogen-flash [quality] ${ALIAS} on :${PORT}
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=halogen-flash
Image=${IMAGE}
Network=host
AddDevice=/dev/dri
AddDevice=/dev/kfd
PodmanArgs=--security-opt seccomp=unconfined --ipc=host --ulimit memlock=-1:-1
Volume=${MODELS_DIR}:/models:ro
Environment=HALOGEN_API_PORT=${PORT}
Environment=HALOGEN_PORT=${ENGINE_PORT}
Environment=HALOGEN_MODEL_ID=${ALIAS}
Environment=HALOGEN_CHECKPOINT=/models/qwen38-flash-next-w4b.hgn
Environment=HALOGEN_VISION_TOWER=1

HealthCmd=/usr/local/bin/halogen-healthcheck api
HealthInterval=30s
HealthStartPeriod=1800s
HealthTimeout=35s
HealthRetries=3

[Service]
Restart=on-failure
RestartSec=10
TimeoutStartSec=1800
LimitMEMLOCK=infinity

[Install]
WantedBy=default.target
EOF
}

retire_llama() {
  echo "stopping llama.cpp so halogen can take :${PORT} and 122 Gi"
  systemctl --user stop llama-servers.target llama-quality llama-deep 2>/dev/null || true
  systemctl --user disable llama-quality.service llama-deep.service llama-servers.target 2>/dev/null || true
  for i in $(seq 1 30); do
    if ! ss -lnt | grep -qE ":${PORT} "; then
      break
    fi
    sleep 1
  done
  if ss -lnt | grep -qE ":${PORT} "; then
    echo "port ${PORT} still listening after stop; giving it 10s more"
    sleep 10
  fi
  if [[ -f "$LLAMA_QUALITY" ]]; then
    mv -f "$LLAMA_QUALITY" "${LLAMA_QUALITY}.retired-halogen"
    echo "retired $LLAMA_QUALITY"
  fi
  if [[ -f "$LLAMA_DEEP" ]]; then
    mv -f "$LLAMA_DEEP" "${LLAMA_DEEP}.retired-halogen"
    echo "retired $LLAMA_DEEP"
  fi
  # Ollama cannot hold weights alongside halogen either.
  systemctl --user stop ollama 2>/dev/null || true
  systemctl stop ollama 2>/dev/null || true
  if [[ -w /proc/sys/vm/compact_memory ]]; then
    echo 1 > /proc/sys/vm/compact_memory
  else
    sudo sh -c 'echo 1 > /proc/sys/vm/compact_memory' || true
  fi
}

wait_health() {
  local i
  for i in $(seq 1 360); do
    if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      echo "healthy after ~$((i * 5))s"
      return 0
    fi
    if systemctl --user is-failed halogen-flash.service >/dev/null 2>&1; then
      echo "halogen-flash FAILED"
      journalctl --user -u halogen-flash.service -n 80 --no-pager || true
      return 1
    fi
    sleep 5
  done
  echo "health timeout"
  journalctl --user -u halogen-flash.service -n 80 --no-pager || true
  return 1
}

start_halogen() {
  write_quadlet
  systemctl --user daemon-reload
  # Quadlets are generated units: systemd refuses `enable` on them. Boot start
  # comes from [Install] WantedBy=default.target plus user lingering.
  systemctl --user enable halogen-flash.service >/dev/null 2>&1 || true
  systemctl --user reset-failed halogen-flash.service 2>/dev/null || true
  systemctl --user restart halogen-flash.service
  wait_health
}

if [[ "${HALOGEN_SERVE_ONLY:-0}" != "1" ]]; then
  echo "starting weight download and image pull in parallel"
  download_weights &
  dl_pid=$!
  pull_image &
  pull_pid=$!
  dl_rc=0
  pull_rc=0
  wait "$dl_pid" || dl_rc=$?
  wait "$pull_pid" || pull_rc=$?
  if [[ "$dl_rc" -ne 0 || "$pull_rc" -ne 0 ]]; then
    echo "parallel work failed download=${dl_rc} pull=${pull_rc}"
    exit 1
  fi
else
  weights_ready || { echo "HALOGEN_SERVE_ONLY=1 but weights are not ready"; exit 1; }
  podman image exists "$IMAGE" || { echo "HALOGEN_SERVE_ONLY=1 but image is missing"; exit 1; }
fi

retire_llama
start_halogen || {
  python3 - "$STATE" <<'PY'
import json, sys, datetime
json.dump({
  "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
  "phase": "failed_health",
  "log": "/tmp/halogen-setup.log",
}, open(sys.argv[1], "w"), indent=2)
PY
  exit 1
}

models_json="$(curl -sf "http://127.0.0.1:${PORT}/v1/models" || true)"
echo "models: ${models_json}"
avail_gb="$(awk '/MemAvailable:/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)"
echo "MemAvailable=${avail_gb} Gi after start (the server's own startup line is the one to believe — MemAvailable counts locked weights as reclaimable cache)"

curl -sf "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"${ALIAS}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word pong.\"}],\"max_completion_tokens\":32,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false}}" \
  | python3 -m json.tool | head -40

python3 - "$STATE" "$IMAGE" "$PORT" "$avail_gb" <<'PY'
import json, sys, datetime
path, image, port, avail = sys.argv[1:]
json.dump({
  "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
  "phase": "serving",
  "alias": "qwen3.8-flash-next",
  "port": int(port),
  "image": image,
  "engine": "halogen-flash",
  "mem_available_gi": avail,
  "llama_quality": "retired",
}, open(path, "w"), indent=2)
print("wrote", path)
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) halogen-setup done ====="
