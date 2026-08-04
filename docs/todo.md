# TODO — open items from the 2026-07-28 platform review

Working list from a critical pass over intent and implementation. Not a
roadmap phase — this is the punch list for things flagged as risks or loose
ends, separate from planned feature work. Check items off in place; delete
the whole doc once it's empty rather than let it fossilize.

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

## Measurement gap — the judge is a single point of failure with no alarm

`gate.py`/`loops.py` parse a `VERDICT:`/`SCOPE:` line out of the same class of
model being graded. The defensive parsing (fail-closed on unparsed SCOPE,
alias handling for near-miss labels) is solid, but nothing tracks whether the
judge is *actually following the format* over time — a future judge-model
swap (the quarterly bake-off) could silently degrade gate accuracy with
nothing to catch it.

- [x] Judge parse-rate tracking (verdict/scope columns in `runs.db`, `qloop stats
      --judge-parse`). Done 2026-07-28, then **superseded**: quality-loop was
      removed entirely 2026-08. The 356 recorded runs survive as a frozen
      archive at `/data/agentlab/runs.db` on ser5; nothing writes to it now.
      Serving-side measurement moved to `packages/inference-bench`.

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
