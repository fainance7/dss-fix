package http

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"gis/internal/config"
)

// Audit C-2: the API-key middleware must fail closed.
func TestRequireAPIKey(t *testing.T) {
	ok := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
	const key = "0123456789abcdef0123456789abcdef"

	cases := []struct {
		name     string
		expected string
		header   string
		want     int
	}{
		{"valid key", key, key, http.StatusNoContent},
		{"missing header", key, "", http.StatusUnauthorized},
		{"wrong key", key, "wrong", http.StatusUnauthorized},
		{"empty expected key fails closed", "", "", http.StatusUnauthorized},
		{"empty expected key rejects any header", "", "anything", http.StatusUnauthorized},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/datasets", nil)
			if tc.header != "" {
				req.Header.Set("X-API-Key", tc.header)
			}
			rec := httptest.NewRecorder()
			requireAPIKey(tc.expected)(ok).ServeHTTP(rec, req)
			if rec.Code != tc.want {
				t.Fatalf("got %d, want %d", rec.Code, tc.want)
			}
		})
	}
}

func TestValidateAuth(t *testing.T) {
	cases := []struct {
		name    string
		cfg     config.HTTPConfig
		wantErr bool
	}{
		{"empty key refused", config.HTTPConfig{}, true},
		{"short key refused", config.HTTPConfig{APIKey: "short"}, true},
		{"strong key accepted", config.HTTPConfig{APIKey: "0123456789abcdef0123456789abcdef"}, false},
		{"explicit dev opt-out accepted", config.HTTPConfig{AuthDisabled: true}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.cfg.ValidateAuth(); (err != nil) != tc.wantErr {
				t.Fatalf("ValidateAuth() err = %v, wantErr %v", err, tc.wantErr)
			}
		})
	}
}
