package main

// imageExtToMIME maps a file extension to the Content-Type wiki-cli sends
// when uploading it, matching the server's allowed image types
// (backend/routes/images.py's ALLOWED_MIME_TYPES).
var imageExtToMIME = map[string]string{
	".png":  "image/png",
	".jpg":  "image/jpeg",
	".jpeg": "image/jpeg",
	".gif":  "image/gif",
	".webp": "image/webp",
}
