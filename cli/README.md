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

# page through all notes — each page prints a "next page: --offset N" hint
# once there are more notes beyond the current page
./wiki-cli list --limit 20 --offset 20

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

# trigger a server-side backup and download it — saves under the server's
# suggested filename by default, or pass your own output path. Downloads as a
# zip containing the database snapshot plus every note's uploaded images.
./wiki-cli backup
./wiki-cli backup ./my-backup.zip

# upload a local file as an image attached to a note, printing the image URL
# to insert into the note's markdown as ![alt](url)
./wiki-cli upload-image --note <uuid> ./photo.jpg

# point at a different server
./wiki-cli list --url http://wiki.example.com
WIKI_URL=http://wiki.example.com ./wiki-cli list
```

## The knowledge tree

Notes form a tree. A **container** is a note that groups other notes and whose
body says what that grouping *is* — what the course or project was, when it
ran, whether it's still current. A note has at most one parent, which must be
a container; a note with no parent sits at the top level.

The point is `ancestors`. A note filed under something called `UH-100` tells
you nothing on its own, but walking up returns each container's prose, which
is what makes the note interpretable — by you a year later, or by an agent
reading through the MCP server.

Containers are kept out of ordinary listing and search, so they never clutter
results. Pass `--scope` when you want them.

```sh
# create a container — its first non-blank line becomes its name, and the
# rest is the description the walk-up hands back
./wiki-cli create --kind container <<'EOF'
# UH-100

A 100-level college survey course I took in 2018. These notes are
historical — useful background, not current thinking.
EOF

# file a note under it (or move it back to the top level)
./wiki-cli set-parent <note-uuid> --parent <container-uuid>
./wiki-cli set-parent <note-uuid> --clear

# walk up: the containers above a note, root first
./wiki-cli ancestors <note-uuid>

# walk down one level
./wiki-cli children <container-uuid>

# every container with its parent — the tree skeleton. Paged like any other
# listing (100 per request max), so page with --offset until you've seen the
# "showing X-Y of N" total.
./wiki-cli list --scope containers --limit 100

# search containers instead of notes (or both)
./wiki-cli search --scope containers "college"
./wiki-cli search --scope all "history"
```
