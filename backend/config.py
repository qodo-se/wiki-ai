import ipaddress
import socket
import urllib.request
from urllib.parse import urlparse

from db import connect

DEFAULTS = {
    "ollama_url": "http://host.docker.internal:11434",
    "ollama_embedding_model": "nomic-embed-text",
    "ollama_chat_model": "gemma4:e4b",
    "qdrant_url": "http://qdrant:6333",
    "qdrant_collection": "notes",
    "categorize_neighbor_limit": "5",
}


class UnsafeConfigURLError(Exception):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    # ollama_url/qdrant_url are user-configurable; without this, a host that
    # passes resolves_to_link_local could still redirect requests onward to
    # one, bypassing the check entirely.
    def redirect_request(self, *args, **kwargs):
        return None


urllib.request.install_opener(urllib.request.build_opener(_NoRedirectHandler))


def _is_link_local(raw_ip: str) -> bool:
    ip = ipaddress.ip_address(raw_ip)
    # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) still routes to
    # the embedded IPv4 target, but IPv6Address.is_link_local doesn't look
    # at the mapped address — check it explicitly.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_link_local


def resolves_to_link_local(hostname: str) -> bool:
    # Blocks pivoting configured outbound URLs (ollama_url/qdrant_url) at
    # cloud metadata endpoints (169.254.169.254 and friends), which all live
    # in the link-local range. Private-network hosts — the intended target
    # for both fields — are deliberately left unrestricted.
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    return any(_is_link_local(sockaddr[0]) for *_, sockaddr in infos)


def assert_url_is_safe(url: str) -> None:
    # Re-checked at request time (not only when the URL was saved) so a
    # hostname that resolved safely at config-save time but has since been
    # repointed at a link-local address can't slip through.
    hostname = urlparse(url).hostname
    if hostname and resolves_to_link_local(hostname):
        raise UnsafeConfigURLError(f"refusing to contact link-local host {hostname!r}")


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
