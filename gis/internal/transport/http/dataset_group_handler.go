package http

import (
	"io"
	"net/http"
	"strings"

	"gis/internal/domain"
	"gis/internal/service"
	"gis/pkg/httputil"

	"github.com/go-chi/chi/v5"
	"github.com/go-playground/validator/v10"
)

// DatasetGroupHandler serves the /dataset-groups routes.
type DatasetGroupHandler struct {
	svc      *service.DatasetGroupService
	validate *validator.Validate
}

// NewDatasetGroupHandler returns a DatasetGroupHandler.
func NewDatasetGroupHandler(svc *service.DatasetGroupService, validate *validator.Validate) *DatasetGroupHandler {
	return &DatasetGroupHandler{svc: svc, validate: validate}
}

// Register mounts the dataset-group routes on r.
func (h *DatasetGroupHandler) Register(r chi.Router) {
	r.Get("/", h.list)
	r.Post("/", h.create)
	r.Get("/{id}", h.get)
	r.Put("/{id}", h.update)
	r.Delete("/{id}", h.delete)
	r.Get("/{id}/latest", h.latest)
	r.Post("/{id}/sld", h.uploadSLD)
	r.Get("/{id}/sld", h.downloadSLD)
}

type datasetGroupRequest struct {
	Code        string `json:"code" validate:"required,max=255,slug"`
	Name        string `json:"name" validate:"required,max=255"`
	Description string `json:"description" validate:"max=2000"`
}

func (r datasetGroupRequest) toInput() service.DatasetGroupInput {
	return service.DatasetGroupInput{Code: r.Code, Name: r.Name, Description: r.Description}
}

func (h *DatasetGroupHandler) create(w http.ResponseWriter, r *http.Request) {
	req, err := httputil.DecodeJSON[datasetGroupRequest](w, r)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.validate.Struct(req); err != nil {
		httputil.WriteValidationErrors(w, err)
		return
	}

	group, err := h.svc.Create(r.Context(), req.toInput())
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusCreated, group)
}

func (h *DatasetGroupHandler) list(w http.ResponseWriter, r *http.Request) {
	var code *string
	if v := strings.TrimSpace(r.URL.Query().Get("code")); v != "" {
		code = &v
	}

	page, ok := parsePositiveIntQuery(w, r, "page", 1)
	if !ok {
		return
	}
	pageSize, ok := parsePositiveIntQuery(w, r, "page_size", service.DefaultPageSize)
	if !ok {
		return
	}

	result, err := h.svc.List(r.Context(), code, page, pageSize)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, newPaginated(result.Items, result.Page, result.PageSize, result.Total))
}

func (h *DatasetGroupHandler) get(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	group, err := h.svc.Get(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, group)
}

func (h *DatasetGroupHandler) update(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	req, err := httputil.DecodeJSON[datasetGroupRequest](w, r)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.validate.Struct(req); err != nil {
		httputil.WriteValidationErrors(w, err)
		return
	}

	group, err := h.svc.Update(r.Context(), id, req.toInput())
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, group)
}

// delete removes a group. A group still referenced by datasets yields 409.
func (h *DatasetGroupHandler) delete(w http.ResponseWriter, r *http.Request) {
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

// latest returns the freshest dataset of the group (greatest end_date, NULLs
// last, then newest), optionally narrowed by ?status= and ?file_type=.
func (h *DatasetGroupHandler) latest(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}

	var status *string
	if v := strings.TrimSpace(r.URL.Query().Get("status")); v != "" {
		if !domain.ValidDatasetStatus(v) {
			httputil.WriteError(w, http.StatusBadRequest, "invalid status")
			return
		}
		status = &v
	}

	var fileType *domain.FileType
	if v := strings.TrimSpace(r.URL.Query().Get("file_type")); v != "" {
		ft := domain.FileType(v)
		if !ft.Valid() {
			httputil.WriteError(w, http.StatusBadRequest, "file_type must be one of: vector_with_kato, vector, raster")
			return
		}
		fileType = &ft
	}

	dataset, err := h.svc.Latest(r.Context(), id, status, fileType)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, dataset)
}

func (h *DatasetGroupHandler) uploadSLD(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	if !parseUpload(w, r) {
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
		contentType = "application/xml"
	}

	group, err := h.svc.UploadSLD(r.Context(), id, service.UploadSLDInput{
		Filename:    header.Filename,
		ContentType: contentType,
		Size:        header.Size,
		Reader:      file,
	})
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, group)
}

func (h *DatasetGroupHandler) downloadSLD(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	group, obj, err := h.svc.DownloadSLD(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	defer obj.Close()

	contentType := "application/xml"
	if group.SldContentType != nil && *group.SldContentType != "" {
		contentType = *group.SldContentType
	}
	filename := "style.sld"
	if group.SldFilename != nil && *group.SldFilename != "" {
		filename = *group.SldFilename
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Disposition", `attachment; filename="`+filename+`"`)
	if _, err := io.Copy(w, obj); err != nil {
		// Headers are already sent; nothing useful to return to the client.
		return
	}
}
