# wiki-mcp

An MCP server exposing wiki-ai's HTTP API as tools, 1:1 with [`wiki-cli`](../cli)'s
command set. Built on the official `mcp` Python SDK, served over streamable
HTTP (not stdio), so it can run detached and be reached by other containers.

## Tools

| Tool | wiki-cli equivalent |
|---|---|
| `list_notes(limit, offset, scope)` | `list` |
| `get_note(note_id)` | `get` |
| `get_note_meta(note_id)` | `meta` |
| `create_note(content, path, kind)` | `create` |
| `update_note(note_id, content, path=None)` | `update` |
| `delete_note(note_id)` | `delete` |
| `search_notes(query, limit, offset, scope)` | `search` |
| `search_notes_hybrid(query, limit, offset, scope)` | `search-hybrid` |
| `search_containers(query, limit, offset)` | `search --scope containers` |
| `get_note_ancestors(note_id)` | `ancestors` |
| `get_note_children(note_id, limit, offset)` | `children` |
| `set_note_parent(note_id, parent_id=None)` | `set-parent` |
| `get_config()` | `config get` |
| `set_config(...)` | `config set` |
| `reindex()` | `reindex` |
| `create_backup()` | `backup` |
| `upload_image(note_id, filename, content_base64)` | `upload-image` |

### The knowledge tree

Notes form a tree. A **container** is a note that groups other notes and whose
body says what that grouping *is*; a note has at most one parent, which must
be a container. Containers are kept out of ordinary note results, so
`list_notes` and the search tools never surface them unless `scope` asks for
them (`"notes"` by default, or `"containers"` / `"all"`).

`get_note_ancestors` is the tool that earns the feature. A note filed under
something called `UH-100` is uninterpretable on its own; walking up returns
each container's prose — that UH-100 was a 2018 college course, say, making
its notes historical rather than current — which is exactly the context a
model needs before judging whether a note is relevant or current.

`list_notes(scope="containers")` returns every container with its `parent_id`,
so the whole tree skeleton arrives in one call with no recursive traversal.

Two tools deliberately don't take a local filesystem path the way their CLI
equivalent does, since this server may run in a container with no access to
whatever filesystem is calling it:

- `create_backup` returns the zip (database snapshot + every note's images)
  embedded directly in the tool response, rather than writing it to a
  server-local path.
- `upload_image` takes the file's content as base64 plus a filename, rather
  than a local path to read from.

## Config

All via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `WIKI_URL` | `http://localhost:8081` | Base URL of the wiki-ai backend this server talks to |
| `MCP_HOST` | `0.0.0.0` | Interface this server binds to |
| `MCP_PORT` | `8082` | Port this server listens on |

## Run standalone

```sh
cd mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
WIKI_URL=http://localhost:8081 python server.py
```

The server is then reachable at `http://localhost:8082/mcp`.

## Run via Docker

Standalone:

```sh
docker build -t wiki-mcp ./mcp
docker run -p 8082:8082 \
  --add-host=host.docker.internal:host-gateway \
  -e WIKI_URL=http://host.docker.internal:8081 \
  wiki-mcp
```

`--add-host` makes `host.docker.internal` resolve on Linux Docker Engine (Docker
Desktop on Mac/Windows wires it up automatically, but the flag is harmless
there too) — same reasoning `docker-compose.yml`'s `api` service documents for
reaching Ollama the same way.

Or as part of the full stack (added as the `mcp` service in the repo's
`docker-compose.yml`, alongside `api` and `qdrant`):

```sh
docker compose up -d
```

This wires `WIKI_URL` to the `api` service's internal hostname (`wiki`)
automatically, and exposes the MCP server on `${MCP_PORT:-8090}` on the host
(the container's own internal port is still 8082 — the host-side default is
different so it doesn't collide with `scripts/staging.sh`, which defaults to
publishing its staging api on host port 8082).

## Point an MCP client at it

The port depends on how you ran it above: **`8082`** standalone (bare `python
server.py` or the standalone `docker run`), or **`8090`** if you used `docker
compose up` (or whatever you set `MCP_PORT` to). The examples below assume
compose's `8090`; swap in `8082` for standalone.

**Claude Code** — add to `.mcp.json` in whatever project you want to use it
from:

```json
{
  "mcpServers": {
    "wiki-ai": {
      "type": "http",
      "url": "http://localhost:8090/mcp"
    }
  }
}
```

Any other MCP client that supports a streamable-HTTP (or SSE-compatible)
remote server works the same way: point it at `http://<host>:8090/mcp`. There
is no authentication on this endpoint — treat it the same as the existing
`api` service, trusted-local-network only.

## Tests

```sh
cd mcp
pip install -r requirements-dev.txt
pytest
```

The test suite mocks the wiki API's HTTP layer (no live backend needed), with
a success and a failure case for every tool.
