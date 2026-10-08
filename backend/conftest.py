import pytest

import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    # _embedding_reindex_checked gates the automatic-reindex check to once per
    # process (see db.py), not once per database — without resetting it here, the
    # first test to run it would leave every later test silently skipping the check.
    monkeypatch.setattr(db, "_embedding_reindex_checked", False)
