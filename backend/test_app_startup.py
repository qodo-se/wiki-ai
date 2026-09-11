import importlib

import pytest

import db


def _reload_app():
    import app

    return importlib.reload(app)


def test_app_boots_with_compatible_schema():
    module = _reload_app()
    assert module.app.title == "wiki"


def test_app_fails_to_boot_with_incompatible_schema(monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [])
    conn = db.connect()
    conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
    conn.commit()
    conn.close()

    with pytest.raises(db.SchemaTooNewError):
        _reload_app()
