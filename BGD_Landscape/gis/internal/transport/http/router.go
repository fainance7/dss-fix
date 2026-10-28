package http

import (
	"crypto/subtle"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"gis/pkg/httputil"
)

// RouterDeps holds the handlers and dependencies the router mounts.
type RouterDeps struct {
	Logger     *slog.Logger
	Health     *HealthHandler
	Categories *CategoryHandler
	Datasets   *DatasetHandler
	// DatasetGroups serves the /dataset-groups routes.
	DatasetGroups *DatasetGroupHandler
	// Landscape serves the /landscape routes.
	Landscape   *LandscapeHandler
	OpenAPISpec []byte
	// APIKey is required in the X-API-Key header on all routes except
	// /healthz, /readyz, /docs and /openapi.yaml.
	APIKey string
	// AuthDisabled skips the API-key check (local development only).
	AuthDisabled bool
}

// NewRouter builds the application's HTTP handler with middleware and routes.
func NewRouter(deps RouterDeps) http.Handler {
	r := chi.NewRouter()

	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(requestLogger(deps.Logger))
	r.Use(middleware.Recoverer)

	// Health/readiness endpoints — exempt from API key.
	r.Get("/healthz", deps.Health.Live)
	r.Get("/readyz", deps.Health.Ready)

	// OpenAPI 3.1.1 spec + Redoc documentation UI — exempt from API key.
	r.Get("/openapi.yaml", deps.openAPISpec)
	r.Get("/docs", docsUI)

	// Domain routes — always require X-API-Key unless auth was explicitly
	// disabled for local development. An empty key with auth enabled rejects
	// every request (fail closed) rather than leaving the API open.
	if !deps.AuthDisabled {
		r.Use(requireAPIKey(deps.APIKey))
	}

	r.Route("/categories", deps.Categories.Register)
	r.Route("/datasets", deps.Datasets.Register)
	r.Route("/dataset-groups", deps.DatasetGroups.Register)
	r.Route("/landscape", deps.Landscape.Register)

	return r
}

// requireAPIKey returns middleware that rejects requests without a valid
// X-API-Key header. The expected key is captured in a closure; comparison
// uses subtle.ConstantTimeCompare to resist timing side-channels.
func requireAPIKey(expected string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			got := r.Header.Get("X-API-Key")
			if expected == "" || got == "" || subtle.ConstantTimeCompare([]byte(got), []byte(expected)) != 1 {
				httputil.WriteError(w, http.StatusUnauthorized, "missing or invalid X-API-Key")
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
