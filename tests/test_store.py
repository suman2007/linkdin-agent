from agent.store import Store


def test_dedupe_and_cap(tmp_path):
    s = Store(tmp_path / "t.db")
    assert not s.seen("a")
    s.record("a", "liked")
    s.record("b", "skipped")
    s.record("c", "liked")
    assert s.seen("a") and s.seen("b")
    assert s.likes_today() == 2


def test_persists(tmp_path):
    p = tmp_path / "t.db"
    Store(p).record("x", "liked")
    assert Store(p).seen("x")


def test_record_overwrites(tmp_path):
    s = Store(tmp_path / "t.db")
    s.record("x", "skipped")
    s.record("x", "liked")
    assert s.likes_today() == 1
