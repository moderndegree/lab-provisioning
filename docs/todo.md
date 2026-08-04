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

## Goal balance — the repo is carrying two priorities that compete for time

Everything below exists because Phase 3/4-shaped work (second-brain wiring,
business-layer tiers, Hermes surfaces) got built ahead of Phase 1's own
acceptance criteria (a full baseline matrix + one-page quality summary from
`runs.db`), even though the roadmap's own sequencing says measurement comes
first.

- [x] **Freeze Phase 4 (consulting productization)** — decided 2026-07-28,
      recorded durably in `roadmap.md` (status note + a FROZEN marker on the
      Phase 4 section itself) rather than living only here.
- [x] **Finish Phase 1 before starting new subsystems** — same decision,
      same place. The baseline matrix was never produced
      (see Measurement gap below for the piece that moved); the constraint
      itself is now recorded in `roadmap.md`, not just this list.
- [x] Adopt a cheap self-check — habit adopted 2026-07-28, no tooling to
      build.

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
