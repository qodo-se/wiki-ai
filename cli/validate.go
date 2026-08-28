package main

import (
	"fmt"
	"os"
	"regexp"
)

var uuidPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

func isValidUUID(id string) bool {
	return uuidPattern.MatchString(id)
}

// requireUUID exits with a usage error if id isn't a well-formed UUID,
// instead of forwarding a malformed value into the API request path.
func requireUUID(id string) string {
	if !isValidUUID(id) {
		fmt.Fprintf(os.Stderr, "error: %q is not a valid uuid\n", id)
		os.Exit(1)
	}
	return id
}
