import json

from loopkit.evals import Task, extract_answer, load_suite, run_suite, score
from loopkit.storage import RunStore


def test_score_modes():
    assert score(Task("t", "p", "42", "numeric"), "blah blah\nANSWER: 42") == 1.0
    assert score(Task("t", "p", "42", "numeric"), "ANSWER: 41") == 0.0
    assert score(Task("t", "p", "Jupiter", "contains"), "It is jupiter.") == 1.0
    assert score(Task("t", "p", "yes", "exact"), "ANSWER: Yes") == 1.0
    assert score(Task("t", "p", r"\bfoo\b", "regex"), "ANSWER: foo bar") == 1.0


def test_extract_answer_prefers_last_answer_line():
    text = "thinking...\nANSWER: draft\nmore thought\nANSWER: final"
    assert extract_answer(text) == "final"


def test_load_suite_skips_comments(tmp_path):
    suite = tmp_path / "s.jsonl"
    suite.write_text(
        '# comment\n{"id": "a", "prompt": "p?", "expected": "1", "match": "numeric"}\n\n'
    )
    tasks = load_suite(suite)
    assert len(tasks) == 1
    assert tasks[0].id == "a"


def test_run_suite_records_to_store(tmp_path, fake_client_factory, monkeypatch):
    monkeypatch.setenv("LOOPKIT_DATA", str(tmp_path / "data"))
    suite = tmp_path / "s.jsonl"
    suite.write_text(
        json.dumps({"id": "a", "prompt": "1+1?", "expected": "2", "match": "numeric"}) + "\n"
        + json.dumps({"id": "b", "prompt": "2+2?", "expected": "4", "match": "numeric"}) + "\n"
    )
    client = fake_client_factory(["ANSWER: 2", "ANSWER: 5"])
    summary = run_suite(client, suite, strategy="single")
    assert summary["tasks"] == 2
    assert summary["mean_score"] == 0.5

    rows = RunStore().summary(summary["run_id"])
    assert rows[0]["tasks"] == 2
    assert rows[0]["mean_score"] == 0.5
    trace_file = tmp_path / "data" / "traces" / f"{summary['run_id']}.jsonl"
    assert len(trace_file.read_text().splitlines()) == 2


def test_run_suite_injects_playbook_context(tmp_path, fake_client_factory, monkeypatch):
    from loopkit.playbook import Playbook

    monkeypatch.setenv("LOOPKIT_DATA", str(tmp_path / "data"))
    suite = tmp_path / "s.jsonl"
    suite.write_text(json.dumps({"id": "a", "prompt": "q", "expected": "x"}) + "\n")
    pb = Playbook(tmp_path / "pb.md")
    pb.apply_ops("ADD: Always show units.")

    client = fake_client_factory(["ANSWER: x"])
    run_suite(client, suite, strategy="single", playbook=pb)
    system_msg = client.calls[0]["messages"][0]
    assert system_msg["role"] == "system"
    assert "Always show units" in system_msg["content"]
