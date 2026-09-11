import json
import urllib.error
import urllib.request

from config import UnsafeConfigURLError, assert_url_is_safe, get_all_config


class VectorStoreError(Exception):
    pass


def _cfg():
    cfg = get_all_config()
    return cfg["qdrant_url"].rstrip("/"), cfg["qdrant_collection"]


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    base_url, _ = _cfg()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base_url + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        assert_url_is_safe(req.full_url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError, UnsafeConfigURLError) as e:
        # ValueError also covers urllib rejecting a malformed/unsupported URL
        # and json.JSONDecodeError (a ValueError subclass) on a non-JSON body.
        raise VectorStoreError(f"could not reach Qdrant at {base_url}: {e}") from e


def _existing_vector_size(collection_info: dict) -> int | None:
    try:
        return collection_info["result"]["config"]["params"]["vectors"]["size"]
    except (KeyError, TypeError):
        return None


def _ensure_collection(vector_size: int) -> None:
    _, collection = _cfg()
    status, resp = _request("GET", f"/collections/{collection}")
    if status == 200:
        existing_size = _existing_vector_size(resp)
        if existing_size is not None and existing_size != vector_size:
            raise VectorStoreError(
                f"Qdrant collection '{collection}' stores {existing_size}-dimension vectors, "
                f"but the current embedding model produced {vector_size} dimensions — likely "
                f"because ollama_embedding_model was changed. Point qdrant_collection at a new "
                f"name for this model (and re-run the backfill), or switch the model back."
            )
        return
    status, resp = _request(
        "PUT",
        f"/collections/{collection}",
        {"vectors": {"size": vector_size, "distance": "Cosine"}},
    )
    if status not in (200, 201):
        raise VectorStoreError(f"could not create Qdrant collection: {resp}")


def upsert_vector(point_id: str, vector: list[float], payload: dict | None = None) -> None:
    _ensure_collection(len(vector))
    _, collection = _cfg()
    status, resp = _request(
        "PUT",
        f"/collections/{collection}/points",
        {"points": [{"id": point_id, "vector": vector, "payload": payload or {}}]},
    )
    if status not in (200, 201):
        raise VectorStoreError(f"could not upsert vector: {resp}")


def delete_vector(point_id: str) -> None:
    _, collection = _cfg()
    status, resp = _request(
        "POST",
        f"/collections/{collection}/points/delete",
        {"points": [point_id]},
    )
    if status not in (200, 201):
        raise VectorStoreError(f"could not delete vector: {resp}")


def search_vectors(vector: list[float], limit: int) -> list[dict]:
    _, collection = _cfg()
    status, resp = _request(
        "POST",
        f"/collections/{collection}/points/search",
        {"vector": vector, "limit": limit, "with_payload": True},
    )
    if status == 404:
        return []  # collection doesn't exist yet — nothing has been embedded
    if status != 200:
        raise VectorStoreError(f"could not search vectors: {resp}")
    result = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(result, list):
        raise VectorStoreError(f"unexpected Qdrant search response: {resp}")
    return result
