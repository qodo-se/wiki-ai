import json
import urllib.error
import urllib.request

from config import UnsafeConfigURLError, assert_url_is_safe, get_all_config


class LLMError(Exception):
    pass


def generate_text(prompt: str) -> str:
    cfg = get_all_config()
    url = cfg["ollama_url"].rstrip("/") + "/api/generate"
    payload = json.dumps(
        {"model": cfg["ollama_chat_model"], "prompt": prompt, "stream": False}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        assert_url_is_safe(url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError, UnsafeConfigURLError) as e:
        raise LLMError(f"could not reach Ollama at {cfg['ollama_url']}: {e}") from e

    text = data.get("response") if isinstance(data, dict) else None
    if not isinstance(text, str):
        raise LLMError(f"Ollama response missing 'response': {data}")
    return text
