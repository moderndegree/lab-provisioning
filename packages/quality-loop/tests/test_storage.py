from quality_loop.loops import Step, Trace
from quality_loop.report import render_markdown_summary
from quality_loop.storage import RunStore


def _record(store, suite, strategy, worker, score):
    run_id = store.new_run_id()
    trace = Trace(strategy=strategy, task="t", answer="a", rounds=1, accepted=True)
    store.record(run_id, suite, "task-1", strategy, worker, score, trace)
    return run_id


def _record_with_critique(store, *, judge_model, parsed, scope_parsed):
    run_id = store.new_run_id()
    trace = Trace(strategy="refine", task="t", answer="a", rounds=1, accepted=True)
    trace.steps.append(
        Step(
            kind="critique",
            model=judge_model,
            content="VERDICT: answered\nSCOPE: in_scope",
            meta={"parsed": parsed, "scope_parsed": scope_parsed},
        )
    )
    store.record(run_id, "extraction", "task-1", "refine", "general", 1.0, trace)
    return run_id


def test_matrix_keeps_latest_run_per_combo(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPKIT_DATA", str(tmp_path / "data"))
    store = RunStore()
    _record(store, "extraction", "single", "general", 0.5)
    _record(store, "extraction", "single", "general", 1.0)  # newer run, same combo
    _record(store, "extraction", "refine", "general", 0.75)

    rows = store.matrix()
    assert len(rows) == 2  # one row per (suite, strategy, worker), not per run
    single_row = next(r for r in rows if r["strategy"] == "single")
    assert single_row["mean_score"] == 1.0  # the latest run, not an average


def test_render_markdown_summary_reports_best_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPKIT_DATA", str(tmp_path / "data"))
    store = RunStore()
    _record(store, "extraction", "single", "general", 0.5)
    _record(store, "extraction", "refine", "general", 0.9)

    text = render_markdown_summary(store.matrix())
    assert "extraction" in text
    assert "best strategy is `refine`" in text
    assert "beats baseline" in text


def test_render_markdown_summary_handles_no_runs():
    text = render_markdown_summary([])
    assert "No runs recorded" in text


def test_record_persists_judge_parse_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPKIT_DATA", str(tmp_path / "data"))
    store = RunStore()
    _record_with_critique(store, judge_model="general", parsed=True, scope_parsed=False)

    row = store.db.execute(
        "SELECT judge_model, critique_rounds, verdict_parsed, scope_parsed FROM results"
    ).fetchone()
    assert row == ("general", 1, 1, 0)


def test_record_without_critique_leaves_judge_columns_null(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPKIT_DATA", str(tmp_path / "data"))
    store = RunStore()
    _record(store, "extraction", "single", "general", 1.0)

    row = store.db.execute(
        "SELECT judge_model, critique_rounds, verdict_parsed, scope_parsed FROM results"
    ).fetchone()
    assert row == (None, 0, None, None)


def test_judge_stats_reports_parse_rate_per_judge(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPKIT_DATA", str(tmp_path / "data"))
    store = RunStore()
    _record_with_critique(store, judge_model="general", parsed=True, scope_parsed=True)
    _record_with_critique(store, judge_model="general", parsed=False, scope_parsed=True)
    _record_with_critique(store, judge_model="coder", parsed=True, scope_parsed=True)

    rows = {r["judge_model"]: r for r in store.judge_stats() if r["judge_model"] == "general"}
    general = rows["general"]
    assert general["critiqued_tasks"] == 2
    assert general["verdict_parse_rate"] == 0.5
    assert general["scope_parse_rate"] == 1.0
