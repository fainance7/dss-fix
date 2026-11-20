package http

import (
	"errors"
	"net/http"

	"gis/internal/domain"
	"gis/pkg/httputil"
)

// respondDomainError maps a domain error to an HTTP status and writes a JSON
// error envelope.
func respondDomainError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, domain.ErrNotFound):
		httputil.WriteError(w, http.StatusNotFound, "not found")
	case errors.Is(err, domain.ErrValidation):
		httputil.WriteError(w, http.StatusUnprocessableEntity, err.Error())
	case errors.Is(err, domain.ErrConflict):
		httputil.WriteError(w, http.StatusConflict, "operation conflicts with existing data")
	default:
		httputil.WriteError(w, http.StatusInternalServerError, "internal server error")
	}
}
