package http

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Этап 7: NewRouter must build without panicking and mount the API-key check
// on domain routes only.
func TestNewRouterAuth(t *testing.T) {
	const key = "0123456789abcdef0123456789abcdef"
	h := NewRouter(RouterDeps{
		Logger:        slog.New(slog.NewTextHandler(io.Discard, nil)),
		Health:        &HealthHandler{},
		Categories:    &CategoryHandler{},
		Datasets:      &DatasetHandler{},
		DatasetGroups: &DatasetGroupHandler{},
		Landscape:     &LandscapeHandler{},
		APIKey:        key,
	})
	cases := []struct {
		path, header string
		want         int
	}{
		{"/healthz", "", http.StatusOK},
		{"/docs", "", http.StatusNotFound},
		{"/openapi.yaml", "", http.StatusNotFound},
		{"/categories", "", http.StatusUnauthorized},
		{"/datasets", "wrong", http.StatusUnauthorized},
		{"/dataset-groups", "", http.StatusUnauthorized},
		{"/landscape/scenarios", "", http.StatusUnauthorized},
	}
	for _, tc := range cases {
		req := httptest.NewRequest(http.MethodGet, tc.path, nil)
		if tc.header != "" {
			req.Header.Set("X-API-Key", tc.header)
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != tc.want {
			t.Fatalf("%s: got %d, want %d", tc.path, rec.Code, tc.want)
		}
		if rec.Header().Get("X-Content-Type-Options") != "nosniff" {
			t.Fatalf("%s: security headers missing", tc.path)
		}
	}
}
