package main

import "testing"

func TestIsValidUUID(t *testing.T) {
	cases := []struct {
		id   string
		want bool
	}{
		{"2492fd58-f28c-4260-849a-4b764aafc933", true},
		{"2492FD58-F28C-4260-849A-4B764AAFC933", true},
		{"", false},
		{"not-a-uuid", false},
		{"2492fd58-f28c-4260-849a-4b764aafc93", false},   // one char short
		{"2492fd58f28c4260849a4b764aafc933", false},      // missing dashes
		{"2492fd58-f28c-4260-849a-4b764aafc933; rm -rf", false},
	}
	for _, c := range cases {
		if got := isValidUUID(c.id); got != c.want {
			t.Errorf("isValidUUID(%q) = %v, want %v", c.id, got, c.want)
		}
	}
}
