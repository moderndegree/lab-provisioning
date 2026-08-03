"""Unit tests for the interactive quality gate (no network)."""

from __future__ import annotations

import json

from quality_loop.gate import (
    GateResult,
    preflight_reachable,
    run_gate,
)


def test_eviction_risk_heavy(fake_client_factory):
    client = fake_client_factory(["should not be called"])
    result = run_gate(client, "task", worker="heavy", skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "eviction_risk"
    assert result.exit_code == 1
    assert client.calls == []


def test_eviction_risk_judge_alias(fake_client_factory):
    client = fake_client_factory(["nope"])
    result = run_gate(client, "task", judge="judge", skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "eviction_risk"
    assert result.exit_code == 1


def test_best_of_n_is_rejected(fake_client_factory):
    """best_of_n was removed: measured identical mean score to refine at 2.5x the
    tokens (and 7x on the coding suite), so it is not a selectable strategy. A
    caller still passing it should fail loudly rather than silently fall back."""
    client = fake_client_factory([])
    result = run_gate(client, "task", strategy="best_of_n", skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "bad_strategy"
    assert result.exit_code == 1


def test_task_too_large(fake_client_factory):
    client = fake_client_factory([])
    result = run_gate(client, "x" * 12_001, baseline="orig", skip_preflight=True)
    assert result.decision == "SKIP"
    assert result.reason == "task_too_large"
    assert result.answer == "orig"
    assert result.exit_code == 2


def test_mini_down_skips(fake_client_factory, monkeypatch):
    monkeypatch.setattr("quality_loop.gate.preflight_reachable", lambda *a, **k: False)
    client = fake_client_factory([])
    client.base_url = "http://127.0.0.1:9/v1"
    result = run_gate(client, "task", baseline="orig")
    assert result.decision == "SKIP"
    assert result.reason == "mini_down"
    assert result.answer == "orig"
    assert result.exit_code == 2


def test_single_accept(fake_client_factory):
    client = fake_client_factory(["four"])
    client.base_url = "http://example.invalid/v1"
    result = run_gate(client, "2+2", strategy="single", skip_preflight=True)
    assert result.decision == "ACCEPT"
    assert result.answer == "four"
    assert result.exit_code == 0


def test_refine_stops_immediately_when_answered(fake_client_factory):
    """Ask-alignment first: an already-answered draft must accept on round 1,
    without spending a revise round polishing it further."""
    client = fake_client_factory([
        "draft answer",
        "VERDICT: answered\nSCOPE: in_scope",
    ])
    result = run_gate(client, "task", strategy="refine", skip_preflight=True)
    assert result.decision == "ACCEPT"
    assert result.reason == "answered"
    assert result.accepted is True
    assert result.answer == "draft answer"
    assert result.rounds == 1
    assert result.exit_code == 0
    # generate + critique only — no revise call for an already-answered draft
    assert len(client.calls) == 2


def test_refine_one_repair_iteration_for_partial(fake_client_factory):
    """partial -> exactly one targeted revise round, then answered stops it."""
    client = fake_client_factory([
        "draft answer",
        "VERDICT: partial\nSCOPE: in_scope\n1. missing the rollback step",
        "draft answer with rollback step",
        "VERDICT: answered\nSCOPE: in_scope",
    ])
    result = run_gate(client, "task", strategy="refine", rounds=2, skip_preflight=True)
    assert result.decision == "ACCEPT"
    assert result.reason == "answered"
    assert result.answer == "draft answer with rollback step"
    assert result.rounds == 2
    assert len(client.calls) == 4  # generate, critique, revise, critique — exactly one repair


def test_refine_keep_baseline_on_persistent_partial(fake_client_factory):
    # generate + 2*(critique partial + revise) with rounds=2, never converges
    client = fake_client_factory([
        "first draft",
        "VERDICT: partial\nSCOPE: in_scope\n1. fix it",
        "second draft",
        "VERDICT: partial\nSCOPE: in_scope\n1. still missing something else",
        "third draft",
    ])
    result = run_gate(
        client, "task", strategy="refine", rounds=2, skip_preflight=True
    )
    assert result.decision == "KEEP_BASELINE"
    assert result.reason == "max_rounds"
    assert result.answer == "first draft"
    assert result.baseline == "first draft"
    assert result.exit_code == 0


def test_refine_fails_fast_on_persistent_off_target(fake_client_factory):
    """off_target that never converges within the round budget must FAIL,
    not silently KEEP_BASELINE a draft that answers the wrong question. Two
    rounds both judged off_target -> reason carries "_persistent"."""
    client = fake_client_factory([
        "draft about topic A",
        "VERDICT: off_target\nSCOPE: in_scope\n1. task asked about topic B",
        "still about topic A",
        "VERDICT: off_target\nSCOPE: in_scope\n1. still the wrong topic",
        "yet another wrong-topic draft",
    ])
    result = run_gate(client, "task", strategy="refine", rounds=2, skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "off_target_persistent"
    assert result.rounds == 2
    assert result.exit_code == 1
    assert result.answer == "draft about topic A"  # baseline, not the failed drafts
    assert "still the wrong topic" in result.extra["critique"]


def test_refine_fails_on_single_round_off_target_without_persistent_label(fake_client_factory):
    """A single off_target verdict (rounds=1) must FAIL but must NOT claim
    'persistent' — it was only ever judged once, even though the round
    budget still spends one revise attempt (its output is discarded here;
    gate.py always delivers `base` for a FAIL, but the same shared round
    logic is also used by evals/star, which do use that revision)."""
    client = fake_client_factory([
        "draft about topic A",
        "VERDICT: off_target\nSCOPE: in_scope\n1. task asked about topic B",
        "revised but discarded by gate.py",
    ])
    result = run_gate(client, "task", strategy="refine", rounds=1, skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "off_target"
    assert result.rounds == 1
    assert result.exit_code == 1
    assert result.answer == "draft about topic A"  # base, not the discarded revision


def test_refine_fails_fast_on_unsafe_without_extra_revise(fake_client_factory):
    """unsafe_or_invalid must stop on the first critique — no revise round is
    spent trying to fix it, and the gate must FAIL rather than deliver it."""
    client = fake_client_factory([
        "draft answer",
        "VERDICT: unsafe_or_invalid\nSCOPE: in_scope\n1. fabricates a source",
    ])
    result = run_gate(client, "task", strategy="refine", rounds=2, skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "unsafe_or_invalid"
    assert result.exit_code == 1
    assert result.rounds == 1
    assert len(client.calls) == 2  # generate + critique only
    assert "fabricates a source" in result.extra["critique"]


def test_refine_fails_fast_on_near_miss_unsafe_label(fake_client_factory):
    """A judge that writes 'VERDICT: unsafe' (missing the _or_invalid suffix)
    must still fail fast, not silently round-trip through KEEP_BASELINE with
    exit_code=0 — this was the confirmed alias-defeat bug from review."""
    client = fake_client_factory([
        "draft answer",
        "VERDICT: unsafe\nSCOPE: in_scope\n1. fabricates a source",
    ])
    result = run_gate(client, "task", strategy="refine", rounds=2, skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "unsafe_or_invalid"
    assert result.exit_code == 1


def test_refine_fails_on_scope_exceeded_even_when_answered(fake_client_factory):
    """A draft that covers the ask but acted on an unconfirmed assumption or
    added something unrequested must FAIL — same severity as unsafe_or_invalid
    — rather than ACCEPT just because VERDICT says answered."""
    client = fake_client_factory([
        "draft answer",
        "VERDICT: answered\nSCOPE: exceeded\n1. deployed to prod without confirming",
    ])
    result = run_gate(client, "task", strategy="refine", skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "scope_exceeded"
    assert result.exit_code == 1
    assert result.rounds == 1
    assert len(client.calls) == 2  # generate + critique only, no revise
    assert "deployed to prod" in result.extra["critique"]


def test_refine_fails_on_missing_scope_line(fake_client_factory):
    """A judge that omits the SCOPE line must fail closed (blocked), not be
    silently treated as in_scope — this was the confirmed fail-open bug."""
    client = fake_client_factory([
        "draft answer",
        "VERDICT: answered",  # no SCOPE line at all
    ])
    result = run_gate(client, "task", strategy="refine", skip_preflight=True)
    assert result.decision == "FAIL"
    assert result.reason == "scope_exceeded"
    assert result.exit_code == 1


def test_refine_from_provided_baseline_answered(fake_client_factory):
    client = fake_client_factory([
        "VERDICT: answered\nSCOPE: in_scope",
    ])
    result = run_gate(
        client,
        "task",
        strategy="refine",
        baseline="seeded draft",
        skip_preflight=True,
    )
    assert result.decision == "ACCEPT"
    assert result.reason == "answered"
    assert result.baseline == "seeded draft"
    assert result.answer == "seeded draft"
    assert result.exit_code == 0


def test_refine_from_provided_baseline_unsafe_fails_fast(fake_client_factory):
    client = fake_client_factory([
        "VERDICT: unsafe_or_invalid\nSCOPE: in_scope\n1. leaks a credential",
    ])
    result = run_gate(
        client,
        "task",
        strategy="refine",
        baseline="seeded draft",
        skip_preflight=True,
    )
    assert result.decision == "FAIL"
    assert result.reason == "unsafe_or_invalid"
    assert result.answer == "seeded draft"
    assert result.exit_code == 1


def test_gate_result_json_contract():
    r = GateResult(
        decision="ACCEPT",
        strategy="refine",
        accepted=True,
        answer="a",
        baseline="b",
        rounds=1,
        tokens=10,
        reason="answered",
    )
    data = json.loads(r.to_json())
    assert data["decision"] == "ACCEPT"
    assert set(data) >= {
        "decision", "strategy", "accepted", "answer", "baseline",
        "rounds", "tokens", "worker", "judge", "playbook", "reason",
    }


def test_preflight_unreachable():
    assert preflight_reachable("http://127.0.0.1:9/v1", timeout_s=0.2) is False
