package http

import (
	"errors"
	"io"
	"net/http"

	"gis/internal/landscape"
	"gis/internal/service"
	"gis/pkg/httputil"

	"github.com/go-chi/chi/v5"
	"github.com/go-playground/validator/v10"
	"github.com/google/uuid"
)

// LandscapeHandler serves the /landscape routes.
type LandscapeHandler struct {
	svc      *service.LandscapeService
	validate *validator.Validate
}

// NewLandscapeHandler returns a LandscapeHandler.
func NewLandscapeHandler(svc *service.LandscapeService, validate *validator.Validate) *LandscapeHandler {
	return &LandscapeHandler{svc: svc, validate: validate}
}

// Register mounts the Landscape Program routes on r.
func (h *LandscapeHandler) Register(r chi.Router) {
	r.Get("/slots", h.slots)
	r.Post("/slots/{code}", h.uploadSlot)
	r.Get("/runs", h.listRuns)
	r.Post("/runs", h.createRun)
	r.Get("/runs/{id}", h.getRun)
	r.Get("/results", h.results)
}

// runRequest is the body of POST /landscape/runs. Overrides are decoded as
// strings so a malformed id is a 422 instead of a 400 on the whole body.
type runRequest struct {
	FromStage *int              `json:"from_stage" validate:"omitempty,gte=0,lte=3"`
	Overrides map[string]string `json:"overrides" validate:"omitempty,dive,uuid"`
	Params    *landscape.Params `json:"params"`
}

// slots lists every leaf input slot with the dataset currently filling it.
func (h *LandscapeHandler) slots(w http.ResponseWriter, r *http.Request) {
	slots, err := h.svc.Slots(r.Context())
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, slots)
}

// uploadSlot stores a raster in the slot's dataset group under the landscape
// category. An unknown slot code yields 404.
func (h *LandscapeHandler) uploadSlot(w http.ResponseWriter, r *http.Request) {
	code := chi.URLParam(r, "code")

	if err := r.ParseMultipartForm(maxUploadBytes); err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "request must be multipart/form-data")
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "file is required")
		return
	}
	defer file.Close()

	contentType := header.Header.Get("Content-Type")
	if contentType == "" {
		contentType = "image/tiff"
	}

	dataset, err := h.svc.UploadToSlot(r.Context(), code, service.SlotUploadInput{
		Filename:    header.Filename,
		ContentType: contentType,
		Size:        header.Size,
		Reader:      file,
		Name:        r.FormValue("name"),
		StartDate:   optionalFormValue(r, "start_date"),
		EndDate:     optionalFormValue(r, "end_date"),
	})
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusCreated, dataset)
}

// decodeRunRequest reads the run body. The whole body is optional — every field
// has a default — so an empty body is the zero request (a default incremental
// run), not an error. A malformed body writes a 400 and reports false.
func decodeRunRequest(w http.ResponseWriter, r *http.Request) (runRequest, bool) {
	req, err := httputil.DecodeJSON[runRequest](w, r)
	switch {
	case errors.Is(err, io.EOF):
		return runRequest{}, true
	case err != nil:
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return runRequest{}, false
	}
	return req, true
}

// createRun queues a Landscape Program run and returns it with 202 Accepted;
// the worker executes it asynchronously.
func (h *LandscapeHandler) createRun(w http.ResponseWriter, r *http.Request) {
	req, ok := decodeRunRequest(w, r)
	if !ok {
		return
	}
	if err := h.validate.Struct(req); err != nil {
		httputil.WriteValidationErrors(w, err)
		return
	}

	overrides := make(map[string]uuid.UUID, len(req.Overrides))
	for group, raw := range req.Overrides {
		id, err := uuid.Parse(raw)
		if err != nil {
			httputil.WriteError(w, http.StatusUnprocessableEntity, "overrides["+group+"] must be a valid UUID")
			return
		}
		overrides[group] = id
	}

	in := service.RunInput{FromStage: req.FromStage, Overrides: overrides}
	if req.Params != nil {
		in.Params = *req.Params
	}

	run, err := h.svc.CreateRun(r.Context(), in)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusAccepted, run)
}

// listRuns returns a page of runs, newest first.
func (h *LandscapeHandler) listRuns(w http.ResponseWriter, r *http.Request) {
	page, ok := parsePositiveIntQuery(w, r, "page", 1)
	if !ok {
		return
	}
	pageSize, ok := parsePositiveIntQuery(w, r, "page_size", service.DefaultPageSize)
	if !ok {
		return
	}

	result, err := h.svc.ListRuns(r.Context(), page, pageSize)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, newPaginated(result.Items, result.Page, result.PageSize, result.Total))
}

// getRun returns one run with its per-node execution state.
func (h *LandscapeHandler) getRun(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	run, err := h.svc.GetRun(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, run)
}

// results lists every output group with its freshest ready dataset and the URLs
// to render it.
func (h *LandscapeHandler) results(w http.ResponseWriter, r *http.Request) {
	results, err := h.svc.Results(r.Context())
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, results)
}
