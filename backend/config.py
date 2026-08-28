from db import connect

DEFAULTS = {
    "ollama_url": "http://host.docker.internal:11434",
    "ollama_embedding_model": "nomic-embed-text",
    "qdrant_url": "http://qdrant:6333",
    "qdrant_collection": "notes",
}


def get_all_config() -> dict[str, str]:
    with connect() as db:
        rows = db.execute("SELECT key, value FROM config").fetchall()
    values = dict(DEFAULTS)
    values.update({key: value for key, value in rows})
    return values


def get_config(key: str) -> str:
    return get_all_config()[key]


def set_config(updates: dict[str, str]) -> dict[str, str]:
    unknown = set(updates) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown config key(s): {', '.join(sorted(unknown))}")
    with connect() as db:
        db.executemany(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(updates.items()),
        )
    return get_all_config()
