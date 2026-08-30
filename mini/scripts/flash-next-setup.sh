#!/usr/bin/env bash
# Unattended Flash-Next bring-up on mini (Strix Halo / gfx1151 / 122 Gi).
# Runtime is llama.cpp master (qwen4exp landed via #27742), built inside the
# Vulkan toolbox. Idempotent. Does NOT restore 35B/27B.
set -euo pipefail
export HOME="${HOME:-/home/blewis}"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

PR_API="https://api.github.com/repos/ggml-org/llama.cpp/pulls/27742"
HF_REPO="unsloth/Qwen3.8-Flash-Next-GGUF"
QUANT="UD-Q4_K_XL"
MODELS_DIR="/data/models"
LOCAL_DIR="${MODELS_DIR}/unsloth-Qwen3.8-Flash-Next-${QUANT}"
SHARD1="${LOCAL_DIR}/${QUANT}/Qwen3.8-Flash-Next-${QUANT}-00001-of-00004.gguf"
EXPECTED_BYTES=111334654784
ALIAS="qwen3.8-flash-next"
PORT=8090
CTX_NATIVE=262144
CTX_PROBE=8192
CTX=$CTX_PROBE
NP=1
CACHE_RAM_MIB=0
NO_MMAP=1
QUADLET_DIR="${HOME}/.config/containers/systemd"
QUALITY_UNIT="${QUADLET_DIR}/llama-quality.container"
DEEP_UNIT="${QUADLET_DIR}/llama-deep.container"
LOG="/tmp/flash-next-setup.log"
STATE="${HOME}/.local/state/flash-next-setup.json"
LOCK="${HOME}/.local/state/flash-next-setup.lock"
# Stay under /data/models — /data itself is root:root 755.
BUILD_DIR="${MODELS_DIR}/src/llama.cpp"
BIN_DIR="${MODELS_DIR}/llama.cpp-qwen4exp"
TOOLBOX_REPO="docker.io/kyuz0/amd-strix-halo-toolboxes"
# Fallback toolbox that is already on the box (Mesa/RADV). Binary may be too old.
FALLBACK_IMAGE="${TOOLBOX_REPO}:vulkan-radv_20260826T085114"

exec > >(tee -a "$LOG") 2>&1
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) flash-next-setup start pid=$$ ====="
export HF_HOME="${MODELS_DIR}/huggingface"
export HF_HUB_ENABLE_HF_TRANSFER=1

mkdir -p "$(dirname "$STATE")" "$LOCAL_DIR" "$BIN_DIR" "$(dirname "$BUILD_DIR")"

# ── lock ──────────────────────────────────────────────────────────────────────
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another flash-next-setup is running; exiting"
  exit 0
fi

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "missing $1"; exit 1; }; }
need_cmd curl
need_cmd python3
need_cmd podman
need_cmd systemctl

# ── gate: PR merged ───────────────────────────────────────────────────────────
pr_json="$(curl -fsSL -H 'Accept: application/vnd.github+json' "$PR_API")"
merged="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("merged"))' <<<"$pr_json")"
pr_state="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("state"))' <<<"$pr_json")"
merge_sha="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("merge_commit_sha") or d.get("head",{}).get("sha") or ""))' <<<"$pr_json")"
echo "PR #27742 state=${pr_state} merged=${merged} sha=${merge_sha}"

# Prefer master once #27742 is merged; otherwise fall back to the PR head.
if [[ "$merged" == "True" ]]; then
  echo "PR #27742 merged; building llama.cpp from origin/master"
else
  echo "PR #27742 not merged yet (state=${pr_state}); building PR head sha=${merge_sha}"
fi

# ── stop both servers; retire deep so it cannot come back at boot ─────────────
systemctl --user stop llama-servers.target llama-quality llama-deep 2>/dev/null || true
# linger until ports free
for i in 1 2 3 4 5 6 7 8 9 10; do
  if ! ss -lnt | grep -qE ':809[01] '; then break; fi
  sleep 1
done
if [[ -f "$DEEP_UNIT" ]]; then
  systemctl --user disable --now llama-deep 2>/dev/null || true
  mv -f "$DEEP_UNIT" "${DEEP_UNIT}.retired-flash-next"
  echo "retired $DEEP_UNIT"
fi
# Ollama must stay down — it and llama-server cannot both hold weights.
systemctl --user stop ollama 2>/dev/null || true
systemctl stop ollama 2>/dev/null || true

xl_bytes() {
  python3 - <<PY
import os
root="${LOCAL_DIR}/${QUANT}"
total=0
if os.path.isdir(root):
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            total += os.path.getsize(os.path.join(dp, fn))
print(total)
PY
}

download_xl() {
  local have_bytes
  have_bytes="$(xl_bytes)"
  echo "local XL bytes=${have_bytes} expected=${EXPECTED_BYTES}"
  if [[ "$have_bytes" -lt "$EXPECTED_BYTES" ]]; then
    need_cmd hf
    echo "downloading ${HF_REPO} ${QUANT} -> ${LOCAL_DIR}"
    hf download "$HF_REPO" \
      --include "${QUANT}/*" \
      --local-dir "$LOCAL_DIR"
    have_bytes="$(xl_bytes)"
  fi
  if [[ ! -f "$SHARD1" ]]; then
    echo "missing first shard: $SHARD1"
    return 1
  fi
  echo "weights ready at $SHARD1 (${have_bytes} bytes)"
}

build_runtime() {
  IMAGE="$FALLBACK_IMAGE"
  podman pull "$IMAGE" || true
  need_cmd git
  if [[ ! -d "$BUILD_DIR/.git" ]]; then
    git clone --filter=blob:none --no-checkout https://github.com/ggml-org/llama.cpp.git "$BUILD_DIR"
  fi
  if [[ "$merged" == "True" ]]; then
    git -C "$BUILD_DIR" fetch --depth 1 origin master
    git -C "$BUILD_DIR" checkout -B master FETCH_HEAD
    git -C "$BUILD_DIR" reset --hard FETCH_HEAD
  else
    git -C "$BUILD_DIR" fetch --depth 1 origin pull/27742/head:pr-27742
    git -C "$BUILD_DIR" checkout -f pr-27742
  fi
  git -C "$BUILD_DIR" clean -fdx -e build
  echo "llama.cpp at $(git -C "$BUILD_DIR" rev-parse --short HEAD) $(git -C "$BUILD_DIR" log -1 --oneline)"
  # Build in the toolbox so we link against its gfx1151 Mesa/RADV. gcc/ninja
  # and vulkan-loader-devel are present; /usr/bin/ld is a dangling
  # alternatives symlink (target /etc/alternatives/ld missing).
  podman run --rm \
    --security-opt seccomp=unconfined \
    -v "$BUILD_DIR":/src/llama.cpp:Z \
    -v "$BIN_DIR":/out:Z \
    "$IMAGE" \
    bash -lc '
      set -euo pipefail
      cd /src/llama.cpp
      if [[ ! -e /usr/bin/ld ]] && [[ -x /usr/bin/ld.bfd ]]; then
        ln -sfn /usr/bin/ld.bfd /usr/bin/ld
      fi
      rm -rf build
      cmake -S . -B build -G Ninja \
        -DGGML_VULKAN=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_SERVER=ON
      cmake --build build --config Release --target llama-server llama-cli
      install -m 0755 build/bin/llama-server /out/llama-server
      if [[ -x build/bin/llama-cli ]]; then
        install -m 0755 build/bin/llama-cli /out/llama-cli
      fi
      test -x /out/llama-server
    '
  if ! grep -a -m1 qwen4exp "${BIN_DIR}/llama-server" >/dev/null; then
    echo "built llama-server still lacks qwen4exp"
    return 1
  fi
  ENTRYPOINT="/opt/llama-fn/llama-server"
  EXTRA_VOL="Volume=${BIN_DIR}:/opt/llama-fn:ro"
  echo "runtime ready: ${BIN_DIR}/llama-server"
}

# Background functions cannot export these into the parent.
IMAGE="$FALLBACK_IMAGE"
ENTRYPOINT="/opt/llama-fn/llama-server"
EXTRA_VOL="Volume=${BIN_DIR}:/opt/llama-fn:ro"

if [[ "${FLASH_NEXT_SERVE_ONLY:-0}" == "1" ]]; then
  echo "serve-only: using existing XL + ${BIN_DIR}/llama-server"
  [[ -f "$SHARD1" ]] || { echo "missing $SHARD1"; exit 1; }
  [[ -x "${BIN_DIR}/llama-server" ]] || { echo "missing llama-server binary"; exit 1; }
  grep -a -m1 qwen4exp "${BIN_DIR}/llama-server" >/dev/null || { echo "binary lacks qwen4exp"; exit 1; }
else
  echo "starting XL download and llama.cpp Vulkan build in parallel"
  download_xl &
  dl_pid=$!
  build_runtime &
  build_pid=$!
  dl_rc=0
  build_rc=0
  wait "$dl_pid" || dl_rc=$?
  wait "$build_pid" || build_rc=$?
  if [[ "$dl_rc" -ne 0 || "$build_rc" -ne 0 ]]; then
    echo "parallel work failed download=${dl_rc} build=${build_rc}"
    exit 1
  fi
fi

# ── write / start helpers ─────────────────────────────────────────────────────
# Weights 111.3 GB + --no-mmap leave ~10 GB. Flash-Next KV is ~24 KB/token, so
# the native 262144 window is ~6 GiB — we spend the leftover on ctx, not cache.
# Probe at 8k, measure MemAvailable + kv bytes/token, then raise to min(262144,
# what actually fits). mmap is only the OOM fallback (PLE can stay on SSD).
write_quality_unit() {
  local mmap_args="-ngl 999 -fa 1 --no-mmap --metrics"
  if [[ "$NO_MMAP" != 1 ]]; then
    mmap_args="-ngl 999 -fa 1 --metrics"
  fi
  cat > "$QUALITY_UNIT" <<EOF
# Managed by flash-next-setup.sh — Qwen3.8-Flash-Next UD-Q4_K_XL on :8090.
# Do not restore llama-deep alongside this; XL is the whole 122 Gi budget.
[Unit]
Description=llama.cpp [quality] ${ALIAS} on :${PORT}
After=network-online.target
Wants=network-online.target
PartOf=llama-servers.target

[Container]
ContainerName=llama-quality
Image=${IMAGE}
Entrypoint=${ENTRYPOINT}
Network=host
AddDevice=/dev/dri
AddDevice=/dev/kfd
PodmanArgs=--security-opt seccomp=unconfined
Volume=${MODELS_DIR}:${MODELS_DIR}:ro
${EXTRA_VOL}

Exec=-m ${SHARD1} \\
     --alias ${ALIAS} \\
     --host 0.0.0.0 \\
     --port ${PORT} \\
     -c ${CTX} \\
     -np ${NP} \\
     ${mmap_args} \\
     --cache-ram ${CACHE_RAM_MIB} \\
     --chat-template-kwargs '{"enable_thinking": true, "reasoning_effort": "medium"}' \\
     --temp 1.0 --top-k 20 --top-p 0.95 --min-p 0 --presence-penalty 0 --reasoning-budget 6000 \\
     --no-webui

HealthCmd=curl -sf http://127.0.0.1:${PORT}/health
HealthInterval=30s
HealthStartPeriod=1800s
HealthTimeout=10s
HealthRetries=3

[Service]
Restart=on-failure
RestartSec=10
TimeoutStartSec=1800

[Install]
WantedBy=default.target llama-servers.target
EOF
  sed -i '/^Volume=$/d' "$QUALITY_UNIT"
}

wait_quality_health() {
  local i
  for i in $(seq 1 360); do
    if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      echo "healthy ctx=${CTX} no_mmap=${NO_MMAP} after ~$((i*5))s"
      return 0
    fi
    if systemctl --user is-failed llama-quality.service >/dev/null 2>&1; then
      echo "llama-quality FAILED ctx=${CTX} no_mmap=${NO_MMAP}"
      journalctl --user -u llama-quality.service -n 80 --no-pager || true
      return 1
    fi
    sleep 5
  done
  echo "health timeout ctx=${CTX} no_mmap=${NO_MMAP}"
  return 1
}

start_quality() {
  write_quality_unit
  systemctl --user daemon-reload
  systemctl --user enable llama-quality.service >/dev/null
  systemctl --user reset-failed llama-quality.service 2>/dev/null || true
  systemctl --user restart llama-quality.service
  wait_quality_health
}

fail_health() {
  python3 - "$STATE" <<'PY'
import json,sys,datetime
json.dump({
  "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
  "phase": "failed_health",
  "log": "/tmp/flash-next-setup.log",
}, open(sys.argv[1],"w"), indent=2)
PY
  exit 1
}

echo "probe load at ctx=${CTX_PROBE} to size KV"
CTX=$CTX_PROBE
if ! start_quality; then
  echo "probe failed with --no-mmap; retrying with mmap (PLE on SSD)"
  NO_MMAP=0
  start_quality || fail_health
fi

avail_gb="$(awk '/MemAvailable:/ {printf "%.4f", $2/1024/1024}' /proc/meminfo)"
echo "MemAvailable=${avail_gb} Gi after probe ctx=${CTX}"

# ~24 KB/token theoretical; prefer the load log if llama.cpp printed a KV size.
journalctl --user -u llama-quality.service -n 400 --no-pager > /tmp/flash-next-kv.log || true
# Heredoc must NOT sit inside $() — python regex has ')' and bash closes early.
python3 - "$CTX_PROBE" "$CTX_NATIVE" "$avail_gb" "$NO_MMAP" /tmp/flash-next-kv.log /tmp/flash-next-ctx.txt <<'PY'
import re, sys
probe = int(sys.argv[1])
native = int(sys.argv[2])
avail_gi = float(sys.argv[3])
no_mmap = sys.argv[4] == "1"
log = open(sys.argv[5], errors="replace").read()
out_path = sys.argv[6]
bpt = 24576.0  # 12 QSA layers * 2 KV heads * 256 * 2 * 2 bytes
mibs = []
for m in re.finditer(r'(?i)(?:kv_cache|kv cache|recurrent|indexer)[^\n]*?size\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*MiB', log):
    mibs.append(float(m.group(1)))
if not mibs:
    for m in re.finditer(r'(?i)llama_kv[^\n]*?size\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*MiB', log):
        mibs.append(float(m.group(1)))
if mibs:
    bpt = max(mibs) * 1024 * 1024 / probe
    print(f"parsed kv {max(mibs):.1f} MiB at {probe} -> {bpt:.0f} B/token", file=sys.stderr)
else:
    print(f"no kv size in log; using theoretical {bpt:.0f} B/token", file=sys.stderr)
# mmap under-reports RSS (PLE faults later); keep more slack.
slack_gi = 2.0 if no_mmap else 4.0
extra_bytes = max(0.0, (avail_gi - slack_gi) * 1024**3)
max_tokens = probe + extra_bytes / bpt
# Never above native: YaRN is ignored on interleaved mrope.
ctx = int(min(native, max(probe, max_tokens)))
ctx = ctx - (ctx % 256)
print(f"avail={avail_gi:.2f}Gi slack={slack_gi}Gi -> ctx {ctx}", file=sys.stderr)
open(out_path, "w").write(str(ctx))
PY
CTX=$(cat /tmp/flash-next-ctx.txt)
echo "target ctx=${CTX} native=${CTX_NATIVE}" 

if [[ "$CTX" -ne "$CTX_PROBE" ]]; then
  if ! start_quality; then
    echo "ctx=${CTX} failed; walking down toward a size that fits"
    landed=0
    for try in 196608 131072 98304 65536 49152 32768 16384; do
      if [[ "$try" -ge "$CTX" ]]; then
        continue
      fi
      CTX=$try
      if start_quality; then
        landed=1
        break
      fi
    done
    [[ "$landed" -eq 1 ]] || fail_health
  fi
fi

# ── smoke ─────────────────────────────────────────────────────────────────────
models_json="$(curl -sf "http://127.0.0.1:${PORT}/v1/models" || true)"
echo "models: ${models_json}"
avail_gb="$(awk '/MemAvailable:/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)"
echo "MemAvailable=${avail_gb} Gi after final ctx=${CTX}"

curl -sf "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"${ALIAS}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word pong.\"}],\"max_tokens\":32}" \
  | python3 -m json.tool | head -40

python3 - "$STATE" "$IMAGE" "$ENTRYPOINT" "$CTX" "$avail_gb" "$merge_sha" "$NO_MMAP" <<'PY'
import json,sys,datetime
path, image, entry, ctx, avail, sha, no_mmap = sys.argv[1:]
json.dump({
  "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
  "phase": "serving",
  "alias": "qwen3.8-flash-next",
  "port": 8090,
  "image": image,
  "entrypoint": entry,
  "ctx": int(ctx),
  "parallel": 1,
  "cache_ram_mib": 0,
  "no_mmap": no_mmap == "1",
  "mem_available_gi": avail,
  "merge_sha": sha,
  "deep": "retired",
}, open(path,"w"), indent=2)
print("wrote", path)
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) flash-next-setup done ====="
