package main

import (
	"flag"
	"fmt"
	"io"
	"os"
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
  search <query>       search notes

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
	limit := fs.Int("limit", 10, "max number of notes to return")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	notes, err := newClient(url).listNotes(*limit)
	if err != nil {
		fail(err)
	}
	for _, n := range notes {
		fmt.Printf("%s  %-30s %-20s %s\n", sanitizeForTerminal(n.ID), sanitizeForTerminal(n.Title), sanitizeForTerminal(n.Path), n.CreatedAt)
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
}

func runCreate(args []string) {
	fs := flag.NewFlagSet("create", flag.ExitOnError)
	path := fs.String("path", "/", "note path")
	file := fs.String("file", "", "read content from this file (default: stdin)")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	content, err := readContent(*file)
	if err != nil {
		fail(err)
	}

	id, err := newClient(url).createNote(content, *path)
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
	limit := fs.Int("limit", 10, "max number of results")
	urlFlag := addURLFlag(fs)
	fs.Parse(args)
	url := *urlFlag

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: wiki-cli search <query>")
		os.Exit(1)
	}

	hits, err := newClient(url).search(fs.Arg(0), *limit)
	if err != nil {
		fail(err)
	}
	for _, h := range hits {
		fmt.Printf("%s  %-30s %-20s %s\n", sanitizeForTerminal(h.ID), sanitizeForTerminal(h.Title), sanitizeForTerminal(h.Path), sanitizeForTerminal(h.Preview))
	}
}
