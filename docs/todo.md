# TODO — open punch list

Started from the 2026-07-28 platform review; carries the loose ends from the
2026-08 infrastructure pass as well. There is no roadmap to slot these into —
`docs/roadmap.md` was deleted because it gated everything on a milestone that no
longer existed, and a new one gets written once the infrastructure settles.

Check items off in place; delete the whole doc once it's empty rather than let it
fossilize.

**Highest value right now:** converge both boxes (see Post-provisioning) and
answer the quality question (see Goal balance). Everything else is bookkeeping.

## Operational (do first — a real service is affected)

- [x] **Tear down Open WebUI on mini by hand.** Verified 2026-07-28: already
      clean on mini — no running container, quadlet source file already
      removed, `systemctl --user daemon-reload` confirms `openwebui.service`
      no longer exists.
- [x] `enable_openwebui: true` set and live on ser5 (2026-07-28). Root cause
      of the original provisioning failure was a real bug (`DNSSearch=` in the
      wrong Quadlet section — fixed in `openwebui.container.j2`/`obs.network.j2`
      etc.), not a fluke; `enabled: true` was also dropped from the Quadlet
      systemd tasks since generator-managed units reject `systemctl enable`.
      Verified: `openwebui.service` active, `curl 127.0.0.1:8080` → 200.
- [x] Re-run `make mini-provision` once to confirm `all.yml` (openwebui vars
      removed) converges cleanly with nothing orphaned. Done 2026-07-28.

## Goal balance — resolved by deletion, 2026-08

The 2026-07-28 review found consulting-shaped work running ahead of quality
measurement, and froze the consulting phase behind a measurement milestone. Both
the freeze and the milestone lived in `roadmap.md`.

That whole structure is gone. quality-loop was deleted after its own recorded
data showed the loops did not beat the baseline for their cost, and `roadmap.md`
was deleted with it — it gated every future phase on a milestone that could no
longer be met, which is worse than having no roadmap. A new one gets written once
the infrastructure settles.

- [x] Freeze the consulting phase — moot; the phase structure no longer exists.
- [x] Finish the measurement phase first — moot for the same reason.
- [ ] **Decide what measures QUALITY now.** This is the real hole the deletions
      left. `packages/inference-bench` measures throughput, not output quality,
      so today nothing answers "is it getting better?" — which was goal #1 and
      what the consulting pitch was meant to rest on. Options: ground-truth
      verification wired into the agent flow (tests, schema checks,
      converge-twice idempotency), a small hand-curated eval actually run on a
      cadence, or an honest "nothing, and the quality claims get softened to
      match".

## Measurement gap — closed by deletion, and still open in substance

The original entry was about `gate.py`/`loops.py` parsing a verdict out of the
same class of model being graded, with nothing tracking whether the judge kept
following the format. Parse-rate tracking was added 2026-07-28 and then
superseded: quality-loop was removed entirely 2026-08. The 356 recorded runs
survive as a frozen archive at `/data/agentlab/runs.db` on ser5; nothing writes
to it now.

The underlying gap did not close — it moved. See the quality question under
"Goal balance" above.

## Post-provisioning (this branch has never been converged)

Everything on `inference-concurrency-tuning` was validated with `--syntax-check`
and deployed to the boxes by hand in the form Ansible renders. That is not the
same as a converge. Working through `docs/provisioning-checklist.md` is the
acceptance test; these are the items that outlive it.

- [ ] **`make mini-preview` and `make ser5-preview` before either apply.** The
      branch rewrote `amdgpu_rocm` (apt -> tarball), folded `roles/toolboxes`
      into `roles/llamacpp`, deleted a role, and moved the model directory. Read
      the diff.
- [x] **Converge each box twice.** Done 2026-08-03. Note the acceptance test is
      weaker than it looks: a clean second run did NOT catch the quadlet shadowing
      below. Converge output proves task execution, not applied state.
      Most likely to churn: the ROCm `unarchive` (guarded by `creates:`), the
      quadlet templates, and the legacy-unit retirement tasks — none of which
      have ever run.
- [x] **Open the Grafana dashboards after ser5 converges.** Done 2026-08-05: the
      11 -> 13 migration came through clean — `/api/health` returns
      `{"database":"ok","version":"13.1.1"}` and the Prometheus datasource
      survived intact (`http://prometheus:9090`, still default). Only the API was
      verified, not the UI, and there are still zero dashboards (by design —
      datasource only). Bumped to 13.1.3 on 2026-08-12. Rollback is
      `grafana_version: "11.4.0"`, one line, data lives in a volume.
- [ ] **Drop the retirement tasks once both boxes are past them.** Two blocks
      exist purely to clean up state a converge cannot otherwise reach, and both
      are dead weight afterwards: the legacy `llama-server*.service` retirement
      in `roles/llamacpp`, and the `quality-gate` skill removal in
      `roles/hermes`. Each says so in a comment.
- [x] **Delete `docs/provisioning-checklist.md`** — done 2026-08-04. All boxes
      ticked and verified end-to-end; the one open decision (`/data/agentlab`) moved
      to Housekeeping below.

## Version audit — done 2026-08-12, repeat quarterly

Full stack audited against upstream. Current and needing nothing: Ubuntu 26.04 /
kernel 7.0.0-29 (both boxes), ROCm 7.14.0 (newest gfx1151 TheRock tarball in
existence — only 7.13.0 and 7.14.0 are published), llama.cpp b10380, Prometheus
v3.13.2, podman 5.7.0, amdgpu_top 0.11.5, tailscale 1.102.2.

Bumped: Grafana 13.1.1 -> 13.1.3, opencode -> 1.18.17, node -> 24.19.0.

**The real finding was floating pins, not stale versions.** Six components used
`"latest"` / `"lts"` / `:main`, so what was installed depended on when someone
last converged and the repo recorded nothing. All are now concrete. Two of them
had already produced real drift: opencode sat at 1.18.13 against a `"latest"`
pin, and the llamacpp toolbox image was 158 llama.cpp builds behind.

Worst of the six was `openwebui_image: ":main"` — a rolling DEV tag, with
`openwebui_autoupdate: true` pulling it unattended, on a service published
through Cloudflare.

- [ ] **Re-run this audit quarterly** alongside the model bake-off below. The
      commands are in the comment block at the top of the version-pin section in
      `ser5/ansible/group_vars/all.yml`.
- [ ] **Apply pending OS updates** — 57 packages on ser5, 15 on mini as of
      2026-08-12, including `apparmor 5.0.0~beta1 -> 5.0.2`. Not applied here
      because it wants a reboot window, and mini reboots mean reloading ~45 GB of
      weights.
- [ ] **Decide about Hermes.** It is the one component that CANNOT be pinned —
      the NousResearch installer always fetches latest, so every converge is an
      unreviewed upgrade. Installed 2026.7.20; upstream 2026.8.3. Compounds the
      unresolved Tier-L/egress question below.

## Scale-mismatch check-in (recurring, not a one-time fix)

Not an action item so much as a standing question worth asking at each
quarterly bake-off alongside the model comparison: is the two-goal split
(measured quality / consulting foundation) still the right allocation of
effort, or has one goal been absorbing the other's time again? Write the
answer down; if it's "yes, drifted," that's what triggers the freeze above.

## Housekeeping from this session

- [ ] Double-check nothing outside this repo (personal notes, other configs)
      still refers to the `ser5/ansible/roles/workstation` name — it's now
      `devtools`.
- [ ] Confirm ai-workstation's own roadmap/README is the place the removed
      maintenance-loop list (inbox triage, contradiction sweep, resurfacing)
      actually lives — link to it from here once it does, rather than leaving
      a dangling reference.

## Retirement tasks — delete after all hosts converge past them

- [ ] `ser5/ansible/roles/observability/tasks/main.yml` — "Retire pre-quadlet static units
      that shadow the generator". Added 2026-08-03. Applied to ser5 by hand already;
      keep until any rebuilt host has run it once.
- [ ] `mini/ansible/roles/llamacpp` — legacy llama-server unit retirement.
- [ ] `ser5/ansible/roles/hermes` — quality-gate skill removal (confirmed removed on ser5).

## Oneshot network units report success forever (found 2026-08-05)

- [ ] **Add a `podman network exists` guard to `roles/observability`.** A quadlet
      `.network` generates a oneshot unit that creates the network, exits 0, and
      then reports `active (exited)` indefinitely. Nothing re-checks it. On
      2026-08-05 podman's storage was reset at 22:18 the previous night — every
      network vanished — while `obs-network.service` still showed success from
      18:30. Prometheus, Grafana and Open WebUI were all down for ~10 hours;
      neither `systemctl` nor a converge showed anything wrong. It surfaced only
      because a converge happened to restart Grafana and the handler failed with
      `unable to find network with name or ID systemd-obs`.

      Fix shape: a task that runs `podman network exists systemd-obs` and
      restarts `obs-network.service` when it does not. Same for
      `systemd-openwebui`. This is the third variant of one pattern — see
      "Verify running state, not converge output"; green Ansible and green
      systemd can both sit on top of a dead service.

      What caused the reset is unknown. No `prune`/`reset` in bash history, no
      prune task in any role, no prune timer on the box. Left unattributed
      deliberately rather than guessed at.

## Watch for

- [ ] Quadlet shadowing: a stale `~/.config/systemd/user/<name>.service` silently wins over
      a quadlet-generated unit of the same name, and a converge cannot detect it — Ansible
      and `systemctl restart` both report success while the old image keeps running. Check
      with `systemctl --user show <unit> -p FragmentPath --value`; anything not under
      `.../systemd/generator/` is shadowed. Audited 2026-08-03: only observability was
      affected; openwebui and both llama-server units are clean.

## Governance — Hermes is not Tier L (found 2026-08-04, UNRESOLVED)

- [ ] **Decide where Hermes routes.** Verified by querying the running service, not
      by reading config: `hermes_ollama_base_url` is still `http://mini:11434`, which
      is DEAD because Ollama is now stopped. Hermes did not fall back to mini's
      llama-server — `hermes-proxy` on `:8645` answers `/v1/models` with **292
      OpenRouter models**, against a configured `OPENROUTER_API_KEY`.

      This matters because `docs/operating-manual.md` routed "away from a computer"
      work to Hermes as Tier L, and Tier L means "data never leaves controlled
      hardware." Anything driven through the gateway, proxy or dashboard has been
      able to egress to a third party. The manual and `ser5/README.md` are corrected;
      the ROUTE is not.

      **Route reconfigured 2026-08-14 — options 1 and 2 are moot.** The premise
      above ("Hermes speaks only `OLLAMA_BASE_URL`") was true of the build installed
      on ser5, not of Hermes. Current Hermes has a first-class `custom` provider for
      any endpoint serving `/v1/chat/completions`:
      https://hermes-agent.nousresearch.com/docs/integrations/providers

      `roles/hermes` now writes `model.provider=custom`,
      `model.base_url=http://mini:8090/v1`, `model.default=qwen3.6-35b-a3b-mtp` and
      `model.context_length=262144` via `hermes config set`, and the dead
      `OLLAMA_BASE_URL` is gone from all three unit templates. No Ollama residency,
      no shim.

- [x] **APPLIED AND VERIFIED ON THE BOX 2026-08-14 19:35.** Set directly over SSH
      with `hermes config set`; a later `make provision` is a no-op on these keys.
      config.yaml now reads provider `custom`, base_url `http://mini:8090/v1`,
      default `qwen3.6-35b-a3b-mtp`, context_length `262144`.

      Proof is end-to-end, not from config: `hermes chat -q "..." -Q` returned the
      answer while mini's own counters moved — `prompt_tokens_total` 20779→20797,
      `tokens_predicted_total` 20→48, `n_decode_total` 19→29. Hermes v0.19.0 does
      support `custom` (980 refs in the installed source tree).

      Left in place: `model.ollama_num_ctx: 192000`, an inert Ollama-era leftover.

- [ ] **Three findings on the box that contradict the 2026-08-04 note above.**
      1. The route was NOT OpenRouter. It was `provider: xai-oauth`,
         `base_url: https://api.x.ai/v1` — egress to **xAI**, a different third
         party than recorded. Still not Tier L, but the note named the wrong one.
      2. `model.default` was `qwen3.6:35b-a3b-mtp-q4_K_M`, an Ollama-style name
         mini's llama-server never advertised (it serves `qwen3.6-35b-a3b-mtp`).
         So the model name was wrong too, not just the endpoint.
      3. **`hermes proxy` is a NOUS PORTAL proxy**, not a proxy for the configured
         model — "Forwarding to: (resolved per-request from your subscription)".
         It reads neither `model.provider` nor `base_url`. The "292 OpenRouter
         models" reading was therefore never evidence about the default route.

- [ ] **hermes-proxy is broken independent of routing.** It exits 2 with "Not
      logged into Nous Portal. Run `hermes auth add nous` first." — there is no
      `nous` credential in `hermes auth list`. Confirmed by A/B: it fails
      identically with provider `custom` and provider `xai-oauth`. It had been
      "active" only because it was last started long ago; it dies on ANY restart,
      so the next reboot would have taken it out regardless. Stopped 2026-08-14 to
      end a 15s restart loop (6 restarts), left ENABLED so it returns once fixed.
      Fixing needs the interactive `hermes auth add nous` device-code flow.

- [ ] **Decide what happens to the FOUR hosted credentials.** THIS IS THE REMAINING
      GOVERNANCE ITEM and the routing change does not resolve it. `hermes auth list`
      on the box shows live credentials for:

        openrouter     OPENROUTER_API_KEY
        opencode-zen   OPENCODE_ZEN_API_KEY
        copilot        COPILOT_GITHUB_TOKEN
        xai-oauth      device_code  ← was the active default provider until today

      Setting a default model does not remove a route. Each of these is reachable
      on fallback, on an explicit `--provider`, or on a `-m` naming a hosted model.
      mini is wifi-only with a known powersave problem, so "mini unreachable" is
      routine rather than hypothetical. Either remove them (Hermes hard-fails when
      mini is down — the correct behaviour for Tier L) or keep them and accept
      Hermes as Tier X.

      Note `fallback_providers: []` is already empty, which helps but is not the
      same as having no credentials.

      Until that is decided, treat Hermes as third-party-egress.

## Backups — turned on 2026-08-05 (was off since the box was built)

Found unconfigured at 08:52 on 2026-08-05: `restic` not installed, `/etc/restic`
absent, `/data/backups` empty since the Jun 19 disk-setup mkdir, no timer loaded
— `enable_backups: false`. Resolved the same morning: the flag was flipped,
`vault_restic_password` set, and ser5 converged. Verified on the box at 10:20:
`restic 0.18.1` installed, `/etc/restic/restic.env` present, repo initialised at
`/data/backups` (config/data/index/keys/locks), `restic-backup.timer` scheduled
for 03:43 daily.

Note for anyone reading the repo's older comments: every mention of what restic
"retains" — including the one in `roles/backups/defaults/main.yml` about
agentlab, and the `/data/agentlab` item below — described an intention rather
than a state until this date. Nothing was in restic before 2026-08-05.

- [ ] **Confirm the first snapshot completed and is restorable.** The repo was
      initialised at 08:50 but `/data/backups/snapshots/` was still empty at
      10:20 — initialising a repo is not taking a backup. A run was triggered by
      hand; check `sudo bash -c 'source /etc/restic/restic.env && restic
      snapshots'` shows one, then actually restore a file from it. An untested
      backup is a belief, not a backup.

- [ ] **Add an off-site tier.** `restic_repo` is `/data/backups` — the same
      physical box, though a different disk (`sda1`, separate from the OS on
      `nvme0n1p2`). That survives an OS wipe and nothing else: not disk failure,
      theft, or fire. The role's own header says to pair it with B2/S3/rclone.

- [ ] **Record both passwords outside this control node.** The restore chain is
      ansible-vault password (in your head) → `ser5/ansible/group_vars/vault.yml`
      (AES256, gitignored at `ser5/.gitignore:7`, exists only on the WSL control
      node) → restic password → `/data/backups`. If the control node dies, the
      snapshots survive and the key does not. Password manager, not the repo.

## Housekeeping

- [ ] **Decide on `/data/agentlab` (824K on ser5) — deliberately, not by reflex.**
      Frozen archive of quality-loop's 356 recorded runs (`runs.db`) plus a README
      explaining what it was. Nothing writes to it. It is in restic as of
      2026-08-05 and was NOT before that date, despite this line having claimed
      otherwise. Keep as evidence
      for the "what measures quality" question above, or archive and delete:

      ```sh
      mkdir -p /data/archive && cp /data/agentlab/runs.db /data/archive/qloop-runs-2026-08.db
      rm -rf /data/agentlab
      ```

## Inference tuning — decisions the 2026-08-04 benchmarks point at

Measured with `llama-benchy-suite` on mini; raw results in `/data/bench/llama-benchy`,
summarised in `mini/AGENTS.md`. None of these are applied — each trades something.

- [ ] **Raise `llamacpp_cache_ram` on the quality instance.** Highest-value knob
      found. A warm prefix is worth ~12x on TTFT (depth 32768: 40.2s cold -> 3.3s
      warm), and the 8 GiB llama-server default holds only a handful of ~1 GiB
      deep-context entries — 41 evictions were logged in a single benchmark pass.
      Every eviction is a session that pays the cold price on its next turn.
      Cost: unified memory shared with the GPU pool (box sat at ~72 of ~122 GiB
      during benchmarking). Check `free -g` before choosing a value.

- [ ] **Consider `-np 2` + `llamacpp_mtp: false` on quality.** Three findings
      converge here: quality's aggregate throughput PEAKS at concurrency 2 (91.2
      t/s) and falls to 62.1 at 4; MTP costs 18% at concurrency 2 while gaining
      21% at 1; and fewer slots means more context per chat (262144 rather than
      131072). Cost: two fewer concurrent sessions. This is a capacity decision.
      Do not apply it without deciding what fan-out the box actually needs — the
      last time this repo sized `parallel` to a workload that did not exist, the
      workload was quality-loop and it got deleted.

- [ ] **Experiment with `llamacpp_cache_reuse`.** Defaults to 0 (KV-shift reuse
      disabled). Agent loops frequently change the MIDDLE of an otherwise
      identical prompt (a tool result lands, an edit applies), which is precisely
      the divergence case this handles. Entirely unmeasured — an experiment, not
      a fix.

- [ ] **Re-run the agentic sims against these findings.** `packages/inference-bench`
      measured a workload we invented; llama-benchy measured the server. The
      concurrency peaks disagree with the earlier `agentsim` numbers, and the
      likely reason is prompt shape (pp4096 exact-tg here vs short prompts there).
      Worth reconciling before trusting either for capacity planning.
