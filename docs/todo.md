# TODO — open items from the 2026-07-28 platform review

Working list from a critical pass over intent and implementation. Not a
roadmap phase — this is the punch list for things flagged as risks or loose
ends, separate from planned feature work. Check items off in place; delete
the whole doc once it's empty rather than let it fossilize.

## Operational (do first — a real service is affected)

- [ ] **Tear down Open WebUI on mini by hand.** The role moved to ser5 this
      session; Ansible no longer manages the mini-side deployment, so a stale
      systemd unit + container may still be running there. On mini:
      ```
      systemctl --user stop openwebui.service && systemctl --user disable openwebui.service
      rm ~/.config/containers/systemd/openwebui.container
      podman rm -f openwebui   # if still present
      ```
- [ ] Set `enable_openwebui: true` in `ser5/ansible/group_vars/all.yml` and
      `make ser5-provision` to bring it up on ser5 instead, if still wanted.
      Verify `http://ser5:8080` reaches mini's Ollama before relying on it.
- [ ] Re-run `make mini-provision` once to confirm `all.yml` (openwebui vars
      removed) converges cleanly with nothing orphaned.

## Goal balance — the repo is carrying two priorities that compete for time

Everything below exists because Phase 3/4-shaped work (second-brain wiring,
business-layer tiers, Hermes surfaces) got built ahead of Phase 1's own
acceptance criteria (a full baseline matrix + one-page quality summary from
`runs.db`), even though the roadmap's own sequencing says measurement comes
first.

- [ ] **Freeze Phase 4 (consulting productization)** — no new tier
      enforcement, client isolation, or demo-surface work — until there is a
      concrete prospect or pilot engagement to build it for. Design docs are
      fine; new code isn't.
- [ ] **Finish Phase 1 before starting new subsystems.** Acceptance is
      already written in `roadmap.md`: baseline matrix in `runs.db` across
      {single, refine, best_of_n} × {general, coder}, plus a one-page summary
      generated from it. Nothing in Phase 2+ should start until `qloop stats`
      can actually produce that.
- [ ] Adopt a cheap self-check: before starting a session's work, name which
      goal (measured quality vs. consulting foundation) it serves. If it's
      neither, ask whether it needs to happen now. This is a habit, not a
      script — no tooling required.

## Measurement gap — the judge is a single point of failure with no alarm

`gate.py`/`loops.py` parse a `VERDICT:`/`SCOPE:` line out of the same class of
model being graded. The defensive parsing (fail-closed on unparsed SCOPE,
alias handling for near-miss labels) is solid, but nothing tracks whether the
judge is *actually following the format* over time — a future judge-model
swap (the quarterly bake-off) could silently degrade gate accuracy with
nothing to catch it.

- [ ] Persist verdict/scope parse success (`parsed`, `scope_parsed` — already
      computed in `_refine_round`'s critique step metadata) into `runs.db` as
      a first-class column, not just trace metadata that gets discarded.
- [ ] Add a `qloop stats` view: judge parse rate over time, sliceable by
      judge model. This is what makes a bake-off candidate's *reliability as
      a judge* visible, not just its answer quality.
- [ ] When Phase 5's independent-judge work (`nemotron-cascade-2` grading
      `best_of_n`) lands, wire the same parse-rate tracking through it —
      don't let a second judge path silently repeat this gap.

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
