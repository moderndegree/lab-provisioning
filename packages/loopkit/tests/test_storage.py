from loopkit.loops import Trace
from loopkit.report import render_markdown_summary
from loopkit.storage import RunStore


def _record(store, suite, strategy, worker, score):
    run_id = store.new_run_id()
    trace = Trace(strategy=strategy, task="t", answer="a", rounds=1, accepted=True)
    store.record(run_id, suite, "task-1", strategy, worker, score, trace)
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
