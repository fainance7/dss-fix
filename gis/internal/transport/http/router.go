package http

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
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
}

// NewRouter builds the application's HTTP handler with middleware and routes.
func NewRouter(deps RouterDeps) http.Handler {
	r := chi.NewRouter()

	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(requestLogger(deps.Logger))
	r.Use(middleware.Recoverer)

	// Health/readiness endpoints.
	r.Get("/healthz", deps.Health.Live)
	r.Get("/readyz", deps.Health.Ready)

	// OpenAPI 3.1.1 spec + Redoc documentation UI.
	r.Get("/openapi.yaml", deps.openAPISpec)
	r.Get("/docs", docsUI)

	// Domain routes. Add future feature routers here.
	r.Route("/categories", deps.Categories.Register)
	r.Route("/datasets", deps.Datasets.Register)
	r.Route("/dataset-groups", deps.DatasetGroups.Register)
	r.Route("/landscape", deps.Landscape.Register)

	return r
}
