from quality_loop.loops import best_of_n, refine, single


def test_single_returns_answer(fake_client_factory):
    client = fake_client_factory(["ANSWER: 42"])
    trace = single(client, "meaning of life?")
    assert trace.answer == "ANSWER: 42"
    assert trace.strategy == "single"
    assert trace.accepted
    assert len(trace.steps) == 1


def test_refine_stops_on_accept(fake_client_factory):
    client = fake_client_factory(
        [
            "draft answer",          # generate
            "VERDICT: REVISE\n1. wrong units",  # critique 1
            "better answer",         # revise
            "VERDICT: ACCEPT",       # critique 2
        ]
    )
    trace = refine(client, "task", max_rounds=3)
    assert trace.answer == "better answer"
    assert trace.accepted
    assert trace.rounds == 2
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["generate", "critique", "revise", "critique"]


def test_refine_gives_up_after_max_rounds(fake_client_factory):
    client = fake_client_factory(
        ["draft", "VERDICT: REVISE\n1. bad", "v2", "VERDICT: REVISE\n1. still bad", "v3"]
    )
    trace = refine(client, "task", max_rounds=2)
    assert trace.answer == "v3"
    assert not trace.accepted
    assert trace.rounds == 2


def test_best_of_n_picks_judged_winner(fake_client_factory):
    client = fake_client_factory(["cand-1", "cand-2", "cand-3", "WINNER: 2 — most correct"])
    trace = best_of_n(client, "task", n=3)
    assert trace.answer == "cand-2"
    assert trace.accepted
    # candidates sampled with distinct seeds for diversity
    seeds = [c.get("seed") for c in client.calls[:3]]
    assert seeds == [0, 1, 2]


def test_best_of_n_defaults_to_first_when_judge_unparseable(fake_client_factory):
    client = fake_client_factory(["c1", "c2", "no idea"])
    trace = best_of_n(client, "task", n=2)
    assert trace.answer == "c1"
    assert not trace.accepted


def test_trace_token_accounting(fake_client_factory):
    client = fake_client_factory(["a", "VERDICT: ACCEPT"])
    trace = refine(client, "task")
    assert trace.total_completion_tokens == 40  # 2 steps × 20 fake tokens
