package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNewClientTrimsTrailingSlash(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.Write([]byte(`{"items":[],"total":0,"limit":10,"offset":0}`))
	}))
	defer srv.Close()

	c := newClient(srv.URL + "/")
	if _, err := c.listNotes(10, 0); err != nil {
		t.Fatalf("listNotes: %v", err)
	}
	if gotPath != "/api/v1/notes" {
		t.Fatalf("expected /api/v1/notes, got %q (trailing slash in base URL should not double up)", gotPath)
	}
}

func TestListNotesRequest(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method = %s, want GET", r.Method)
		}
		if r.URL.Path != "/api/v1/notes" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if got := r.URL.Query().Get("limit"); got != "5" {
			t.Errorf("limit = %s, want 5", got)
		}
		if got := r.URL.Query().Get("offset"); got != "15" {
			t.Errorf("offset = %s, want 15", got)
		}
		json.NewEncoder(w).Encode(noteListResponse{
			Items:  []noteSummary{{ID: "abc", Title: "hi"}},
			Total:  42,
			Limit:  5,
			Offset: 15,
		})
	}))
	defer srv.Close()

	list, err := newClient(srv.URL).listNotes(5, 15)
	if err != nil {
		t.Fatalf("listNotes: %v", err)
	}
	if len(list.Items) != 1 || list.Items[0].ID != "abc" {
		t.Fatalf("unexpected items: %+v", list.Items)
	}
	if list.Total != 42 || list.Limit != 5 || list.Offset != 15 {
		t.Fatalf("unexpected pagination fields: %+v", list)
	}
}

func TestGetNoteContent(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/notes/some-id" {
			t.Errorf("path = %s", r.URL.Path)
		}
		w.Write([]byte("hello note"))
	}))
	defer srv.Close()

	content, err := newClient(srv.URL).getNoteContent("some-id")
	if err != nil {
		t.Fatalf("getNoteContent: %v", err)
	}
	if content != "hello note" {
		t.Fatalf("content = %q", content)
	}
}

func TestGetNoteMeta(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/notes/some-id/meta" {
			t.Errorf("path = %s", r.URL.Path)
		}
		json.NewEncoder(w).Encode(noteMeta{
			ID: "some-id", Title: "hi", Path: "/a/b",
			CreatedAt: "2026-01-01", UpdatedAt: "2026-01-02",
		})
	}))
	defer srv.Close()

	meta, err := newClient(srv.URL).getNoteMeta("some-id")
	if err != nil {
		t.Fatalf("getNoteMeta: %v", err)
	}
	if meta.ID != "some-id" || meta.Title != "hi" || meta.Path != "/a/b" ||
		meta.CreatedAt != "2026-01-01" || meta.UpdatedAt != "2026-01-02" {
		t.Fatalf("unexpected meta: %+v", meta)
	}
}

func TestGetNoteMetaNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"note not found"}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).getNoteMeta("missing"); err == nil {
		t.Fatal("expected an error for 404 response")
	}
}

func TestCreateNoteSendsContentAndPathAndReturnsServerID(t *testing.T) {
	var gotBody map[string]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/api/v1/notes" {
			t.Errorf("path = %s", r.URL.Path)
		}
		json.NewDecoder(r.Body).Decode(&gotBody)
		json.NewEncoder(w).Encode(map[string]string{"id": "2492fd58-f28c-4260-849a-4b764aafc933"})
	}))
	defer srv.Close()

	id, err := newClient(srv.URL).createNote("the content", "/a/b")
	if err != nil {
		t.Fatalf("createNote: %v", err)
	}
	if id != "2492fd58-f28c-4260-849a-4b764aafc933" {
		t.Fatalf("id = %q, want the id the server returned, not a client-chosen one", id)
	}
	if gotBody["content"] != "the content" || gotBody["path"] != "/a/b" {
		t.Fatalf("unexpected body: %+v", gotBody)
	}
}

func TestCreateNoteRejectsNonUUIDServerID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]string{"id": "not-a-uuid"})
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).createNote("content", "/"); err == nil {
		t.Fatal("expected an error when the server returns a non-UUID id")
	}
}

func TestPutNoteSendsContentAndPath(t *testing.T) {
	var gotBody map[string]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut {
			t.Errorf("method = %s, want PUT", r.Method)
		}
		if r.URL.Path != "/api/v1/notes/my-id" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if ct := r.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("content-type = %s", ct)
		}
		json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	if err := newClient(srv.URL).putNote("my-id", "the content", "/a/b"); err != nil {
		t.Fatalf("putNote: %v", err)
	}
	if gotBody["content"] != "the content" || gotBody["path"] != "/a/b" {
		t.Fatalf("unexpected body: %+v", gotBody)
	}
}

func TestPutNoteNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"note not found"}`))
	}))
	defer srv.Close()

	if err := newClient(srv.URL).putNote("missing", "content", "/"); err == nil {
		t.Fatal("expected an error updating a note that doesn't exist (update is not upsert)")
	}
}

func TestUploadImageSendsMultipartRequestWithNoteIDAndContentType(t *testing.T) {
	dir := t.TempDir()
	imgPath := filepath.Join(dir, "photo.png")
	if err := os.WriteFile(imgPath, []byte("fake-png-bytes"), 0o644); err != nil {
		t.Fatal(err)
	}

	var gotNoteID, gotContentType, gotFilename string
	var gotBytes []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/api/v1/images" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Fatalf("ParseMultipartForm: %v", err)
		}
		gotNoteID = r.FormValue("note_id")
		file, header, err := r.FormFile("file")
		if err != nil {
			t.Fatalf("FormFile: %v", err)
		}
		defer file.Close()
		gotContentType = header.Header.Get("Content-Type")
		gotFilename = header.Filename
		gotBytes, _ = io.ReadAll(file)
		json.NewEncoder(w).Encode(map[string]string{"id": "img-id", "url": "/api/v1/images/img-id"})
	}))
	defer srv.Close()

	url, err := newClient(srv.URL).uploadImage("note-1", imgPath, "image/png")
	if err != nil {
		t.Fatalf("uploadImage: %v", err)
	}
	if url != "/api/v1/images/img-id" {
		t.Fatalf("url = %q, want /api/v1/images/img-id", url)
	}
	if gotNoteID != "note-1" {
		t.Errorf("note_id = %q, want note-1", gotNoteID)
	}
	if gotContentType != "image/png" {
		t.Errorf("content-type = %q, want image/png", gotContentType)
	}
	if gotFilename != "photo.png" {
		t.Errorf("filename = %q, want photo.png", gotFilename)
	}
	if string(gotBytes) != "fake-png-bytes" {
		t.Errorf("bytes = %q, want fake-png-bytes", gotBytes)
	}
}

func TestUploadImageReturnsAPIErrorOnFailure(t *testing.T) {
	dir := t.TempDir()
	imgPath := filepath.Join(dir, "photo.png")
	if err := os.WriteFile(imgPath, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"note not found"}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).uploadImage("missing", imgPath, "image/png"); err == nil {
		t.Fatal("expected an error for a 404 response")
	}
}

func TestDeleteNote(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodDelete {
			t.Errorf("method = %s, want DELETE", r.Method)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	if err := newClient(srv.URL).deleteNote("my-id"); err != nil {
		t.Fatalf("deleteNote: %v", err)
	}
}

func TestSearchRequest(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.URL.Query().Get("q"); got != "hello world" {
			t.Errorf("q = %q", got)
		}
		if got := r.URL.Query().Get("limit"); got != "7" {
			t.Errorf("limit = %q", got)
		}
		json.NewEncoder(w).Encode([]searchHit{{ID: "x"}})
	}))
	defer srv.Close()

	hits, err := newClient(srv.URL).search("hello world", 7)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(hits) != 1 || hits[0].ID != "x" {
		t.Fatalf("unexpected hits: %+v", hits)
	}
}

func TestSemanticSearchRequest(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/search/semantic" {
			t.Errorf("path = %q", r.URL.Path)
		}
		if got := r.URL.Query().Get("q"); got != "hello world" {
			t.Errorf("q = %q", got)
		}
		if got := r.URL.Query().Get("limit"); got != "7" {
			t.Errorf("limit = %q", got)
		}
		json.NewEncoder(w).Encode([]searchHit{{ID: "x"}})
	}))
	defer srv.Close()

	hits, err := newClient(srv.URL).semanticSearch("hello world", 7)
	if err != nil {
		t.Fatalf("semanticSearch: %v", err)
	}
	if len(hits) != 1 || hits[0].ID != "x" {
		t.Fatalf("unexpected hits: %+v", hits)
	}
}

func TestNon2xxReturnsAPIError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"note not found"}`))
	}))
	defer srv.Close()

	_, err := newClient(srv.URL).getNoteContent("missing")
	if err == nil {
		t.Fatal("expected an error for 404 response")
	}
	apiErr, ok := err.(*apiError)
	if !ok {
		t.Fatalf("expected *apiError, got %T", err)
	}
	if apiErr.status != http.StatusNotFound {
		t.Fatalf("status = %d", apiErr.status)
	}
	if !strings.Contains(apiErr.Error(), "note not found") {
		t.Fatalf("error message missing body: %v", apiErr)
	}
}

func TestTransportErrorIsReturned(t *testing.T) {
	c := newClient("http://127.0.0.1:0")
	if _, err := c.listNotes(1, 0); err == nil {
		t.Fatal("expected a transport error connecting to an invalid address")
	}
}
