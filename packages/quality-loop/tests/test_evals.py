import json

from quality_loop.evals import Task, extract_answer, load_suite, run_suite, score
from quality_loop.storage import RunStore


def test_score_modes():
    assert score(Task("t", "p", "42", "numeric"), "blah blah\nANSWER: 42") == 1.0
    assert score(Task("t", "p", "42", "numeric"), "ANSWER: 41") == 0.0
    assert score(Task("t", "p", "Jupiter", "contains"), "It is jupiter.") == 1.0
    assert score(Task("t", "p", "yes", "exact"), "ANSWER: Yes") == 1.0
    assert score(Task("t", "p", r"\bfoo\b", "regex"), "ANSWER: foo bar") == 1.0


def test_extract_answer_prefers_last_answer_line():
    text = "thinking...\nANSWER: draft\nmore thought\nANSWER: final"
    assert extract_answer(text) == "final"


def test_score_json_schema_mode():
    schema = {
        "type": "object",
        "required": ["name", "age"],
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    }
    task = Task("t", "p", "", "json_schema", schema=schema)
    good = 'reasoning...\n```json\n{"name": "Ana", "age": 30}\n```\nANSWER: done'
    assert score(task, good) == 1.0

    missing_field = 'ANSWER: {"name": "Ana"}'
    assert score(task, missing_field) == 0.0

    wrong_type = 'ANSWER: {"name": "Ana", "age": "thirty"}'
    assert score(task, wrong_type) == 0.0

    not_json = "ANSWER: sorry, no JSON here"
    assert score(task, not_json) == 0.0


def test_score_test_mode():
    tests = "assert add(2, 3) == 5\nassert add(-1, 1) == 0\n"
    task = Task("t", "p", "", "test", tests=tests)
    passing = "```python\ndef add(a, b):\n    return a + b\n```\nANSWER: done"
    assert score(task, passing) == 1.0

    failing = "```python\ndef add(a, b):\n    return a - b\n```\nANSWER: done"
    assert score(task, failing) == 0.0


def test_load_suite_carries_schema_and_tests(tmp_path):
    suite = tmp_path / "s.jsonl"
    suite.write_text(
        json.dumps({
            "id": "a", "prompt": "p?", "match": "json_schema",
            "schema": {"type": "object", "required": ["x"]},
        }) + "\n"
        + json.dumps({
            "id": "b", "prompt": "p?", "match": "test",
            "tests": "assert True",
        }) + "\n"
    )
    tasks = load_suite(suite)
    assert tasks[0].schema == {"type": "object", "required": ["x"]}
    assert tasks[1].tests == "assert True"


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
    from quality_loop.playbook import Playbook

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
