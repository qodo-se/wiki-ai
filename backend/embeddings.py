import json
import urllib.error
import urllib.request

from config import UnsafeConfigURLError, assert_url_is_safe, get_all_config


class EmbeddingError(Exception):
    pass


def embed_text(text: str) -> list[float]:
    cfg = get_all_config()
    url = cfg["ollama_url"].rstrip("/") + "/api/embeddings"
    payload = json.dumps({"model": cfg["ollama_embedding_model"], "prompt": text}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        assert_url_is_safe(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError, UnsafeConfigURLError) as e:
        # ValueError also covers urllib rejecting a malformed/unsupported URL
        # and json.JSONDecodeError (a ValueError subclass) on a non-JSON body.
        raise EmbeddingError(f"could not reach Ollama at {cfg['ollama_url']}: {e}") from e

    embedding = data.get("embedding") if isinstance(data, dict) else None
    if not isinstance(embedding, list) or not embedding:
        raise EmbeddingError(f"Ollama response missing 'embedding': {data}")
    return embedding
