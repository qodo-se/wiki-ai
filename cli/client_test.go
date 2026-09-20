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

func TestCreateBackupParsesFilenameAndSize(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/api/v1/backup" {
			t.Errorf("path = %s", r.URL.Path)
		}
		json.NewEncoder(w).Encode(map[string]any{"filename": "manual-backup.db", "size": 4096})
	}))
	defer srv.Close()

	meta, err := newClient(srv.URL).createBackup()
	if err != nil {
		t.Fatalf("createBackup: %v", err)
	}
	if meta.Filename != "manual-backup.db" || meta.Size != 4096 {
		t.Fatalf("unexpected meta: %+v", meta)
	}
}

func TestCreateBackupReturnsAPIErrorWhenOneIsAlreadyInProgress(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
		w.Write([]byte(`{"detail":"a backup is already in progress"}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).createBackup(); err == nil {
		t.Fatal("expected an error for a 409 response")
	}
}

func TestDownloadBackupSavesToExplicitPath(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/backup" {
			t.Errorf("path = %s", r.URL.Path)
		}
		w.Header().Set("Content-Disposition", `attachment; filename="manual-backup.db"`)
		w.Write([]byte("fake-sqlite-bytes"))
	}))
	defer srv.Close()

	dir := t.TempDir()
	dest := filepath.Join(dir, "out.db")
	saved, err := newClient(srv.URL).downloadBackup(dest)
	if err != nil {
		t.Fatalf("downloadBackup: %v", err)
	}
	if saved != dest {
		t.Fatalf("saved = %q, want %q", saved, dest)
	}
	data, err := os.ReadFile(dest)
	if err != nil {
		t.Fatalf("reading saved file: %v", err)
	}
	if string(data) != "fake-sqlite-bytes" {
		t.Fatalf("data = %q", data)
	}
}

func TestDownloadBackupDerivesFilenameFromContentDisposition(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Disposition", `attachment; filename="manual-backup.db"`)
		w.Write([]byte("bytes"))
	}))
	defer srv.Close()

	dir := t.TempDir()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	defer os.Chdir(wd)

	saved, err := newClient(srv.URL).downloadBackup("")
	if err != nil {
		t.Fatalf("downloadBackup: %v", err)
	}
	if saved != "manual-backup.db" {
		t.Fatalf("saved = %q, want the server-suggested filename", saved)
	}
	if _, err := os.Stat(filepath.Join(dir, saved)); err != nil {
		t.Fatalf("expected file to exist: %v", err)
	}
}

func TestDownloadBackupOverwritesAnExistingFileOfTheSameAutoDerivedName(t *testing.T) {
	// The server always names it "manual-backup.db" — running this twice from
	// the same directory is expected to just overwrite, matching the server's
	// own single-file, no-history design. No "refuse to clobber" check here.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Disposition", `attachment; filename="manual-backup.db"`)
		w.Write([]byte("new-bytes"))
	}))
	defer srv.Close()

	dir := t.TempDir()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	defer os.Chdir(wd)

	if err := os.WriteFile("manual-backup.db", []byte("old-bytes"), 0o644); err != nil {
		t.Fatal(err)
	}

	if _, err := newClient(srv.URL).downloadBackup(""); err != nil {
		t.Fatalf("downloadBackup: %v", err)
	}
	data, err := os.ReadFile("manual-backup.db")
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "new-bytes" {
		t.Fatalf("data = %q, want the new content to have overwritten the old", data)
	}
}

func TestDownloadBackupStripsPathTraversalFromServerFilename(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// A malicious or compromised server suggesting an absolute path / parent
		// traversal must not make the CLI write outside the current directory.
		w.Header().Set("Content-Disposition", `attachment; filename="../../etc/evil.db"`)
		w.Write([]byte("bytes"))
	}))
	defer srv.Close()

	dir := t.TempDir()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	defer os.Chdir(wd)

	saved, err := newClient(srv.URL).downloadBackup("")
	if err != nil {
		t.Fatalf("downloadBackup: %v", err)
	}
	if saved != "evil.db" {
		t.Fatalf("saved = %q, want the traversal stripped down to the base filename", saved)
	}
	if _, err := os.Stat(filepath.Join(dir, "evil.db")); err != nil {
		t.Fatalf("expected file inside the working directory: %v", err)
	}
}

func TestDownloadBackupLeavesExistingFileIntactOnFailedCopy(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Declares far more bytes than it actually sends, so the client's
		// io.Copy fails partway through — simulating a dropped connection.
		w.Header().Set("Content-Length", "1000000")
		w.Write([]byte("partial"))
	}))
	defer srv.Close()

	dir := t.TempDir()
	dest := filepath.Join(dir, "existing.db")
	if err := os.WriteFile(dest, []byte("original-good-backup"), 0o644); err != nil {
		t.Fatal(err)
	}

	if _, err := newClient(srv.URL).downloadBackup(dest); err == nil {
		t.Fatal("expected an error from a truncated response body")
	}

	data, err := os.ReadFile(dest)
	if err != nil {
		t.Fatalf("original file should still exist: %v", err)
	}
	if string(data) != "original-good-backup" {
		t.Fatalf("original file was modified by the failed download: %q", data)
	}
	leftovers, _ := filepath.Glob(dest + ".*.download-tmp")
	if len(leftovers) != 0 {
		t.Fatalf("staging file should have been cleaned up, found: %v", leftovers)
	}
}

func TestDownloadBackupDoesNotFollowSymlinkAtPredictableStagingName(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("new-bytes"))
	}))
	defer srv.Close()

	dir := t.TempDir()
	dest := filepath.Join(dir, "out.db")
	canary := filepath.Join(dir, "canary-sensitive-file")
	if err := os.WriteFile(canary, []byte("do-not-touch"), 0o644); err != nil {
		t.Fatal(err)
	}
	// Plants a symlink at the naive fixed staging name ("<dest>.download-tmp")
	// pointing at a sensitive file. If the code used that fixed name, os.Create
	// would follow the symlink and truncate the canary in place — the point of
	// os.CreateTemp's randomized name is that an attacker can't know what name
	// to plant a symlink at.
	if err := os.Symlink(canary, dest+".download-tmp"); err != nil {
		t.Fatal(err)
	}

	if _, err := newClient(srv.URL).downloadBackup(dest); err != nil {
		t.Fatalf("downloadBackup: %v", err)
	}

	data, err := os.ReadFile(canary)
	if err != nil {
		t.Fatalf("canary file should still exist untouched: %v", err)
	}
	if string(data) != "do-not-touch" {
		t.Fatalf("canary file was modified — a predictable staging path was followed: %q", data)
	}
}

func TestDownloadBackupReturnsAPIErrorWhenNoneExists(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"no backup has been created yet"}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).downloadBackup(filepath.Join(t.TempDir(), "out.db")); err == nil {
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

func TestUploadImageSendsFileAsMultipart(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "photo.png")
	if err := os.WriteFile(path, []byte("fake-png-bytes"), 0o644); err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/api/v1/notes/note-1/images" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Fatalf("ParseMultipartForm: %v", err)
		}
		file, header, err := r.FormFile("file")
		if err != nil {
			t.Fatalf("FormFile: %v", err)
		}
		defer file.Close()
		data, _ := io.ReadAll(file)
		if string(data) != "fake-png-bytes" {
			t.Errorf("uploaded content = %q", data)
		}
		if header.Filename != "photo.png" {
			t.Errorf("filename = %q", header.Filename)
		}
		json.NewEncoder(w).Encode(map[string]string{"id": "img-1", "url": "/api/v1/images/img-1"})
	}))
	defer srv.Close()

	img, err := newClient(srv.URL).uploadImage("note-1", path)
	if err != nil {
		t.Fatalf("uploadImage: %v", err)
	}
	if img.ID != "img-1" || img.URL != "/api/v1/images/img-1" {
		t.Fatalf("unexpected result: %+v", img)
	}
}

func TestUploadImageReturnsAPIErrorOnMissingNote(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "photo.png")
	if err := os.WriteFile(path, []byte("bytes"), 0o644); err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"note not found"}`))
	}))
	defer srv.Close()

	if _, err := newClient(srv.URL).uploadImage("missing", path); err == nil {
		t.Fatal("expected an error for a 404 response")
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
