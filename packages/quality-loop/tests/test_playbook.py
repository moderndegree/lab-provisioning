from quality_loop.playbook import Playbook


def test_apply_ops_add_update_remove(tmp_path):
    pb = Playbook(tmp_path / "pb.md")
    applied = pb.apply_ops("ADD: Check units before answering.\nADD: Restate the question first.")
    assert len(applied) == 2
    assert pb.bullets[0].text == "Check units before answering."

    pb.apply_ops("UPDATE 1: Always check units before answering.")
    assert pb.bullets[0].text == "Always check units before answering."

    pb.apply_ops("REMOVE 2")
    assert len(pb.bullets) == 1
    assert pb.bullets[0].number == 1  # renumbered


def test_no_changes_is_a_noop(tmp_path):
    pb = Playbook(tmp_path / "pb.md")
    assert pb.apply_ops("NO_CHANGES") == []
    assert pb.bullets == []


def test_duplicate_adds_are_rejected(tmp_path):
    pb = Playbook(tmp_path / "pb.md")
    pb.apply_ops("ADD: Check units.")
    applied = pb.apply_ops("ADD: check units!")  # same after normalization
    assert applied == []
    assert len(pb.bullets) == 1


def test_max_bullets_cap(tmp_path):
    pb = Playbook(tmp_path / "pb.md", max_bullets=2)
    pb.apply_ops("ADD: one\nADD: two\nADD: three")
    assert len(pb.bullets) == 2


def test_roundtrip_save_load(tmp_path):
    path = tmp_path / "pb.md"
    pb = Playbook(path)
    pb.apply_ops("ADD: Sanity-check edge cases.\nADD: Prefer exact arithmetic.")
    pb.bullets[0].helpful = 3
    pb.save()

    reloaded = Playbook(path)
    assert [b.text for b in reloaded.bullets] == [
        "Sanity-check edge cases.",
        "Prefer exact arithmetic.",
    ]
    assert reloaded.bullets[0].helpful == 3
    assert "Playbook of tactics" in reloaded.as_context()


def test_reflect_applies_and_saves(tmp_path, fake_client_factory):
    client = fake_client_factory(["ADD: Convert times to minutes before subtracting."])
    pb = Playbook(tmp_path / "pb.md")
    applied = pb.reflect(client, "time math task", "failed on units", "score=0 (FAIL)")
    assert applied
    assert (tmp_path / "pb.md").exists()
    assert "Convert times" in Playbook(tmp_path / "pb.md").bullets[0].text
