package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/textproto"
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

// uploadImage uploads the local file at path as an attachment of noteID. The
// image's Content-Type is set explicitly to mimeType (rather than sniffed)
// since that's what the server's allowlist checks against. This bypasses
// do() rather than extending it, since do() always sends
// application/json — a multipart body needs its own boundary-specific
// Content-Type header instead.
func (c *client) uploadImage(noteID, path, mimeType string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()

	reader, writer := io.Pipe()
	w := multipart.NewWriter(writer)
	writeErr := make(chan error, 1)
	go func() {
		header := make(textproto.MIMEHeader)
		header.Set("Content-Disposition", fmt.Sprintf(`form-data; name="file"; filename="%s"`, quoteEscape(filepath.Base(path))))
		header.Set("Content-Type", mimeType)
		part, err := w.CreatePart(header)
		if err == nil {
			_, err = io.Copy(part, file)
		}
		if err == nil {
			err = w.WriteField("note_id", noteID)
		}
		if err == nil {
			err = w.Close()
		} else {
			_ = writer.CloseWithError(err)
		}
		writeErr <- err
	}()

	/* The multipart writer runs concurrently so the image is never buffered in memory. */
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/v1/images", reader)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", w.FormDataContentType())

	// Large uploads need a timeout separate from ordinary API calls.
	uploadHTTP := *c.http
	uploadHTTP.Timeout = 10 * time.Minute
	resp, err := uploadHTTP.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", &apiError{status: resp.StatusCode, body: string(respBody)}
	}

	var created struct {
		URL string `json:"url"`
	}
	if err := json.Unmarshal(respBody, &created); err != nil {
		return "", err
	}
	return created.URL, nil
}

// quoteEscape escapes a filename for use in a multipart Content-Disposition
// header — the same escaping mime/multipart applies internally in
// CreateFormFile, which we can't use here since it hardcodes
// application/octet-stream instead of letting us set mimeType.
func quoteEscape(s string) string {
	return strings.NewReplacer("\\", "\\\\", `"`, "\\\"").Replace(s)
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
