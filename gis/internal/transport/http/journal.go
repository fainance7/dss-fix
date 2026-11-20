package http

import (
	"context"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5/middleware"
)

// SecurityEvent mirrors the journal record written by the transport layer.
type SecurityEvent struct {
	Event      string
	RemoteAddr string
	Method     string
	Path       string
	Status     int
	Detail     string
}

// Journal receives security events for the append-only security journal
// (Этап 7, ТЗ п. 4.8.3).
type Journal interface {
	RecordEvent(ctx context.Context, e SecurityEvent) error
}

func recordEvent(j Journal, log *slog.Logger, r *http.Request, e SecurityEvent) {
	if j == nil {
		return
	}
	// Detached from the request so a client disconnect cannot drop the record.
	if err := j.RecordEvent(context.WithoutCancel(r.Context()), e); err != nil && log != nil {
		log.Error("security journal write failed", "event", e.Event, "error", err)
	}
}

// journalWrites records every data-changing request (all methods except
// GET/HEAD) with its outcome in the security journal.
func journalWrites(j Journal, log *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if j == nil || r.Method == http.MethodGet || r.Method == http.MethodHead {
				next.ServeHTTP(w, r)
				return
			}
			ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
			next.ServeHTTP(ww, r)
			recordEvent(j, log, r, SecurityEvent{
				Event: "data_change", RemoteAddr: r.RemoteAddr, Method: r.Method,
				Path: r.URL.Path, Status: ww.Status(),
			})
		})
	}
}
