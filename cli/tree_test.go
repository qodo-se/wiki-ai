package main

import (
	"encoding/json"
	"flag"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const testParentID = "33333333-3333-3333-3333-333333333333"

// --- scope threading -------------------------------------------------------

func TestListNotesOmitsScopeWhenUnset(t *testing.T) {
	// A command that never mentions scope must send the request it always
	// sent, leaving the server's own default to apply.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, present := r.URL.Query()["scope"]; present {
			t.Errorf("scope should be absent, got %q", r.URL.RawQuery)
		}
		w.Write([]byte(`{"items":[],"total":0,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).listNotes(10, 0, ""); err != nil {
		t.Fatalf("listNotes: %v", err)
	}
}

func TestListNotesSendsScopeWhenSet(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.URL.Query().Get("scope"); got != "containers" {
			t.Errorf("scope = %q, want containers", got)
		}
		w.Write([]byte(`{"items":[],"total":0,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).listNotes(10, 0, "containers"); err != nil {
		t.Fatalf("listNotes: %v", err)
	}
}

func TestSearchSendsScope(t *testing.T) {
	for _, tc := range []struct {
		name string
		path string
		call func(*client) error
	}{
		{"search", "/api/v1/search", func(c *client) error {
			_, err := c.search("q", 10, 0, "all")
			return err
		}},
		{"search-hybrid", "/api/v1/search/hybrid", func(c *client) error {
			_, err := c.hybridSearch("q", 10, 0, "all")
			return err
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != tc.path {
					t.Errorf("path = %s, want %s", r.URL.Path, tc.path)
				}
				if got := r.URL.Query().Get("scope"); got != "all" {
					t.Errorf("scope = %q, want all", got)
				}
				w.Write([]byte(`{"items":[],"total":0,"limit":10,"offset":0}`))
			}))
			defer srv.Close()

			if err := tc.call(newClient(srv.URL)); err != nil {
				t.Fatalf("%s: %v", tc.name, err)
			}
		})
	}
}

func TestListNotesParsesKindAndParent(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"items":[
			{"id":"a","title":"UH-100","path":"/","preview":"","created_at":"x","kind":"container","parent_id":"` + testParentID + `"},
			{"id":"b","title":"root","path":"/","preview":"","created_at":"x","kind":"container","parent_id":null}
		],"total":2,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	list, err := newClient(srv.URL).listNotes(10, 0, "containers")
	if err != nil {
		t.Fatalf("listNotes: %v", err)
	}
	if list.Items[0].Kind != "container" {
		t.Errorf("kind = %q, want container", list.Items[0].Kind)
	}
	if list.Items[0].ParentID == nil || *list.Items[0].ParentID != testParentID {
		t.Errorf("parent_id = %v, want %s", list.Items[0].ParentID, testParentID)
	}
	if list.Items[1].ParentID != nil {
		t.Errorf("a null parent_id must decode as nil, got %v", *list.Items[1].ParentID)
	}
}

func TestListNotesToleratesABackendWithoutTreeFields(t *testing.T) {
	// A backend predating the knowledge tree sends neither field; parsing
	// must still succeed rather than failing the whole command.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"items":[{"id":"a","title":"t","path":"/","preview":"","created_at":"x"}],"total":1,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	list, err := newClient(srv.URL).listNotes(10, 0, "")
	if err != nil {
		t.Fatalf("listNotes: %v", err)
	}
	if list.Items[0].Kind != "" || list.Items[0].ParentID != nil {
		t.Errorf("missing fields should stay zero-valued, got %+v", list.Items[0])
	}
}

// --- createNote --kind -----------------------------------------------------

func TestCreateNoteSendsKindOnlyWhenSet(t *testing.T) {
	for _, tc := range []struct {
		name     string
		kind     string
		wantKind string
		present  bool
	}{
		{"unset", "", "", false},
		{"container", "container", "container", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				raw, _ := io.ReadAll(r.Body)
				var body map[string]string
				if err := json.Unmarshal(raw, &body); err != nil {
					t.Fatalf("bad body: %v", err)
				}
				got, present := body["kind"]
				if present != tc.present {
					t.Errorf("kind present = %v, want %v (body %s)", present, tc.present, raw)
				}
				if got != tc.wantKind {
					t.Errorf("kind = %q, want %q", got, tc.wantKind)
				}
				w.Write([]byte(`{"id":"11111111-1111-1111-1111-111111111111"}`))
			}))
			defer srv.Close()

			if _, err := newClient(srv.URL).createNote("# C", "/", tc.kind); err != nil {
				t.Fatalf("createNote: %v", err)
			}
		})
	}
}

// --- getNoteAncestors ------------------------------------------------------

func TestGetNoteAncestorsRequestAndParse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method = %s, want GET", r.Method)
		}
		if r.URL.Path != "/api/v1/notes/"+testParentID+"/ancestors" {
			t.Errorf("path = %s", r.URL.Path)
		}
		w.Write([]byte(`{"items":[
			{"id":"a","title":"education","kind":"container","content":"# education","content_truncated":false},
			{"id":"b","title":"UH-100","kind":"container","content":"# UH-100","content_truncated":true}
		]}`))
	}))
	defer srv.Close()

	resp, err := newClient(srv.URL).getNoteAncestors(testParentID)
	if err != nil {
		t.Fatalf("getNoteAncestors: %v", err)
	}
	if len(resp.Items) != 2 {
		t.Fatalf("want 2 ancestors, got %d", len(resp.Items))
	}
	if resp.Items[0].Title != "education" {
		t.Errorf("ancestors must come back root first, got %q", resp.Items[0].Title)
	}
	if !resp.Items[1].ContentTruncated {
		t.Error("content_truncated did not decode")
	}
}

func TestGetNoteAncestorsSurfacesAPIError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"note not found"}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).getNoteAncestors(testParentID); err == nil {
		t.Fatal("expected an error for a 404")
	}
}

// --- getNoteChildren -------------------------------------------------------

func TestGetNoteChildrenRequestAndParse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/notes/"+testParentID+"/children" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if got := r.URL.Query().Get("limit"); got != "5" {
			t.Errorf("limit = %s, want 5", got)
		}
		if got := r.URL.Query().Get("offset"); got != "10" {
			t.Errorf("offset = %s, want 10", got)
		}
		json.NewEncoder(w).Encode(childListResponse{
			Items:  []childSummary{{ID: "c1", Title: "Sub", Kind: "container"}},
			Total:  11,
			Limit:  5,
			Offset: 10,
		})
	}))
	defer srv.Close()

	resp, err := newClient(srv.URL).getNoteChildren(testParentID, 5, 10)
	if err != nil {
		t.Fatalf("getNoteChildren: %v", err)
	}
	if resp.Total != 11 || resp.Items[0].Kind != "container" {
		t.Fatalf("unexpected response: %+v", resp)
	}
}

// --- setNoteParent ---------------------------------------------------------

func TestSetNoteParentSendsTheParentID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut {
			t.Errorf("method = %s, want PUT", r.Method)
		}
		if r.URL.Path != "/api/v1/notes/"+testParentID+"/parent" {
			t.Errorf("path = %s", r.URL.Path)
		}
		raw, _ := io.ReadAll(r.Body)
		if string(raw) != `{"parent_id":"`+testParentID+`"}` {
			t.Errorf("body = %s", raw)
		}
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	parent := testParentID
	if err := newClient(srv.URL).setNoteParent(testParentID, &parent); err != nil {
		t.Fatalf("setNoteParent: %v", err)
	}
}

func TestSetNoteParentClearsWithAnExplicitNull(t *testing.T) {
	// An omitted key would read as "leave the parent alone"; clearing has to
	// be an explicit JSON null.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		if string(raw) != `{"parent_id":null}` {
			t.Errorf("body = %s, want an explicit null", raw)
		}
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	if err := newClient(srv.URL).setNoteParent(testParentID, nil); err != nil {
		t.Fatalf("setNoteParent: %v", err)
	}
}

func TestSetNoteParentSurfacesARejectedAssignment(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"detail":"that would make a cycle"}`))
	}))
	defer srv.Close()

	parent := testParentID
	err := newClient(srv.URL).setNoteParent(testParentID, &parent)
	if err == nil {
		t.Fatal("expected an error for a 400")
	}
	if !strings.Contains(err.Error(), "cycle") {
		t.Errorf("error should carry the server's reason, got %q", err.Error())
	}
}

// --- flag parsing around the positional argument ---------------------------

func TestParsePositionalAcceptsFlagsOnEitherSide(t *testing.T) {
	// Regression: Go's flag package stops parsing at the first non-flag
	// argument, so `set-parent <uuid> --parent <uuid>` — the form the usage
	// text and README both document — silently dropped every flag.
	for _, tc := range []struct {
		name       string
		args       []string
		wantID     string
		wantParent string
	}{
		{"flags after positional", []string{"note-id", "--parent", "box-id"}, "note-id", "box-id"},
		{"flags before positional", []string{"--parent", "box-id", "note-id"}, "note-id", "box-id"},
		{"flags on both sides", []string{"--parent", "box-id", "note-id", "--url", "http://x"}, "note-id", "box-id"},
		{"no flags at all", []string{"note-id"}, "note-id", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fs := flag.NewFlagSet("set-parent", flag.ContinueOnError)
			parent := fs.String("parent", "", "")
			fs.String("url", "", "")

			got := parsePositional(fs, tc.args)

			if got != tc.wantID {
				t.Errorf("positional = %q, want %q", got, tc.wantID)
			}
			if *parent != tc.wantParent {
				t.Errorf("--parent = %q, want %q", *parent, tc.wantParent)
			}
		})
	}
}

func TestParsePositionalReturnsEmptyWhenNoneGiven(t *testing.T) {
	fs := flag.NewFlagSet("ancestors", flag.ContinueOnError)
	fs.String("url", "", "")

	if got := parsePositional(fs, []string{"--url", "http://x"}); got != "" {
		t.Errorf("positional = %q, want empty so the caller prints usage", got)
	}
}

func TestFormatParentDistinguishesTopLevel(t *testing.T) {
	if got := formatParent(nil); got != "(top level)" {
		t.Errorf("nil parent = %q", got)
	}
	empty := ""
	if got := formatParent(&empty); got != "(top level)" {
		t.Errorf("empty parent = %q", got)
	}
	id := testParentID
	if got := formatParent(&id); got != testParentID {
		t.Errorf("parent = %q, want the full id so the tree can be reconstructed", got)
	}
}

func TestSearchCommandsAcceptScopeAfterTheQuery(t *testing.T) {
	// Regression: runSearch/runHybridSearch registered --scope but parsed
	// once with Go's flag parser, which stops at the positional query — so
	// `search "college" --scope containers` silently searched notes instead.
	for _, order := range [][]string{
		{"college", "--scope", "containers"},
		{"--scope", "containers", "college"},
	} {
		fs := flag.NewFlagSet("search", flag.ContinueOnError)
		scope := fs.String("scope", "notes", "")
		fs.Int("limit", 10, "")

		query := parsePositional(fs, order)

		if query != "college" {
			t.Errorf("%v: query = %q, want college", order, query)
		}
		if *scope != "containers" {
			t.Errorf("%v: scope = %q, want containers", order, *scope)
		}
	}
}

// --- breadcrumb on search hits ---------------------------------------------

func TestSearchHitDecodesTreeFields(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"items":[
			{"id":"a","title":"Reconstruction essay","path":"/","preview":"p","score":2,
			 "created_at":"x","kind":"note","parent_id":"` + testParentID + `",
			 "breadcrumb":"School › UH-100 › US History Foundations"}
		],"total":1,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	resp, err := newClient(srv.URL).search("Reconstruction", 10, 0, "")
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	hit := resp.Items[0]
	if hit.Kind != "note" {
		t.Errorf("kind = %q, want note", hit.Kind)
	}
	if hit.ParentID == nil || *hit.ParentID != testParentID {
		t.Errorf("parent_id = %v, want %s", hit.ParentID, testParentID)
	}
	if hit.Breadcrumb != "School › UH-100 › US History Foundations" {
		t.Errorf("breadcrumb = %q", hit.Breadcrumb)
	}
}

func TestSearchHitToleratesABackendWithoutTreeFields(t *testing.T) {
	// A backend predating this change sends none of the three.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"items":[{"id":"a","title":"t","path":"/","preview":"p","score":1,"created_at":"x"}],"total":1,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	resp, err := newClient(srv.URL).search("q", 10, 0, "")
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	hit := resp.Items[0]
	if hit.Kind != "" || hit.ParentID != nil || hit.Breadcrumb != "" {
		t.Errorf("missing fields should stay zero-valued, got %+v", hit)
	}
}

func TestHybridSearchHitDecodesTheBreadcrumb(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/search/hybrid" {
			t.Errorf("path = %s", r.URL.Path)
		}
		w.Write([]byte(`{"items":[{"id":"a","title":"t","path":"/","preview":"p","score":0.5,
			"created_at":"x","kind":"note","parent_id":null,"breadcrumb":"School"}],
			"total":1,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	resp, err := newClient(srv.URL).hybridSearch("q", 10, 0, "")
	if err != nil {
		t.Fatalf("hybridSearch: %v", err)
	}
	if resp.Items[0].Breadcrumb != "School" {
		t.Errorf("breadcrumb = %q, want School", resp.Items[0].Breadcrumb)
	}
	if resp.Items[0].ParentID != nil {
		t.Error("a null parent_id must decode as nil")
	}
}
