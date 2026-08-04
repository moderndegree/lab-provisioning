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
ls -la /data/toolboxes/models/*.gguf
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
                '/data/toolboxes/models/qwen3.6-35b-a3b-mtp-q4_K_M.gguf')
EOF
sudo chown blewis:blewis /data/toolboxes/models/qwen3.6-35b-a3b-mtp-q4_K_M.gguf

# gpt-oss — MXFP4 is its native format, so no requantisation loss
HF_HOME=/data/toolboxes/models/huggingface \
  hf download ggml-org/gpt-oss-20b-GGUF gpt-oss-20b-MXFP4.gguf \
  --local-dir /data/toolboxes/models
```

Do **not** hardlink these out of `/data/ollama/blobs`. The toolbox only
bind-mounts `toolboxes_models_dir`, and a hardlink both hides the dependency and
pins the blob alive after an `ollama rm`.

## 2. mini — after `make provision`

```sh
# both instances up, at the expected slot geometry
systemctl --user is-active llama-quality llama-throughput
journalctl --user -u llama-quality    -n 200 | grep -oE 'n_slots = [0-9]+, n_ctx_slot = [0-9]+'
journalctl --user -u llama-throughput -n 200 | grep -oE 'n_slots = [0-9]+, n_ctx_slot = [0-9]+'
#   quality    -> n_slots = 4, n_ctx_slot = 131072
#   throughput -> n_slots = 8, n_ctx_slot = 131072

# MTP actually engaged (silent if it is not — the loader just logs
# "unused tensor blk.40.nextn.* -- ignoring" and carries on)
journalctl --user -u llama-quality -n 300 | grep -c 'MTP draft context'   # expect >= 1

# group control — this is the point of the quadlet migration
systemctl --user stop  llama-servers.target   # both down, ports released, 0 survivors
systemctl --user start llama-servers.target

# Ollama should be installed and NOT running
systemctl is-enabled ollama    # disabled
systemctl is-active ollama     # inactive
```

Manual cleanup on mini:

- [x] **Lemonade removed** (package, PPA, and its 34 GB cache) — done 2026-08 on
      the live box. A rebuilt box never installs it, so nothing to redo.
- [x] **vLLM-only models reclaimed** — the AWQ checkpoint (24 GB) and
      `openai/gpt-oss-20b` safetensors (13 GB) are gone from the HF cache, along
      with the unused q8 GGUF (37.8 GB), the vllm-therock image (35 GB) and the
      rocm-7.2.4 toolbox (7 GB). ~180 GB total. vLLM is gone entirely — the
      toolbox, its device-name shim and its tuned-MoE wiring all went with it. It
      measured ~3.5x slower than llama.cpp twice, so re-testing means restoring
      the toolbox entry and re-downloading ~59 GB. Deliberate, not an accident.
- [ ] **Copy the benchmarks over when you want to re-measure.** They live in the
      repo now, but nothing deploys them:
      `scp packages/inference-bench/*.py blewis@mini:/tmp/`

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

The Hermes quality-gate skill removal **is** automated (`roles/hermes` has an
explicit `state: absent` task) — without it Hermes would keep discovering a skill
pointing at a script that no longer exists. Verify, then clean the rest by hand:

```sh
ls -d /data/services/hermes/skills/quality-gate 2>/dev/null || echo "removed OK"
```

Manual cleanup on ser5 — all verified present, none of it Ansible-managed:

- [ ] **Dangling `qloop` symlinks.** They point into a venv that is no longer
      provisioned, so they resolve to nothing:
      ```sh
      sudo rm -f /usr/local/bin/qloop /usr/local/bin/loopkit
      ```
- [ ] **Orphaned systemd unit** from the deleted agentlab role:
      ```sh
      systemctl --user disable agentlab-run@.service 2>/dev/null
      rm -f ~/.config/systemd/user/agentlab-run@.service
      systemctl --user daemon-reload
      ```
- [ ] **Decide on `/data/agentlab` — do this deliberately, not by reflex.**
      It is intentionally left on disk and still in restic. It holds `runs.db`:
      **356 recorded evaluation runs that cannot be regenerated**, since the
      harness that produced them is deleted. The rest is disposable.
      ```sh
      du -sh /data/agentlab/*
      #   runs.db   140K   <- the irreplaceable part
      #   venv       14M   <- dead, safe to delete
      #   src/traces/suites/playbooks/jobs/datasets   ~1.3M
      ```
      Recommended: keep `runs.db`, drop the rest, and remove the restic line in
      `ser5/ansible/roles/backups/defaults/main.yml` once you have.
      ```sh
      # if you want the record but not the corpse:
      mkdir -p /data/archive && cp /data/agentlab/runs.db /data/archive/qloop-runs-2026-08.db
      rm -rf /data/agentlab
      ```
- [ ] **Open WebUI needs nothing.** Its endpoints already point at `:8090`/`:8091`
      in `webui.db`. The Ansible vars are seeds for a fresh data dir only — the
      DB wins on a configured instance, so converging will not change or break it.

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
  noise.
