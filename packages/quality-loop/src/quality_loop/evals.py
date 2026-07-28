"""Eval runner: measure whether a loop strategy actually beats the baseline.

Task suites are JSONL files, one task per line:

  {"id": "gsm-1", "prompt": "...", "expected": "42", "match": "numeric"}

match modes:
  exact        — normalized string equality
  contains     — expected appears in the answer (case-insensitive)
  regex        — expected is a regex searched in the answer
  numeric      — last number in the answer equals expected (tolerance 1e-6)
  json_schema  — answer contains JSON validated against the task's "schema"
  test         — answer's code block is executed against the task's "tests"

The final answer is extracted from an ANSWER: line when present, so prompts
should ask for one — reasoning models otherwise bury the result mid-prose.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from quality_loop.client import ChatClient
from quality_loop.loops import STRATEGIES, Trace
from quality_loop.playbook import Playbook
from quality_loop.storage import RunStore

ANSWER_SUFFIX = 'End your reply with a line "ANSWER: <final answer>".'
TEST_TIMEOUT_S = 20


@dataclass
class Task:
    id: str
    prompt: str
    expected: str
    match: str = "contains"
    schema: dict | None = None
    tests: str | None = None


def load_suite(path: str | Path) -> list[Task]:
    tasks = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        tasks.append(
            Task(
                id=str(obj.get("id", f"task-{line_no}")),
                prompt=obj["prompt"],
                expected=str(obj.get("expected", "")),
                match=obj.get("match", "contains"),
                schema=obj.get("schema"),
                tests=obj.get("tests"),
            )
        )
    return tasks


def extract_answer(text: str) -> str:
    """Prefer the last 'ANSWER: ...' line; fall back to the whole text."""
    matches = re.findall(r"ANSWER:\s*(.+)", text)
    return matches[-1].strip() if matches else text.strip()


def _last_number(text: str) -> float | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(nums[-1]) if nums else None


def _extract_fenced_block(text: str, lang: str | None = None) -> str | None:
    """Return the content of the first fenced code block, optionally matching a
    language tag (e.g. 'json', 'python'). Falls back to any fenced block."""
    if lang:
        match = re.search(rf"```{lang}\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    match = re.search(r"```(?:\w+)?\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else None


def _extract_json(text: str) -> Any:
    """Find the JSON payload in an answer: fenced ```json block, then the
    extracted ANSWER: line, then the raw text. Raises ValueError if none parse."""
    candidates = []
    fenced = _extract_fenced_block(text, "json")
    if fenced:
        candidates.append(fenced)
    candidates.append(extract_answer(text))
    candidates.append(text.strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    raise ValueError("no valid JSON found in answer")


def validate_schema(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Minimal stdlib JSON-schema-ish validator. Supports: type, required,
    properties, items, enum. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    expected_type = schema.get("type")
    if expected_type:
        py_type = type_map.get(expected_type)
        if py_type is None:
            errors.append(f"{path}: unknown schema type {expected_type!r}")
        elif expected_type == "number" and isinstance(value, bool):
            errors.append(f"{path}: expected number, got boolean")
        elif not isinstance(value, py_type):
            errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
            return errors  # further checks would be meaningless on the wrong type

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum {schema['enum']}")

    if expected_type == "object" and isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                errors.extend(validate_schema(value[key], subschema, f"{path}.{key}"))

    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema, f"{path}[{i}]"))

    return errors


def _run_code_against_tests(code: str, tests: str) -> bool:
    """Write the answer's code plus the task's tests to a temp file and run it
    as a subprocess with a timeout. Passes iff it exits cleanly. Isolated to a
    throwaway temp dir; no network access is granted beyond the default env."""
    with tempfile.TemporaryDirectory(prefix="qloop-test-") as tmpdir:
        script = Path(tmpdir) / "check.py"
        script.write_text(f"{code}\n\n{tests}\n", encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=tmpdir,
                capture_output=True,
                timeout=TEST_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0


def score(task: Task, answer_text: str) -> float:
    if task.match == "json_schema":
        try:
            value = _extract_json(answer_text)
        except ValueError:
            return 0.0
        if task.schema is None:
            return 0.0
        return float(not validate_schema(value, task.schema))
    if task.match == "test":
        if not task.tests:
            return 0.0
        code = _extract_fenced_block(answer_text, "python") or extract_answer(answer_text)
        return float(_run_code_against_tests(code, task.tests))

    answer = extract_answer(answer_text)
    expected = task.expected.strip()
    if task.match == "exact":
        return float(answer.strip().lower() == expected.lower())
    if task.match == "contains":
        return float(expected.lower() in answer.lower())
    if task.match == "regex":
        return float(bool(re.search(expected, answer)))
    if task.match == "numeric":
        got, want = _last_number(answer), _last_number(expected)
        return float(got is not None and want is not None and abs(got - want) < 1e-6)
    raise ValueError(f"unknown match mode: {task.match}")


def run_suite(
    client: ChatClient,
    suite_path: str | Path,
    strategy: str = "single",
    worker: str = "general",
    playbook: Playbook | None = None,
    reflect: bool = False,
    store: RunStore | None = None,
    limit: int | None = None,
    on_result: Callable[[Task, Trace, float], None] | None = None,
    **strategy_kwargs,
) -> dict:
    """Run every task through the strategy; record, optionally reflect."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {sorted(STRATEGIES)}")
    tasks = load_suite(suite_path)
    if limit:
        tasks = tasks[:limit]
    store = store or RunStore()
    run_id = store.new_run_id()
    suite_name = Path(suite_path).stem
    loop_fn = STRATEGIES[strategy]

    scores: list[float] = []
    for task in tasks:
        system = playbook.as_context() or None if playbook else None
        prompt = f"{task.prompt}\n\n{ANSWER_SUFFIX}"
        trace = loop_fn(client, prompt, worker=worker, system=system, **strategy_kwargs)
        task_score = score(task, trace.answer)
        scores.append(task_score)
        store.record(run_id, suite_name, task.id, strategy, worker, task_score, trace)
        if on_result:
            on_result(task, trace, task_score)
        if playbook and reflect:
            steps = "; ".join(f"{s.kind}({s.model})" for s in trace.steps)
            outcome = f"score={task_score} ({'PASS' if task_score >= 1 else 'FAIL'})"
            playbook.reflect(
                client,
                task.prompt,
                f"strategy={strategy}; steps: {steps}; final answer: {extract_answer(trace.answer)[:400]}",
                outcome,
            )

    return {
        "run_id": run_id,
        "suite": suite_name,
        "strategy": strategy,
        "worker": worker,
        "tasks": len(tasks),
        "mean_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
    }
