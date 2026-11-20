// Package http wires the chi router and runs the HTTP server with graceful
// shutdown.
package http

import (
	"context"
	"errors"
	"log/slog"
	"net/http"

	"gis/internal/config"
)

// Server runs the application's HTTP server.
type Server struct {
	srv             *http.Server
	shutdownTimeout config.HTTPConfig
	log             *slog.Logger
}

// NewServer builds an *http.Server from the config and handler.
func NewServer(cfg config.HTTPConfig, handler http.Handler, log *slog.Logger) *Server {
	return &Server{
		srv: &http.Server{
			Addr:              cfg.Addr(),
			Handler:           handler,
			ReadHeaderTimeout: cfg.ReadHeaderTimeout,
			ReadTimeout:       cfg.ReadTimeout,
			WriteTimeout:      cfg.WriteTimeout,
			IdleTimeout:       cfg.IdleTimeout,
		},
		shutdownTimeout: cfg,
		log:             log,
	}
}

// Run starts serving and blocks until ctx is cancelled, then shuts down
// gracefully within the configured timeout.
func (s *Server) Run(ctx context.Context) error {
	errCh := make(chan error, 1)
	go func() {
		s.log.Info("http server listening", "addr", s.srv.Addr)
		if err := s.srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
			return
		}
		errCh <- nil
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), s.shutdownTimeout.ShutdownTimeout)
		defer cancel()
		s.log.Info("http server shutting down")
		return s.srv.Shutdown(shutdownCtx)
	}
}
