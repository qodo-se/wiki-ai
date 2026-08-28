package main

import "testing"

func TestSanitizeForTerminal(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"plain text unchanged", "hello world", `hello world`},
		{"escapes ESC (ANSI/OSC injection)", "\x1b[31mfake error\x1b[0m", `\x1b[31mfake error\x1b[0m`},
		{"escapes newline and carriage return", "line1\nline2\r", `line1\x0aline2\x0d`},
		{"escapes tab", "a\tb", `a\x09b`},
		{"escapes DEL", "a\x7fb", `a\x7fb`},
		{"escapes C1 control range", "ab", `a\x9bb`},
		{"leaves unicode alone", "héllo 世界", "héllo 世界"},
		{"escapes RTL override (Trojan Source)", "safe\u202eevil", `safe\u202eevil`},
		{"escapes isolate controls", "a\u2066b\u2069c", `a\u2066b\u2069c`},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := sanitizeForTerminal(c.in); got != c.want {
				t.Errorf("sanitizeForTerminal(%q) = %q, want %q", c.in, got, c.want)
			}
		})
	}
}
