#!/usr/bin/env bash
# HTTP benches against the live halogen-flash server on :8090.
# Does NOT start a second engine. Writes /data/bench/halogen/<stamp>/
set -euo pipefail
export PATH="${HOME:-/home/blewis}/.local/bin:/usr/local/bin:${PATH}"
export PYTHONUNBUFFERED=1
export HF_HOME="${HF_HOME:-/data/models/huggingface}"

API="http://127.0.0.1:8090"
IMAGE="ghcr.io/peonist-ai/halogen-flash-server:0.5.8"
MODEL="qwen3.8-flash-next"
TOK="/data/models/halogen-qwen3.8-flash-next/tokenizer"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="/data/bench/halogen/${STAMP}"
mkdir -p "$OUT"
ln -sfn "$OUT" /data/bench/halogen/latest

exec > >(tee -a "$OUT/run.log") 2>&1
echo "===== $STAMP halogen bench start pid=$$ ====="

need() { command -v "$1" >/dev/null || { echo "missing $1"; exit 1; }; }
need curl
need python3
need podman
need llama-benchy

curl -sf --max-time 10 "$API/health" >"$OUT/health.json" || {
  echo "halogen /health failed"
  exit 1
}

{
  echo "time_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "cmdline=$(cat /proc/cmdline)"
  echo "image=$IMAGE"
  echo "model=$MODEL"
  echo -n "power1_average_uW="; cat /sys/class/hwmon/hwmon5/power1_average 2>/dev/null || echo NA
  echo -n "power1_input_uW="; cat /sys/class/hwmon/hwmon5/power1_input 2>/dev/null || echo NA
  echo -n "freq1_Hz="; cat /sys/class/hwmon/hwmon5/freq1_input 2>/dev/null || echo NA
  echo -n "temp1_mC="; cat /sys/class/hwmon/hwmon5/temp1_input 2>/dev/null || echo NA
  echo -n "MemAvailable_kB="; awk '/MemAvailable:/ {print $2}' /proc/meminfo
} | tee "$OUT/conditions.txt"

# Sample package power while benches run.
(
  while kill -0 $$ 2>/dev/null; do
    printf '%s avg_uW=%s in_uW=%s freq_Hz=%s temp_mC=%s\n' \
      "$(date -u +%H:%M:%S)" \
      "$(cat /sys/class/hwmon/hwmon5/power1_average 2>/dev/null || echo NA)" \
      "$(cat /sys/class/hwmon/hwmon5/power1_input 2>/dev/null || echo NA)" \
      "$(cat /sys/class/hwmon/hwmon5/freq1_input 2>/dev/null || echo NA)" \
      "$(cat /sys/class/hwmon/hwmon5/temp1_input 2>/dev/null || echo NA)"
    sleep 2
  done
) >"$OUT/power.tsv" &
PWR_PID=$!
trap 'kill $PWR_PID 2>/dev/null || true' EXIT

hb() {
  podman run --rm --network=host --entrypoint python3 "$IMAGE" \
    /halogen/tools/halogen-bench.py --api "$API" "$@"
}

echo ""
echo "=== halogen-bench sweep pp2048,8192,32768 tg128 serial,mtp x3 effort=low ==="
hb -p 2048,8192,32768 -n 128 -d serial,mtp -r 3 --effort low --json \
  | tee "$OUT/halogen-sweep.json"

echo ""
echo "=== halogen-bench tg256 serial,mtp (default prompt shapes, effort=low) ==="
hb -n 256 -d serial,mtp -r 3 --effort low --json \
  | tee "$OUT/halogen-tg256.json"

EXTRA='{"chat_template_kwargs":{"enable_thinking":false},"temperature":0}'

echo ""
echo "=== llama-benchy depth pp1024 tg256 depths 0 4096 16384 32768 ==="
llama-benchy \
  --base-url "$API/v1" \
  --model "$MODEL" --tokenizer "$TOK" \
  --served-model-name "$MODEL" \
  --pp 1024 --tg 256 --concurrency 1 \
  --depth 0 4096 16384 32768 \
  --runs 3 --latency-mode generation \
  --extra-body "$EXTRA" \
  --skip-coherence \
  --format json --save-result "$OUT/benchy-depth.json" \
  --emit-progress "$OUT/benchy-depth.progress" \
  | tee "$OUT/benchy-depth.log"

echo ""
echo "=== llama-benchy prefix-cache pp2048 tg256 depth 16384 32768 conc 1 ==="
llama-benchy \
  --base-url "$API/v1" \
  --model "$MODEL" --tokenizer "$TOK" \
  --served-model-name "$MODEL" \
  --pp 2048 --tg 256 --concurrency 1 \
  --depth 16384 32768 \
  --enable-prefix-caching \
  --runs 3 --latency-mode generation \
  --extra-body "$EXTRA" \
  --skip-coherence \
  --format json --save-result "$OUT/benchy-prefix.json" \
  --emit-progress "$OUT/benchy-prefix.progress" \
  | tee "$OUT/benchy-prefix.log"

echo ""
echo "=== llama-benchy concurrency pp4096 tg256 conc 1 2 4 ==="
llama-benchy \
  --base-url "$API/v1" \
  --model "$MODEL" --tokenizer "$TOK" \
  --served-model-name "$MODEL" \
  --pp 4096 --tg 256 --exact-tg --depth 0 \
  --concurrency 1 2 4 \
  --runs 3 --latency-mode generation \
  --extra-body "$EXTRA" \
  --skip-coherence \
  --format json --save-result "$OUT/benchy-conc.json" \
  --emit-progress "$OUT/benchy-conc.progress" \
  | tee "$OUT/benchy-conc.log"

kill "$PWR_PID" 2>/dev/null || true
wait "$PWR_PID" 2>/dev/null || true

python3 - "$OUT" <<'PY'
import json, os, sys, glob, statistics
out = sys.argv[1]

def loadj(name):
    p = os.path.join(out, name)
    if not os.path.isfile(p) or os.path.getsize(p) == 0:
        return None
    try:
        return json.load(open(p))
    except Exception as e:
        return {"_error": str(e), "_raw_head": open(p, errors="replace").read()[:500]}

summary = {
    "dir": out,
    "sweep": loadj("halogen-sweep.json"),
    "tg256": loadj("halogen-tg256.json"),
    "depth": loadj("benchy-depth.json"),
    "prefix": loadj("benchy-prefix.json"),
    "conc": loadj("benchy-conc.json"),
}
json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=2)

# power stats
pw = []
for line in open(os.path.join(out, "power.tsv"), errors="replace"):
    if "avg_uW=" in line:
        try:
            v = int(line.split("avg_uW=")[1].split()[0])
            if v > 0:
                pw.append(v / 1e6)
        except Exception:
            pass
if pw:
    print(f"package power W: n={len(pw)} min={min(pw):.1f} median={statistics.median(pw):.1f} max={max(pw):.1f}")
print("wrote", os.path.join(out, "summary.json"))
PY

date -u +"%Y-%m-%dT%H:%M:%SZ" >"$OUT/DONE"
echo "===== $STAMP halogen bench done ====="
