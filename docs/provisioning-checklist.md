# Provisioning checklist — llama.cpp serving path + quality-loop removal

One-time steps for converging the change that made `llama-server` mini's serving
path and deleted quality-loop. Ansible handles everything it can; this file is
the part it **cannot** — Ansible does not remove what it no longer manages, so
retiring a role leaves its artifacts behind on the host.

Delete this file once both boxes have converged and the manual steps are done.

---

## Order matters

Converge **mini before ser5**. ser5's Open WebUI points at mini's `:8090`/`:8091`;
bringing ser5 up first just gives you a UI with dead endpoints.

---

## 1. mini — before `make provision`

**Stage the GGUFs first.** `roles/llamacpp` asserts every `llamacpp_instances`
model exists and fails the play if one is missing. This is deliberate — the
alternative is a unit that restart-loops with the real cause buried in
`journalctl --user`. Already present on the live box; a rebuild needs them again.

```sh
ls -la /data/models/*.gguf
#   qwen3.6-35b-a3b-mtp-q4_K_M.gguf   21.7 GB   <- required
#   gpt-oss-20b-MXFP4.gguf            12.1 GB   <- required
```

If missing:

```sh
# qwen — copy out of Ollama's blob store (already pulled; same file, no download)
sudo -n python3 - <<'EOF'
import json, glob, shutil
m = glob.glob('/data/ollama/manifests/**/qwen3.6/35b-a3b-mtp-q4_K_M', recursive=True)[0]
d = [l for l in json.load(open(m))['layers'] if 'model' in l['mediaType']][0]['digest']
shutil.copyfile('/data/ollama/blobs/' + d.replace('sha256:', 'sha256-'),
                '/data/models/qwen3.6-35b-a3b-mtp-q4_K_M.gguf')
EOF
sudo chown blewis:blewis /data/models/qwen3.6-35b-a3b-mtp-q4_K_M.gguf

# gpt-oss — MXFP4 is its native format, so no requantisation loss
HF_HOME=/data/models/huggingface \
  hf download ggml-org/gpt-oss-20b-GGUF gpt-oss-20b-MXFP4.gguf \
  --local-dir /data/models
```

Do **not** hardlink these out of `/data/ollama/blobs`. The quadlets bind-mount
`llamacpp_models_dir` read-only, and a hardlink both hides the dependency and
pins the blob alive after an `ollama rm`.

## 2. mini — after `make provision`

Run `make mini-preview` (check + diff) first. This branch rewrote `amdgpu_rocm`
from apt to a tarball, folded `roles/toolboxes` into `roles/llamacpp`, and moved
the model directory — **none of it has been through a real converge**, so the
diff is worth reading before the apply.

```sh
# ── serving path ───────────────────────────────────────────────────────────
systemctl --user is-active llama-quality llama-throughput          # active active
journalctl --user -u llama-quality    -n 200 | grep -oE 'n_slots = [0-9]+, n_ctx_slot = [0-9]+'
journalctl --user -u llama-throughput -n 200 | grep -oE 'n_slots = [0-9]+, n_ctx_slot = [0-9]+'
#   quality    -> n_slots = 4, n_ctx_slot = 131072
#   throughput -> n_slots = 8, n_ctx_slot = 131072

# MTP is silent when it does NOT engage — the loader just logs
# "unused tensor blk.40.nextn.* -- ignoring" and carries on at ~15% less speed.
journalctl --user -u llama-quality -n 300 | grep -c 'MTP draft context'    # >= 1

# group control (the reason for the quadlet migration)
systemctl --user stop  llama-servers.target   # both down, ports released
systemctl --user start llama-servers.target

# metrics endpoint — ser5's Prometheus scrapes these
curl -sf http://127.0.0.1:8090/metrics | grep -c '^llamacpp:'   # > 0

# ── GPU stack ──────────────────────────────────────────────────────────────
readlink /opt/rocm                     # /opt/rocm-7.14.0
sudo /opt/rocm/bin/rocminfo | grep -m1 gfx1151     # named natively, no override
amdgpu_top --version                   # survived the apt purge

# ── what should NOT be there ───────────────────────────────────────────────
which distrobox                        # not installed
systemctl is-active ollama             # inactive
ls /data/toolboxes 2>/dev/null         # gone; models live in /data/models
```

**Throughput sanity.** A GPU that silently fell back to CPU still serves — it
just does ~2 tok/s instead of ~87. That is the failure mode to check for, and a
`curl` to `/v1/models` will not reveal it:

```sh
scp packages/inference-bench/lbench.py blewis@mini:/tmp/
ssh blewis@mini "python3 /tmp/lbench.py --base http://127.0.0.1:8090/v1 \
  --model qwen3.6-35b-a3b-mtp --concurrency 1 --max-tokens 192"
# expect ~85-88 tok/s per-stream
```

**Then converge a second time.** Idempotency is unproven on this branch; the
second run should report no changes. Things most likely to churn: the ROCm
`unarchive` (guarded by `creates:`), the quadlet templates, and the legacy-unit
retirement tasks.

- [ ] **The ROCm tarball is ~1.71 GB and downloads from source on converge.**
      Nothing is pre-staged — no hand-placed file in `/tmp` — so the fetch is
      reproducible rather than trusting whatever happened to be lying around.
      `get_url` verifies it against `rocm_tarball_sha256`, so a truncated or
      altered download fails at the download step instead of half unpacking into
      the ROCm prefix, where it would surface later as a broken `rocminfo`. The
      unpack is guarded by `creates:`, so the already-installed tree is left
      alone and the first converge just verifies and moves on.
- [ ] **Copy the benchmarks over when you want to re-measure.** They live in the
      repo but nothing deploys them:
      `scp packages/inference-bench/*.py blewis@mini:/tmp/`

## 2b. One-time migrations already done on the live box

These are recorded so a **rebuild** reproduces them, and so nobody wonders where
things went. All are already true on mini.

- **`roles/toolboxes` is gone.** Its udev rule, model dir and HuggingFace CLI
  moved into `roles/llamacpp`; distrobox is purged. Interactive llama.cpp work is
  now `podman run --rm -it --device /dev/dri --device /dev/kfd --security-opt
  seccomp=unconfined -v /data/models:/data/models:ro <image> /bin/bash`.
- **Models moved** `/data/toolboxes/models` -> `/data/models`, since nothing
  called a toolbox exists any more.
- **ROCm 7.2.4 (apt) -> 7.14.0 (TheRock tarball).** The apt stack was purged
  (22 GiB) and the gfx1151 tarball unpacked to `/opt/rocm-7.14.0` (8.3 GiB).
  Purging the apt packages **deletes the `/opt/rocm` symlink** — it was
  update-alternatives-managed — so the role recreates it unconditionally. If you
  do this by hand, recreate the symlink or `rocminfo` vanishes.
  `amdgpu-top` matches the same package glob as the ROCm packages; exclude it
  from any purge or you lose your GPU monitor.

## 3. Using Ollama to try a model

Ollama's env was retuned for this role: `context 32768`, `parallel 1`,
`max_loaded 1`, `keep_alive 5m` — previously `131072 / 2 / 2 / -1`, which was
sized for it being the serving path. `keep_alive: -1` in particular would pin a
test model on GPU memory forever.

It still cannot run at full size alongside llama-server — 73 GiB is already
resident. Free room first:

```sh
systemctl --user stop llama-throughput      # frees ~36 GiB
sudo systemctl start ollama
ollama run some-new-model:tag               # unloads itself after 5m idle
sudo systemctl stop ollama
systemctl --user start llama-throughput
```

## 4. ser5 — after `make provision`

**This converge carries two major-version container bumps**, which is the largest
untested risk on the branch:

| | was | now |
|---|---|---|
| Prometheus | v2.55.1 | **v3.13.2** (major) |
| Grafana | 11.4.0 | **13.1.1** (two majors) |

Prometheus was checked before bumping — only `--config.file` and
`--storage.tsdb.retention.time` are passed, both still valid in v3, the config is
plain `static_configs`, and the TSDB reads forward. **Grafana 11 → 13 was not
verifiable ahead of time**: dashboards usually migrate, but two majors is where
panel schemas and datasource plugins change. Check your dashboards specifically,
not just that the service is up.

```sh
systemctl --user is-active prometheus grafana openwebui hermes
podman ps --format '{{.Names}} | {{.Image}} | {{.Status}}'

# Prometheus 3 came up AND is scraping mini's new llama.cpp targets
curl -s 'http://127.0.0.1:9090/api/v1/targets' \
  | python3 -c 'import json,sys; [print(" ", t["labels"].get("instance"), t["health"]) \
      for t in json.load(sys.stdin)["data"]["activeTargets"]]'
#   expect mini-quality and mini-throughput both "up"

# Grafana 13 — log in and open each dashboard. "Service is up" is not the test.
curl -sf http://127.0.0.1:3000/api/health

# the retired quality-gate skill is removed by the role, not by hand
ls -d /data/services/hermes/skills/quality-gate 2>/dev/null || echo "removed OK"

# Open WebUI still sees both mini endpoints
podman exec openwebui curl -sf http://mini:8090/v1/models >/dev/null && echo "8090 OK"
podman exec openwebui curl -sf http://mini:8091/v1/models >/dev/null && echo "8091 OK"
```

If Grafana 13 breaks a dashboard, the rollback is one line —
`grafana_version: "11.4.0"` in `roles/observability/defaults/main.yml` — and the
data is in a volume, so nothing is lost by reverting.

**Manual cleanup — verified present, none of it Ansible-managed:**

- [ ] **Dangling `qloop` symlinks.** They point into a venv that no longer exists:
      ```sh
      sudo rm -f /usr/local/bin/qloop /usr/local/bin/loopkit
      ```
- [ ] **Orphaned systemd unit** from the deleted agentlab role:
      ```sh
      systemctl --user disable agentlab-run@.service 2>/dev/null
      rm -f ~/.config/systemd/user/agentlab-run@.service
      systemctl --user daemon-reload
      ```
- [ ] **Decide on `/data/agentlab` — deliberately, not by reflex.** Left on disk
      and still in restic on purpose. It holds `runs.db`: **356 recorded
      evaluation runs that cannot be regenerated**, since the harness is deleted.
      The rest is disposable.
      ```sh
      du -sh /data/agentlab/*
      #   runs.db   140K   <- the irreplaceable part
      #   venv       14M   <- dead
      #   src/traces/suites/playbooks/jobs/datasets   ~1.3M
      ```
      If you want the record but not the corpse:
      ```sh
      mkdir -p /data/archive && cp /data/agentlab/runs.db /data/archive/qloop-runs-2026-08.db
      rm -rf /data/agentlab
      ```
      Then drop the `/data/agentlab` line from
      `roles/backups/defaults/main.yml`.
- [ ] **Open WebUI needs no action.** Its endpoints already point at
      `:8090`/`:8091` in `webui.db`. The Ansible vars are seeds for a fresh data
      dir only — the DB wins on a configured instance, so converging will neither
      change nor break it.

## 5. Known gaps

- **Neither box has been through a real `make provision` for this change.** The
  roles were validated with `ansible-playbook --syntax-check` and deployed by
  hand in exactly the form Ansible renders (template output diffed against what
  is live), but **idempotency on a second converge is unproven**. Run
  `make mini-preview` / `make ser5-preview` (check + diff) before the real thing.
- **`parallel: 4` on the qwen instance is sized to a workload that no longer
  exists** — it was measured against quality-loop's 3-4 candidate fan-out. It is
  a sane default for interactive use, but re-measure with
  `packages/inference-bench/fanoutsim.py` against whatever replaces it.
- **Run-to-run benchmark variance is high** (the same configuration returned 63.0
  and 99.9 tok/s on separate runs, unexplained). Treat sub-10% differences as
  noise and re-run before acting on one.
- **Grafana 11 -> 13 is two majors and was not verifiable ahead of time.** Open
  the dashboards, not just the health endpoint. Rollback is one line.
- **Nothing measures output quality any more.** `inference-bench` measures
  throughput. quality-loop was deleted on its own evidence and nothing replaced
  the thing it was meant to provide — see `todo.md`. Worth answering before any
  claim about quality gets made externally.

## 6. When this file goes away

Delete it once both boxes have converged, the second converge came back clean,
and the unchecked boxes above are resolved. Its whole job is to carry the
one-time steps across the gap between "the branch is correct" and "the boxes
match it" — after that it is just another stale document.

The parts worth keeping live elsewhere already: mini's gotchas in
`mini/AGENTS.md`, the sizing rationale in `roles/llamacpp/defaults/main.yml`, the
benchmark caveats in `packages/inference-bench/README.md`.
