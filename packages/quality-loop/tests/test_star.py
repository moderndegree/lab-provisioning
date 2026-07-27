import json

from quality_loop.star import bootstrap


def _suite(tmp_path):
    suite = tmp_path / "s.jsonl"
    suite.write_text(
        json.dumps({"id": "a", "prompt": "1+1?", "expected": "2", "match": "numeric"}) + "\n"
        + json.dumps({"id": "b", "prompt": "hardest?", "expected": "7", "match": "numeric"}) + "\n"
    )
    return suite


def test_bootstrap_keeps_correct_and_rationalizes(tmp_path, fake_client_factory):
    # a: correct first try; b: wrong, then correct with hint → rationalized
    client = fake_client_factory(["ANSWER: 2", "ANSWER: 9", "reasoning...\nANSWER: 7"])
    out = tmp_path / "sft.jsonl"
    summary = bootstrap(client, _suite(tmp_path), out, rationalize=True)
    assert summary == {"tasks": 2, "kept": 2, "rationalized": 1, "failed": 0, "out": str(out)}

    examples = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(examples) == 2
    # hint must never leak into the training prompt
    assert "Hint" not in examples[1]["messages"][0]["content"]
    assert examples[1]["messages"][1]["role"] == "assistant"


def test_bootstrap_without_rationalization_drops_failures(tmp_path, fake_client_factory):
    client = fake_client_factory(["ANSWER: 2", "ANSWER: 9"])
    out = tmp_path / "sft.jsonl"
    summary = bootstrap(client, _suite(tmp_path), out, rationalize=False)
    assert summary["kept"] == 1
    assert summary["failed"] == 1
