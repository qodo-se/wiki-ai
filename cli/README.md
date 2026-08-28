# wiki-cli

A command-line client for the wiki HTTP API. Written in Go, standard library
only — no external dependencies, builds to a single static binary.

## Build

```sh
cd cli
go build -o wiki-cli .
```

## Usage

By default the CLI talks to `http://localhost:8081` (the `docker-compose.yml`
port mapping). Override with `--url` or `$WIKI_URL`.

Flags must come *before* positional arguments (a limitation of Go's `flag`
package, which stops parsing at the first non-flag argument) — e.g.
`wiki-cli update --path /new/path <uuid>`, not the other way around.

```sh
# list recent notes
./wiki-cli list --limit 20

# create a note from a file, at a given path — the server allocates the id
# and prints it (the CLI never picks its own id)
./wiki-cli create --path /docs/setup --file notes.md

# create a note from stdin
echo "hello wiki" | ./wiki-cli create

# fetch content / metadata
./wiki-cli get <uuid>
./wiki-cli meta <uuid>

# update an existing note's content, keeping its existing path
# (update only ever modifies a note that already exists — it 404s otherwise;
# use `create` to make a new one)
./wiki-cli update --file notes.md <uuid>

# update content and move it to a new path
./wiki-cli update --path /docs/new-setup --file notes.md <uuid>

# delete a note
./wiki-cli delete <uuid>

# search
./wiki-cli search --limit 20 "setup"

# point at a different server
./wiki-cli list --url http://wiki.example.com
WIKI_URL=http://wiki.example.com ./wiki-cli list
```
