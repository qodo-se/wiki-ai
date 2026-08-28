package main

import (
	"fmt"
	"strings"
)

// isBidiControl reports whether r is one of the Unicode directional-override
// / isolate control points that can be used to visually reorder text
// ("Trojan Source"-style attacks).
func isBidiControl(r rune) bool {
	switch r {
	case 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, // LRE, RLE, PDF, LRO, RLO
		0x2066, 0x2067, 0x2068, 0x2069: // LRI, RLI, FSI, PDI
		return true
	}
	return false
}

// sanitizeForTerminal escapes control characters (including ANSI/OSC escape
// sequences and Unicode bidi override/isolate controls) in server-derived
// text before it's printed as part of structured output (list/search/meta).
// Note content — and note IDs, which the API accepts as arbitrary strings —
// is unauthenticated and unrestricted server-side, so these fields could
// otherwise be used to spoof or manipulate the terminal.
func sanitizeForTerminal(s string) string {
	var b strings.Builder
	for _, r := range s {
		switch {
		case r == 0x7f, r < 0x20, r >= 0x80 && r <= 0x9f:
			fmt.Fprintf(&b, "\\x%02x", r)
		case isBidiControl(r):
			fmt.Fprintf(&b, "\\u%04x", r)
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}
