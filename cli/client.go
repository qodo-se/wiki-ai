package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type client struct {
	baseURL string
	http    *http.Client
}

func newClient(baseURL string) *client {
	return &client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: 10 * time.Second},
	}
}

// apiError is returned when the server responds with a non-2xx status.
type apiError struct {
	status int
	body   string
}

func (e *apiError) Error() string {
	return fmt.Sprintf("server returned %d: %s", e.status, sanitizeForTerminal(e.body))
}

func (c *client) do(method, path string, query url.Values, body []byte) ([]byte, error) {
	u := c.baseURL + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}

	var reqBody io.Reader
	if body != nil {
		reqBody = bytes.NewReader(body)
	}

	req, err := http.NewRequest(method, u, reqBody)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &apiError{status: resp.StatusCode, body: string(respBody)}
	}
	return respBody, nil
}

type noteSummary struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	Path      string `json:"path"`
	Preview   string `json:"preview"`
	CreatedAt string `json:"created_at"`
}

type noteMeta struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	Path      string `json:"path"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}

type searchHit struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	Path      string `json:"path"`
	Preview   string `json:"preview"`
	CreatedAt string `json:"created_at"`
}

type noteListResponse struct {
	Items  []noteSummary `json:"items"`
	Total  int           `json:"total"`
	Limit  int           `json:"limit"`
	Offset int           `json:"offset"`
}

func (c *client) listNotes(limit, offset int) (*noteListResponse, error) {
	body, err := c.do(http.MethodGet, "/api/v1/notes", url.Values{
		"limit":  {fmt.Sprint(limit)},
		"offset": {fmt.Sprint(offset)},
	}, nil)
	if err != nil {
		return nil, err
	}
	var list noteListResponse
	if err := json.Unmarshal(body, &list); err != nil {
		return nil, err
	}
	return &list, nil
}

func (c *client) getNoteContent(id string) (string, error) {
	body, err := c.do(http.MethodGet, "/api/v1/notes/"+id, nil, nil)
	if err != nil {
		return "", err
	}
	return string(body), nil
}

func (c *client) getNoteMeta(id string) (*noteMeta, error) {
	body, err := c.do(http.MethodGet, "/api/v1/notes/"+id+"/meta", nil, nil)
	if err != nil {
		return nil, err
	}
	var meta noteMeta
	if err := json.Unmarshal(body, &meta); err != nil {
		return nil, err
	}
	return &meta, nil
}

// createNote asks the server to allocate a new note id. The client never
// picks the id itself.
func (c *client) createNote(content, path string) (string, error) {
	payload, err := json.Marshal(map[string]string{
		"content": content,
		"path":    path,
	})
	if err != nil {
		return "", err
	}
	body, err := c.do(http.MethodPost, "/api/v1/notes", nil, payload)
	if err != nil {
		return "", err
	}
	var created struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(body, &created); err != nil {
		return "", err
	}
	if !isValidUUID(created.ID) {
		return "", fmt.Errorf("server returned an invalid note id: %q", sanitizeForTerminal(created.ID))
	}
	return created.ID, nil
}

func (c *client) putNote(id, content, path string) error {
	payload, err := json.Marshal(map[string]string{
		"content": content,
		"path":    path,
	})
	if err != nil {
		return err
	}
	_, err = c.do(http.MethodPut, "/api/v1/notes/"+id, nil, payload)
	return err
}

func (c *client) deleteNote(id string) error {
	_, err := c.do(http.MethodDelete, "/api/v1/notes/"+id, nil, nil)
	return err
}

func (c *client) search(query string, limit int) ([]searchHit, error) {
	body, err := c.do(http.MethodGet, "/api/v1/search", url.Values{
		"q":     {query},
		"limit": {fmt.Sprint(limit)},
	}, nil)
	if err != nil {
		return nil, err
	}
	var hits []searchHit
	if err := json.Unmarshal(body, &hits); err != nil {
		return nil, err
	}
	return hits, nil
}

type backupMeta struct {
	Filename string `json:"filename"`
	Size     int64  `json:"size"`
}

func (c *client) createBackup() (*backupMeta, error) {
	body, err := c.do(http.MethodPost, "/api/v1/backup", nil, nil)
	if err != nil {
		return nil, err
	}
	var meta backupMeta
	if err := json.Unmarshal(body, &meta); err != nil {
		return nil, err
	}
	return &meta, nil
}

// downloadLatestBackup fetches the most recent backup and saves it to destPath,
// returning the path actually written. If destPath is "", the filename is
// derived from the response's Content-Disposition header. This bypasses do()
// rather than extending it, since do() always reads the whole body into memory
// as a JSON response — a backup file should stream straight to disk instead.
func (c *client) downloadLatestBackup(destPath string) (string, error) {
	req, err := http.NewRequest(http.MethodGet, c.baseURL+"/api/v1/backup/latest", nil)
	if err != nil {
		return "", err
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return "", &apiError{status: resp.StatusCode, body: string(respBody)}
	}

	if destPath == "" {
		// filepath.Base strips any directory components the server (or a
		// man-in-the-middle) might smuggle into Content-Disposition's filename
		// (e.g. "../../etc/passwd" or an absolute path) — never trust a
		// server-supplied filename as a raw local path.
		destPath = filepath.Base(filenameFromContentDisposition(resp.Header.Get("Content-Disposition")))
		if destPath == "" || destPath == "." || destPath == string(filepath.Separator) {
			destPath = "wiki-backup.db"
		}
	}

	f, err := os.Create(destPath)
	if err != nil {
		return "", err
	}
	defer f.Close()

	if _, err := io.Copy(f, resp.Body); err != nil {
		return "", err
	}
	return destPath, nil
}

func filenameFromContentDisposition(header string) string {
	if header == "" {
		return ""
	}
	_, params, err := mime.ParseMediaType(header)
	if err != nil {
		return ""
	}
	return params["filename"]
}

func (c *client) semanticSearch(query string, limit int) ([]searchHit, error) {
	body, err := c.do(http.MethodGet, "/api/v1/search/semantic", url.Values{
		"q":     {query},
		"limit": {fmt.Sprint(limit)},
	}, nil)
	if err != nil {
		return nil, err
	}
	var hits []searchHit
	if err := json.Unmarshal(body, &hits); err != nil {
		return nil, err
	}
	return hits, nil
}
