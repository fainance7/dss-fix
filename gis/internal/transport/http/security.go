package http

import (
	"errors"
	"net/http"

	"gis/pkg/httputil"
)

// maxUploadTotalBytes caps the whole multipart request body (Этап 7, ПТБ-16).
// It matches the parser's own read limit, so nothing larger could be processed
// anyway; without it the body was spooled to disk with no bound.
const maxUploadTotalBytes = 256 << 20

// parseUpload bounds the request body and parses the multipart form. It writes
// the error response itself and returns false on failure.
func parseUpload(w http.ResponseWriter, r *http.Request) bool {
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadTotalBytes)
	if err := r.ParseMultipartForm(maxUploadBytes); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			httputil.WriteError(w, http.StatusRequestEntityTooLarge, "request body is too large")
			return false
		}
		httputil.WriteError(w, http.StatusBadRequest, "request must be multipart/form-data")
		return false
	}
	return true
}

// securityHeaders sets defensive response headers on every API response
// (Этап 7, ПТБ-05). The API returns JSON, files and GeoTIFFs only, so the
// strictest CSP applies; the optional /docs page sets its own.
func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		h.Set("Cache-Control", "no-store")
		next.ServeHTTP(w, r)
	})
}
