package http

import (
	"net/http"
	"strconv"

	"gis/internal/service"
	"gis/pkg/httputil"

	"github.com/go-chi/chi/v5"
	"github.com/go-playground/validator/v10"
	"github.com/google/uuid"
)

// CategoryHandler serves the /categories routes.
type CategoryHandler struct {
	svc      *service.CategoryService
	validate *validator.Validate
}

// NewCategoryHandler returns a CategoryHandler.
func NewCategoryHandler(svc *service.CategoryService, validate *validator.Validate) *CategoryHandler {
	return &CategoryHandler{svc: svc, validate: validate}
}

// Register mounts the category routes on r.
func (h *CategoryHandler) Register(r chi.Router) {
	r.Get("/", h.list)
	r.Post("/", h.create)
	r.Get("/{id}", h.get)
	r.Put("/{id}", h.update)
	r.Delete("/{id}", h.delete)
}

type categoryRequest struct {
	ParentID    *string `json:"parent_id" validate:"omitempty,uuid"`
	Code        string  `json:"code" validate:"required,max=255,slug"`
	Name        string  `json:"name" validate:"required,max=255"`
	Description string  `json:"description" validate:"max=2000"`
}

func (r categoryRequest) toInput() (service.CategoryInput, error) {
	in := service.CategoryInput{Code: r.Code, Name: r.Name, Description: r.Description}
	if r.ParentID != nil {
		id, err := uuid.Parse(*r.ParentID)
		if err != nil {
			return in, err
		}
		in.ParentID = &id
	}
	return in, nil
}

func (h *CategoryHandler) create(w http.ResponseWriter, r *http.Request) {
	req, err := httputil.DecodeJSON[categoryRequest](w, r)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.validate.Struct(req); err != nil {
		httputil.WriteValidationErrors(w, err)
		return
	}
	in, _ := req.toInput()

	category, err := h.svc.Create(r.Context(), in)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusCreated, category)
}

func (h *CategoryHandler) list(w http.ResponseWriter, r *http.Request) {
	parentID, ok := parseOptionalUUIDQuery(w, r, "parent_id")
	if !ok {
		return
	}
	categories, err := h.svc.List(r.Context(), parentID)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, categories)
}

func (h *CategoryHandler) get(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	category, err := h.svc.Get(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, category)
}

func (h *CategoryHandler) update(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	req, err := httputil.DecodeJSON[categoryRequest](w, r)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.validate.Struct(req); err != nil {
		httputil.WriteValidationErrors(w, err)
		return
	}
	in, _ := req.toInput()

	category, err := h.svc.Update(r.Context(), id, in)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, category)
}

func (h *CategoryHandler) delete(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	if err := h.svc.Delete(r.Context(), id); err != nil {
		respondDomainError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// parseUUIDParam reads a UUID path parameter, writing a 400 if it is invalid.
func parseUUIDParam(w http.ResponseWriter, r *http.Request, name string) (uuid.UUID, bool) {
	id, err := uuid.Parse(chi.URLParam(r, name))
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid "+name)
		return uuid.Nil, false
	}
	return id, true
}

// parsePositiveIntQuery reads an optional positive integer query parameter,
// returning def when absent. A present but invalid value writes a 400.
func parsePositiveIntQuery(w http.ResponseWriter, r *http.Request, name string, def int) (int, bool) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return def, true
	}
	v, err := strconv.Atoi(raw)
	if err != nil || v < 1 {
		httputil.WriteError(w, http.StatusBadRequest, "invalid "+name)
		return 0, false
	}
	return v, true
}

// parseOptionalUUIDQuery reads an optional UUID query parameter. A missing value
// yields (nil, true); an invalid value writes a 400 and yields (nil, false).
func parseOptionalUUIDQuery(w http.ResponseWriter, r *http.Request, name string) (*uuid.UUID, bool) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return nil, true
	}
	id, err := uuid.Parse(raw)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid "+name)
		return nil, false
	}
	return &id, true
}
