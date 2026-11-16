package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
)

const defaultBaseURL = "http://localhost:8081"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	cmd := os.Args[1]
	args := os.Args[2:]

	switch cmd {
	case "list":
		runList(args)
	case "get":
		runGet(args)
	case "meta":
		runMeta(args)
	case "create":
		runCreate(args)
	case "update":
		runUpdate(args)
	case "delete":
		runDelete(args)
	case "search":
		runSearch(args)
	case "search-hybrid":
		runHybridSearch(args)
	case "ancestors":
		runAncestors(args)
	case "children":
		runChildren(args)
	case "set-parent":
		runSetParent(args)
	case "config":
		runConfig(args)
	case "reindex":
		runReindex(args)
	case "backup":
		runBackup(args)
	case "upload-image":
		runUploadImage(args)
	case "-h", "--help", "help":
		usage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n\n", cmd)
		usage()
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `wiki-cli - command-line client for the wiki API

Usage:
  wiki-cli <command> [flags]

Commands:
  list                 list notes
  get <uuid>           print a note's raw content
  meta <uuid>          print a note's metadata
  create               create a note (content from --file or stdin)
  update <uuid>        update a note (content from --file or stdin)
  delete <uuid>        delete a note
  search <query>       search notes (keyword, whole-word match)
  search-hybrid <query>
                       keyword + semantic search fused by reciprocal rank
                       fusion — catches notes an exact keyword match would
                       miss semantically, and vice versa; prints each hit's
                       fused RRF score
  ancestors <uuid>     print the containers this note lives under, root
                       first — each container's body says what it is, which
                       is the context a note's own text usually leaves out
  children <uuid>      list what's filed directly under this note
  set-parent <uuid> (--parent <uuid> | --clear)
                       file a note under a container, or move it back to the
                       top level
  config get           print the server's current Ollama/Qdrant settings
  config set [flags]   update one or more settings (see "config set flags"
                       below); only the flags you pass are changed
  reindex              drop and re-embed every note's semantic search vector
                       (e.g. after changing the embedding model)
  backup [output-path]
                       trigger a server-side backup and download it (default
                       output filename comes from the server)
  upload-image --note <uuid> <path>
                       upload a local file as an image attached to a note,
                       printing the image URL to insert into the note's
                       markdown as ![alt](url)

The wiki is a tree: a container is a note that groups other notes and whose
body describes what that grouping is. Containers are hidden from ordinary
listing and search — pass --scope to see them.

list flags:
  --limit int     max number of notes to return per page (default 10)
  --offset int    number of notes to skip, for paging (default 0)
  --scope string  "notes" (default), "containers", or "all". Listing with
                  --scope containers returns every container with its
                  parent, i.e. the whole tree skeleton in one call

children flags:
  --limit int    max number of children to return per page (default 10)
  --offset int   number of children to skip, for paging (default 0)

set-parent flags:
  --parent string  uuid of the container to file this note under
  --clear          move the note back to the top level
                   (pass exactly one of --parent or --clear)

search / search-hybrid flags:
  --limit int     max number of results to return per page (default 10)
  --offset int    number of results to skip, for paging (default 0)
  --scope string  "notes" (default), "containers", or "all"

create flags:
  --path string   note path (default "/")
  --file string   read content from this file (default: stdin)
  --kind string   "note" (default) or "container". A container's first
                  non-blank line becomes its name in the tree, and its body
                  is where you say what the grouping is

config set flags:
  --ollama-url string
  --ollama-embedding-model string
  --ollama-chat-model string
  --qdrant-url string
  --qdrant-collection string

Global flags (accepted by every command):
  --url string   base URL of the wiki API (default "http://localhost:8081",
                 or $WIKI_URL if set)

Flags must come before positional arguments, e.g.:
  wiki-cli update --path /docs/setup <uuid>

Run "wiki-cli <command> -h" for command-specific flags.
`)
}

// addURLFlag registers --url on fs. Call fs.Parse, then dereference the
// returned pointer to get the effective value.
func addURLFlag(fs *flag.FlagSet) *string {
	def := os.Getenv("WIKI_URL")
	if def == "" {
		def = defaultBaseURL
	}
	return fs.String("url", def, "base URL of the wiki API")
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "error:", err)
	os.Exit(1)
}

// readContent reads note content from --file, or stdin if --file is unset/"-".
func readContent(path string) (string, error) {
	if path == "" || path == "-" {
		data, err := io.ReadAll(os.Stdin)
		return string(data), err
	}
	data, err := os.ReadFile(path)
	return string(data), err
}

func runList(args []string) {
	fs := flag.NewFlagSet("list", flag.ExitOnError)
	limit := fs.Int("limit", 10, "max number of notes to return per page")
	offset := fs.Int("offset", 0, "number of notes to skip (for paging)")
	scope := addScopeFlag(fs)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if *offset < 0 {
		fmt.Fprintln(os.Stderr, "error: --offset must be >= 0")
		os.Exit(1)
	}
	requireValidScope(*scope)

	list, err := newClient(url).listNotes(*limit, *offset, *scope)
	if err != nil {
		fail(err)
	}
	for _, n := range list.Items {
		fmt.Printf("%s  %-11s %-30s %-20s %s\n", sanitizeForTerminal(n.ID), sanitizeForTerminal(n.Kind), sanitizeForTerminal(n.Title), sanitizeForTerminal(n.Path), n.CreatedAt)
	}

	if list.Total == 0 {
		fmt.Println("no notes")
		return
	}
	if len(list.Items) == 0 {
		fmt.Printf("no notes at offset %d (%d total) — try a smaller --offset\n", list.Offset, list.Total)
		return
	}
	shownFrom := list.Offset + 1
	shownTo := list.Offset + len(list.Items)
	fmt.Printf("showing %d-%d of %d\n", shownFrom, shownTo, list.Total)
	if shownTo < list.Total {
		fmt.Printf("next page: --offset %d\n", list.Offset+*limit)
	}
}

func runGet(args []string) {
	fs := flag.NewFlagSet("get", flag.ExitOnError)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli get <uuid>")
		os.Exit(1)
	}

	content, err := newClient(url).getNoteContent(requireUUID(fs.Arg(0)))
	if err != nil {
		fail(err)
	}
	fmt.Print(content)
}

func runMeta(args []string) {
	fs := flag.NewFlagSet("meta", flag.ExitOnError)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli meta <uuid>")
		os.Exit(1)
	}

	meta, err := newClient(url).getNoteMeta(requireUUID(fs.Arg(0)))
	if err != nil {
		fail(err)
	}
	fmt.Printf("id:         %s\n", sanitizeForTerminal(meta.ID))
	fmt.Printf("title:      %s\n", sanitizeForTerminal(meta.Title))
	fmt.Printf("path:       %s\n", sanitizeForTerminal(meta.Path))
	fmt.Printf("created_at: %s\n", meta.CreatedAt)
	fmt.Printf("updated_at: %s\n", meta.UpdatedAt)
	fmt.Printf("kind:       %s\n", sanitizeForTerminal(meta.Kind))
	fmt.Printf("parent:     %s\n", formatParent(meta.ParentID))
}

func runCreate(args []string) {
	fs := flag.NewFlagSet("create", flag.ExitOnError)
	path := fs.String("path", "/", "note path")
	file := fs.String("file", "", "read content from this file (default: stdin)")
	kind := fs.String("kind", "note", `"note" or "container"`)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if *kind != "note" && *kind != "container" {
		fmt.Fprintf(os.Stderr, "error: --kind must be \"note\" or \"container\", got %q\n", sanitizeForTerminal(*kind))
		os.Exit(1)
	}

	content, err := readContent(*file)
	if err != nil {
		fail(err)
	}

	id, err := newClient(url).createNote(content, *path, *kind)
	if err != nil {
		fail(err)
	}
	fmt.Println(sanitizeForTerminal(id))
}

func runUpdate(args []string) {
	fs := flag.NewFlagSet("update", flag.ExitOnError)
	path := fs.String("path", "", "note path (default: keep the note's current path)")
	file := fs.String("file", "", "read content from this file (default: stdin)")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli update [--path P] [--file F] <uuid>")
		os.Exit(1)
	}
	id := requireUUID(fs.Arg(0))
	c := newClient(url)

	notePath := *path
	if notePath == "" {
		meta, err := c.getNoteMeta(id)
		if err != nil {
			fail(err)
		}
		notePath = meta.Path
	}

	content, err := readContent(*file)
	if err != nil {
		fail(err)
	}

	if err := c.putNote(id, content, notePath); err != nil {
		fail(err)
	}
	fmt.Println(id)
}

func runDelete(args []string) {
	fs := flag.NewFlagSet("delete", flag.ExitOnError)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli delete <uuid>")
		os.Exit(1)
	}

	if err := newClient(url).deleteNote(requireUUID(fs.Arg(0))); err != nil {
		fail(err)
	}
	fmt.Println("deleted")
}

func runSearch(args []string) {
	fs := flag.NewFlagSet("search", flag.ExitOnError)
	limit := fs.Int("limit", 10, "max number of results per page")
	offset := fs.Int("offset", 0, "number of results to skip (for paging)")
	scope := addScopeFlag(fs)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli search <query>")
		os.Exit(1)
	}
	if *offset < 0 {
		fmt.Fprintln(os.Stderr, "error: --offset must be >= 0")
		os.Exit(1)
	}

	requireValidScope(*scope)

	resp, err := newClient(url).search(fs.Arg(0), *limit, *offset, *scope)
	if err != nil {
		fail(err)
	}
	for _, h := range resp.Items {
		fmt.Printf("%s  %-30s %-20s matches:%-3d %s\n", sanitizeForTerminal(h.ID), sanitizeForTerminal(h.Title), sanitizeForTerminal(h.Path), int(h.Score), sanitizeForTerminal(h.Preview))
	}
	printSearchPageInfo(resp, *limit)
}

// addScopeFlag is shared by list/search/search-hybrid so the three never
// drift on spelling or default. Empty means "don't send one" — the server's
// own default (notes only) then applies.
func addScopeFlag(fs *flag.FlagSet) *string {
	return fs.String("scope", "notes", `which kinds of note: "notes", "containers", or "all"`)
}

func requireValidScope(scope string) {
	switch scope {
	case "notes", "containers", "all":
		return
	}
	fmt.Fprintf(os.Stderr, "error: --scope must be \"notes\", \"containers\", or \"all\", got %q\n", sanitizeForTerminal(scope))
	os.Exit(1)
}

func formatParent(parentID *string) string {
	if parentID == nil || *parentID == "" {
		return "(top level)"
	}
	return sanitizeForTerminal(*parentID)
}

func runAncestors(args []string) {
	fs := flag.NewFlagSet("ancestors", flag.ExitOnError)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli ancestors <uuid>")
		os.Exit(1)
	}

	resp, err := newClient(url).getNoteAncestors(requireUUID(fs.Arg(0)))
	if err != nil {
		fail(err)
	}
	if len(resp.Items) == 0 {
		fmt.Println("(top level — no containers above this note)")
		return
	}
	// Root first, indenting one level per step, so the shape of the chain is
	// visible at a glance rather than having to be reconstructed.
	for i, a := range resp.Items {
		fmt.Printf("%s%s  %s\n", strings.Repeat("  ", i), sanitizeForTerminal(a.ID), sanitizeForTerminal(a.Title))
	}
}

func runChildren(args []string) {
	fs := flag.NewFlagSet("children", flag.ExitOnError)
	limit := fs.Int("limit", 10, "max number of children to return per page")
	offset := fs.Int("offset", 0, "number of children to skip (for paging)")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli children <uuid>")
		os.Exit(1)
	}
	if *offset < 0 {
		fmt.Fprintln(os.Stderr, "error: --offset must be >= 0")
		os.Exit(1)
	}

	resp, err := newClient(url).getNoteChildren(requireUUID(fs.Arg(0)), *limit, *offset)
	if err != nil {
		fail(err)
	}
	for _, c := range resp.Items {
		fmt.Printf("%s  %-11s %-30s %s\n", sanitizeForTerminal(c.ID), sanitizeForTerminal(c.Kind), sanitizeForTerminal(c.Title), c.CreatedAt)
	}
	if resp.Total == 0 {
		fmt.Println("nothing filed under this note")
		return
	}
	if len(resp.Items) == 0 {
		fmt.Printf("no children at offset %d (%d total) — try a smaller --offset\n", resp.Offset, resp.Total)
		return
	}
	shownTo := resp.Offset + len(resp.Items)
	fmt.Printf("showing %d-%d of %d\n", resp.Offset+1, shownTo, resp.Total)
	if shownTo < resp.Total {
		fmt.Printf("next page: --offset %d\n", resp.Offset+*limit)
	}
}

func runSetParent(args []string) {
	fs := flag.NewFlagSet("set-parent", flag.ExitOnError)
	parent := fs.String("parent", "", "uuid of the container to file this note under")
	clear := fs.Bool("clear", false, "move the note back to the top level")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli set-parent <uuid> (--parent <uuid> | --clear)")
		os.Exit(1)
	}
	// Requiring exactly one of the two makes clearing an explicit act. A bare
	// `set-parent <uuid>` is far more likely to be a forgotten flag than a
	// deliberate request to unfile the note.
	if *clear == (*parent != "") {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli set-parent <uuid> (--parent <uuid> | --clear)")
		fmt.Fprintln(os.Stderr, "error: pass exactly one of --parent or --clear")
		os.Exit(1)
	}

	var parentID *string
	if !*clear {
		validated := requireUUID(*parent)
		parentID = &validated
	}

	if err := newClient(url).setNoteParent(requireUUID(fs.Arg(0)), parentID); err != nil {
		fail(err)
	}
	if parentID == nil {
		fmt.Println("moved to top level")
		return
	}
	fmt.Printf("filed under %s\n", sanitizeForTerminal(*parentID))
}

func runBackup(args []string) {
	fs := flag.NewFlagSet("backup", flag.ExitOnError)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	outPath := ""
	if fs.NArg() > 0 {
		outPath = fs.Arg(0)
	}

	c := newClient(url)
	meta, err := c.createBackup()
	if err != nil {
		fail(err)
	}
	saved, err := c.downloadBackup(outPath, meta.SnapshotID)
	if err != nil {
		fail(err)
	}
	fmt.Println(saved)
}

func runUploadImage(args []string) {
	fs := flag.NewFlagSet("upload-image", flag.ExitOnError)
	note := fs.String("note", "", "uuid of the note to attach the image to (required)")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if *note == "" || fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli upload-image --note <uuid> <path>")
		os.Exit(1)
	}

	img, err := newClient(url).uploadImage(requireUUID(*note), fs.Arg(0))
	if err != nil {
		fail(err)
	}
	fmt.Println(sanitizeForTerminal(img.URL))
}

func runHybridSearch(args []string) {
	fs := flag.NewFlagSet("search-hybrid", flag.ExitOnError)
	limit := fs.Int("limit", 10, "max number of results per page")
	offset := fs.Int("offset", 0, "number of results to skip (for paging)")
	scope := addScopeFlag(fs)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli search-hybrid <query>")
		os.Exit(1)
	}
	if *offset < 0 {
		fmt.Fprintln(os.Stderr, "error: --offset must be >= 0")
		os.Exit(1)
	}

	requireValidScope(*scope)

	resp, err := newClient(url).hybridSearch(fs.Arg(0), *limit, *offset, *scope)
	if err != nil {
		fail(err)
	}
	for _, h := range resp.Items {
		fmt.Printf("%s  %-30s %-20s score:%.4f %s\n", sanitizeForTerminal(h.ID), sanitizeForTerminal(h.Title), sanitizeForTerminal(h.Path), h.Score, sanitizeForTerminal(h.Preview))
	}
	printSearchPageInfo(resp, *limit)
}

// printSearchPageInfo mirrors runList's "showing X-Y of Z" / "next page:
// --offset N" output, so paging through search results works the same way
// as paging through the note list.
func printSearchPageInfo(resp *searchResponse, limit int) {
	if resp.Total == 0 {
		fmt.Println("no matches")
		return
	}
	if len(resp.Items) == 0 {
		fmt.Printf("no matches at offset %d (%d total) — try a smaller --offset\n", resp.Offset, resp.Total)
		return
	}
	shownFrom := resp.Offset + 1
	shownTo := resp.Offset + len(resp.Items)
	fmt.Printf("showing %d-%d of %d\n", shownFrom, shownTo, resp.Total)
	if shownTo < resp.Total {
		fmt.Printf("next page: --offset %d\n", resp.Offset+limit)
	}
}

func runConfig(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli config <get|set> [flags]")
		os.Exit(1)
	}
	switch args[0] {
	case "get":
		runConfigGet(args[1:])
	case "set":
		runConfigSet(args[1:])
	default:
		fmt.Fprintf(os.Stderr, "unknown config subcommand %q (want \"get\" or \"set\")\n", args[0])
		os.Exit(1)
	}
}

func runConfigGet(args []string) {
	fs := flag.NewFlagSet("config get", flag.ExitOnError)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)

	cfg, err := newClient(*urlFlag).getConfig()
	if err != nil {
		fail(err)
	}
	printConfig(cfg)
}

func runConfigSet(args []string) {
	fs := flag.NewFlagSet("config set", flag.ExitOnError)
	ollamaURL := fs.String("ollama-url", "", "")
	embeddingModel := fs.String("ollama-embedding-model", "", "")
	chatModel := fs.String("ollama-chat-model", "", "")
	qdrantURL := fs.String("qdrant-url", "", "")
	qdrantCollection := fs.String("qdrant-collection", "", "")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)

	updates := map[string]string{}
	for field, val := range map[string]*string{
		"ollama_url":             ollamaURL,
		"ollama_embedding_model": embeddingModel,
		"ollama_chat_model":      chatModel,
		"qdrant_url":             qdrantURL,
		"qdrant_collection":      qdrantCollection,
	} {
		if *val != "" {
			updates[field] = *val
		}
	}
	if len(updates) == 0 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli config set [--ollama-url U] [--ollama-embedding-model M] [--ollama-chat-model M] [--qdrant-url U] [--qdrant-collection C]")
		os.Exit(1)
	}

	cfg, err := newClient(*urlFlag).setConfig(updates)
	if err != nil {
		fail(err)
	}
	printConfig(cfg)
}

func printConfig(cfg *appConfig) {
	fmt.Printf("ollama_url:             %s\n", sanitizeForTerminal(cfg.OllamaURL))
	fmt.Printf("ollama_embedding_model: %s\n", sanitizeForTerminal(cfg.OllamaEmbeddingModel))
	fmt.Printf("ollama_chat_model:      %s\n", sanitizeForTerminal(cfg.OllamaChatModel))
	fmt.Printf("qdrant_url:             %s\n", sanitizeForTerminal(cfg.QdrantURL))
	fmt.Printf("qdrant_collection:      %s\n", sanitizeForTerminal(cfg.QdrantCollection))
}

func runReindex(args []string) {
	fs := flag.NewFlagSet("reindex", flag.ExitOnError)
	urlFlag := addURLFlag(fs)
	fs.Parse(args)

	result, err := newClient(*urlFlag).reindex()
	if err != nil {
		fail(err)
	}
	fmt.Printf("total:%d embedded:%d failed:%d\n", result.Total, result.Embedded, result.Failed)
}
