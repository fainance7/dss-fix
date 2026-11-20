package http

import (
	"context"
	"net/http"

	"gis/pkg/httputil"
)

// ReadinessCheck reports whether a dependency is reachable.
type ReadinessCheck func(ctx context.Context) error

// HealthHandler serves liveness and readiness probes.
type HealthHandler struct {
	checks map[string]ReadinessCheck
}

// NewHealthHandler builds a HealthHandler with the given named readiness checks.
func NewHealthHandler(checks map[string]ReadinessCheck) *HealthHandler {
	return &HealthHandler{checks: checks}
}

// Live reports that the process is up.
func (h *HealthHandler) Live(w http.ResponseWriter, r *http.Request) {
	httputil.WriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// Ready runs all readiness checks and reports per-dependency status. It returns
// 503 if any check fails.
func (h *HealthHandler) Ready(w http.ResponseWriter, r *http.Request) {
	results := make(map[string]string, len(h.checks))
	ready := true
	for name, check := range h.checks {
		if err := check(r.Context()); err != nil {
			results[name] = "error: " + err.Error()
			ready = false
			continue
		}
		results[name] = "ok"
	}

	status := http.StatusOK
	if !ready {
		status = http.StatusServiceUnavailable
	}
	httputil.WriteJSON(w, status, map[string]any{
		"ready":      ready,
		"components": results,
	})
}
