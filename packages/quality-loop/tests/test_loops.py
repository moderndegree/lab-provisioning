from quality_loop.loops import (
    VERDICT_ANSWERED,
    VERDICT_OFF_TARGET,
    VERDICT_PARTIAL,
    VERDICT_UNSAFE,
    _parse_scope_exceeded,
    _parse_verdict,
    refine,
    single,
)


def test_single_returns_answer(fake_client_factory):
    client = fake_client_factory(["ANSWER: 42"])
    trace = single(client, "meaning of life?")
    assert trace.answer == "ANSWER: 42"
    assert trace.strategy == "single"
    assert trace.accepted
    assert len(trace.steps) == 1


def test_refine_stops_on_answered(fake_client_factory):
    client = fake_client_factory(
        [
            "draft answer",          # generate
            "VERDICT: partial\nSCOPE: in_scope\n1. wrong units",  # critique 1
            "better answer",         # revise
            "VERDICT: answered\nSCOPE: in_scope",     # critique 2
        ]
    )
    trace = refine(client, "task", max_rounds=3)
    assert trace.answer == "better answer"
    assert trace.accepted
    assert trace.classification == VERDICT_ANSWERED
    assert trace.rounds == 2
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique", "revise", "critique"]


def test_refine_stops_immediately_on_answered(fake_client_factory):
    """answered on the first pass must stop before any revise round."""
    client = fake_client_factory(["draft answer", "VERDICT: answered\nSCOPE: in_scope"])
    trace = refine(client, "task", max_rounds=3)
    assert trace.accepted
    assert trace.rounds == 1
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique"]


def test_refine_stops_immediately_on_unsafe(fake_client_factory):
    """unsafe_or_invalid must fail fast — no revise round is spent on it."""
    client = fake_client_factory(
        ["draft answer", "VERDICT: unsafe_or_invalid\nSCOPE: in_scope\n1. fabricates a citation"]
    )
    trace = refine(client, "task", max_rounds=3)
    assert not trace.accepted
    assert trace.classification == VERDICT_UNSAFE
    assert trace.rounds == 1
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique"]  # no revise


def test_refine_verdict_alias_unsafe_without_suffix_still_fails_fast(fake_client_factory):
    """A judge that drops the '_or_invalid' suffix must still fail fast, not
    silently downgrade to partial (that was the confirmed bug from review)."""
    client = fake_client_factory(
        ["draft answer", "VERDICT: unsafe\nSCOPE: in_scope\n1. fabricates a source"]
    )
    trace = refine(client, "task", max_rounds=3)
    assert not trace.accepted
    assert trace.classification == VERDICT_UNSAFE
    assert trace.rounds == 1
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique"]  # no revise


def test_refine_verdict_alias_invalid_still_fails_fast(fake_client_factory):
    client = fake_client_factory(
        ["draft answer", "VERDICT: invalid\nSCOPE: in_scope\n1. wrong facts"]
    )
    trace = refine(client, "task", max_rounds=3)
    assert not trace.accepted
    assert trace.classification == VERDICT_UNSAFE
    assert trace.rounds == 1


def test_refine_gives_up_after_max_rounds(fake_client_factory):
    client = fake_client_factory(
        [
            "draft",
            "VERDICT: partial\nSCOPE: in_scope\n1. bad",
            "v2",
            "VERDICT: partial\nSCOPE: in_scope\n1. still bad",
            "v3",
        ]
    )
    trace = refine(client, "task", max_rounds=2)
    assert trace.answer == "v3"
    assert not trace.accepted
    assert trace.classification == VERDICT_PARTIAL
    assert trace.rounds == 2


def test_refine_persistent_off_target_never_accepts(fake_client_factory):
    client = fake_client_factory(
        [
            "draft",
            "VERDICT: off_target\nSCOPE: in_scope\n1. answers a different question",
            "v2",
            "VERDICT: off_target\nSCOPE: in_scope\n1. still the wrong question",
            "v3",
        ]
    )
    trace = refine(client, "task", max_rounds=2)
    assert not trace.accepted
    assert trace.classification == VERDICT_OFF_TARGET
    assert trace.rounds == 2


def test_refine_stops_when_critique_repeats(fake_client_factory):
    same = "VERDICT: off_target\nSCOPE: in_scope\n1. still the wrong question"
    client = fake_client_factory(["draft", same, "v2", same, "v3"])
    trace = refine(client, "task", max_rounds=3)
    assert not trace.accepted
    assert trace.classification == VERDICT_OFF_TARGET
    # generate + critique + revise + identical critique → stop (no third revise)
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique", "revise", "critique"]
    assert trace.steps[-1].meta.get("stalled") is True


def test_refine_stops_when_answer_unchanged(fake_client_factory):
    client = fake_client_factory(
        [
            "same answer",
            "VERDICT: partial\nSCOPE: in_scope\n1. tweak",
            "same answer",  # revise produces no change
            "VERDICT: answered\nSCOPE: in_scope",  # would not be reached
        ]
    )
    trace = refine(client, "task", max_rounds=3)
    assert trace.answer == "same answer"
    assert not trace.accepted
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique", "revise"]


def test_refine_clamps_max_rounds(fake_client_factory):
    # Distinct critiques so anti-spin "same critique" does not fire early;
    # max_rounds=99 must still clamp to MAX_REFINE_ROUNDS (3).
    client = fake_client_factory(
        ["d0"]
        + sum(
            (
                [f"VERDICT: partial\nSCOPE: in_scope\n1. issue-{i}", f"d{i}"]
                for i in range(1, 6)
            ),
            [],
        )
    )
    trace = refine(client, "task", max_rounds=99)
    assert trace.rounds == 3
    assert not trace.accepted


def test_refine_stops_on_scope_exceeded_even_if_answered(fake_client_factory):
    """A draft can technically cover the ask and still overstep it — e.g. by
    acting on an unconfirmed assumption. SCOPE: exceeded must block delivery
    even when VERDICT says answered, and must not spend a revise round."""
    client = fake_client_factory(
        [
            "draft answer",
            "VERDICT: answered\nSCOPE: exceeded\n1. assumed prod deploy without asking",
        ]
    )
    trace = refine(client, "task", max_rounds=3)
    assert not trace.accepted
    assert trace.scope_exceeded is True
    assert trace.rounds == 1
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique"]  # no revise


def test_refine_scope_in_scope_does_not_block(fake_client_factory):
    client = fake_client_factory(["draft answer", "VERDICT: answered\nSCOPE: in_scope"])
    trace = refine(client, "task", max_rounds=3)
    assert trace.accepted
    assert trace.scope_exceeded is False


def test_refine_scope_missing_line_fails_closed(fake_client_factory):
    """A judge that omits the SCOPE line entirely must fail closed (blocked),
    not silently pass as in_scope — this was the confirmed fail-open bug."""
    client = fake_client_factory(["draft answer", "VERDICT: answered"])
    trace = refine(client, "task", max_rounds=3)
    assert not trace.accepted
    assert trace.scope_exceeded is True


def test_parse_scope_exceeded_fails_closed_on_missing_or_noncompliant_line():
    assert _parse_scope_exceeded("VERDICT: answered\nSCOPE: exceeded\n1. x") == (True, True)
    assert _parse_scope_exceeded("VERDICT: answered\nSCOPE: in_scope") == (False, True)
    # missing line -> fails closed (blocked), not silently in_scope
    assert _parse_scope_exceeded("VERDICT: answered") == (True, False)
    # noncompliant token (not an exact match) -> also fails closed, and must
    # not be treated as a substring/prefix match against "in_scope"
    exceeded, parsed = _parse_scope_exceeded(
        "VERDICT: answered\nSCOPE: in_scope_but_actually_assumed_a_deploy"
    )
    assert exceeded is True
    assert parsed is False


def test_parse_scope_exceeded_tolerates_trailing_punctuation():
    exceeded, parsed = _parse_scope_exceeded("VERDICT: answered\nSCOPE: in_scope.")
    assert (exceeded, parsed) == (False, True)
    exceeded, parsed = _parse_scope_exceeded("VERDICT: answered\nSCOPE: exceeded,")
    assert (exceeded, parsed) == (True, True)


def test_parse_verdict_unparseable_defaults_to_partial():
    label, parsed = _parse_verdict("I have thoughts but forgot the format")
    assert label == VERDICT_PARTIAL
    assert parsed is False


def test_parse_verdict_recognizes_all_labels():
    for label in ("answered", "partial", "off_target", "unsafe_or_invalid"):
        parsed_label, parsed = _parse_verdict(f"VERDICT: {label}\nsome text")
        assert parsed_label == label
        assert parsed is True


def test_parse_verdict_aliases_near_miss_unsafe_labels():
    for label in ("unsafe", "invalid"):
        parsed_label, parsed = _parse_verdict(f"VERDICT: {label}\nsome text")
        assert parsed_label == VERDICT_UNSAFE
        assert parsed is True


def test_trace_token_accounting(fake_client_factory):
    client = fake_client_factory(["a", "VERDICT: answered\nSCOPE: in_scope"])
    trace = refine(client, "task")
    assert trace.total_completion_tokens == 40  # 2 steps × 20 fake tokens
