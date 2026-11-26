from trailhead.data.legacy import migrate_legacy_dirs


def test_moves_old_folder_when_new_missing(tmp_path):
    old, new = tmp_path / "PortfolioTracker", tmp_path / "Trailhead"
    old.mkdir()
    (old / "portfolio.db").write_text("db")
    assert migrate_legacy_dirs([(old, new)]) == [new]
    assert not old.exists()
    assert (new / "portfolio.db").read_text() == "db"


def test_never_overwrites_existing_new_folder(tmp_path):
    old, new = tmp_path / "PortfolioTracker", tmp_path / "Trailhead"
    old.mkdir()
    (old / "portfolio.db").write_text("old")
    new.mkdir()
    (new / "portfolio.db").write_text("new")
    assert migrate_legacy_dirs([(old, new)]) == []
    assert (new / "portfolio.db").read_text() == "new"
    assert (old / "portfolio.db").read_text() == "old"


def test_noop_without_old_folder(tmp_path):
    assert migrate_legacy_dirs([(tmp_path / "missing", tmp_path / "Trailhead")]) == []
    assert not (tmp_path / "Trailhead").exists()
