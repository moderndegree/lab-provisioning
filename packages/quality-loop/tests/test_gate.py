"""Unit tests for the interactive quality gate (no network)."""

from __future__ import annotations

import json

from quality_loop.gate import (
    MAX_BEST_OF_N,
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


def test_n_too_large(fake_client_factory):
    client = fake_client_factory([])
    result = run_gate(
        client, "task", strategy="best_of_n", n=MAX_BEST_OF_N + 1, skip_preflight=True
    )
    assert result.decision == "FAIL"
    assert result.reason == "n_too_large"
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


def test_refine_accept(fake_client_factory):
    client = fake_client_factory([
        "draft answer",
        "VERDICT: ACCEPT\nLooks good.",
    ])
    result = run_gate(client, "task", strategy="refine", skip_preflight=True)
    assert result.decision == "ACCEPT"
    assert result.accepted is True
    assert result.answer == "draft answer"
    assert result.exit_code == 0


def test_refine_keep_baseline_on_max_rounds(fake_client_factory):
    # generate + 2*(critique REVISE + revise) with rounds=2
    client = fake_client_factory([
        "first draft",
        "VERDICT: REVISE\n1. fix it",
        "second draft",
        "VERDICT: REVISE\n1. still bad",
        "third draft",
    ])
    result = run_gate(
        client, "task", strategy="refine", rounds=2, skip_preflight=True
    )
    assert result.decision == "KEEP_BASELINE"
    assert result.answer == "first draft"
    assert result.baseline == "first draft"
    assert result.exit_code == 0


def test_refine_from_provided_baseline(fake_client_factory):
    client = fake_client_factory([
        "VERDICT: ACCEPT\nok",
    ])
    result = run_gate(
        client,
        "task",
        strategy="refine",
        baseline="seeded draft",
        skip_preflight=True,
    )
    assert result.decision == "ACCEPT"
    assert result.baseline == "seeded draft"
    assert result.answer == "seeded draft"
    assert result.exit_code == 0


def test_best_of_n_accept(fake_client_factory):
    client = fake_client_factory([
        "cand1",
        "cand2",
        "cand3",
        "WINNER: 2 — clearest",
    ])
    result = run_gate(
        client, "task", strategy="best_of_n", n=3, skip_preflight=True
    )
    assert result.decision == "ACCEPT"
    assert result.answer == "cand2"
    assert result.exit_code == 0


def test_best_of_n_unparsed_with_baseline(fake_client_factory):
    client = fake_client_factory([
        "cand1",
        "cand2",
        "cand3",
        "I like the second one",  # no WINNER line
    ])
    result = run_gate(
        client,
        "task",
        strategy="best_of_n",
        n=3,
        baseline="orig",
        skip_preflight=True,
    )
    assert result.decision == "KEEP_BASELINE"
    assert result.answer == "orig"
    assert result.reason == "judge_unparsed"


def test_gate_result_json_contract():
    r = GateResult(
        decision="ACCEPT",
        strategy="refine",
        accepted=True,
        answer="a",
        baseline="b",
        rounds=1,
        tokens=10,
        reason="accepted",
    )
    data = json.loads(r.to_json())
    assert data["decision"] == "ACCEPT"
    assert set(data) >= {
        "decision", "strategy", "accepted", "answer", "baseline",
        "rounds", "tokens", "worker", "judge", "playbook", "reason",
    }


def test_preflight_unreachable():
    assert preflight_reachable("http://127.0.0.1:9/v1", timeout_s=0.2) is False
