package http

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"gis/internal/domain"
	"gis/internal/service"
	"gis/pkg/httputil"

	"github.com/go-chi/chi/v5"
	"github.com/go-playground/validator/v10"
	"github.com/google/uuid"
)

// maxUploadBytes caps the in-memory portion of a multipart upload (64 MiB).
const maxUploadBytes = 64 << 20

// DatasetHandler serves the /datasets routes.
type DatasetHandler struct {
	svc      *service.DatasetService
	validate *validator.Validate
}

// NewDatasetHandler returns a DatasetHandler.
func NewDatasetHandler(svc *service.DatasetService, validate *validator.Validate) *DatasetHandler {
	return &DatasetHandler{svc: svc, validate: validate}
}

// Register mounts the dataset routes on r.
func (h *DatasetHandler) Register(r chi.Router) {
	r.Get("/", h.list)
	r.Post("/", h.upload)
	r.Get("/{id}", h.get)
	r.Put("/{id}", h.update)
	r.Patch("/{id}", h.patch)
	r.Get("/{id}.geojson", h.geojson)
	r.Get("/{id}.cog", h.cog)
	r.Get("/{id}/status", h.status)
	r.Get("/{id}/download", h.download)
	r.Post("/{id}/sld", h.uploadSLD)
	r.Get("/{id}/sld", h.downloadSLD)
	r.Post("/{id}/mapping", h.mapping)
	r.Get("/{id}/observations", h.observations)
	r.Delete("/{id}", h.delete)
}

// status long-polls the dataset's processing status. With ?current=<status> it
// holds the request until the status changes (or ?wait=<seconds> elapses,
// default 25, max 60); without it, it returns the current status immediately.
func (h *DatasetHandler) status(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}

	current := r.URL.Query().Get("current")

	var wait time.Duration
	if raw := r.URL.Query().Get("wait"); raw != "" {
		secs, err := strconv.Atoi(raw)
		if err != nil || secs < 0 {
			httputil.WriteError(w, http.StatusBadRequest, "invalid wait")
			return
		}
		wait = time.Duration(secs) * time.Second
	}

	info, err := h.svc.WaitForStatus(r.Context(), id, current, wait)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, info)
}

type yearColumnInput struct {
	Column    string  `json:"column" validate:"required"`
	StartDate string  `json:"start_date" validate:"required,datetime=2006-01-02"`
	EndDate   *string `json:"end_date" validate:"omitempty,datetime=2006-01-02"`
}

type mappingRequest struct {
	KatoColumn  string            `json:"kato_column" validate:"required"`
	YearColumns []yearColumnInput `json:"year_columns" validate:"required,min=1,dive"`
}

// mapping saves the KATO column and year-column mapping for a vector_with_kato
// dataset, moving it to ready.
func (h *DatasetHandler) mapping(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	req, err := httputil.DecodeJSON[mappingRequest](w, r)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.validate.Struct(req); err != nil {
		httputil.WriteValidationErrors(w, err)
		return
	}

	in := service.MappingInput{KatoColumn: req.KatoColumn}
	for _, yc := range req.YearColumns {
		in.YearColumns = append(in.YearColumns, domain.YearColumn{Column: yc.Column, StartDate: yc.StartDate, EndDate: yc.EndDate})
	}

	dataset, err := h.svc.SaveMapping(r.Context(), id, in)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, dataset)
}

func (h *DatasetHandler) upload(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(maxUploadBytes); err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "request must be multipart/form-data")
		return
	}

	categoryRaw := r.FormValue("category_id")
	categoryID, err := uuid.Parse(categoryRaw)
	if err != nil {
		httputil.WriteError(w, http.StatusUnprocessableEntity, "category_id must be a valid UUID")
		return
	}

	fileType := domain.FileType(r.FormValue("file_type"))
	if !fileType.Valid() {
		httputil.WriteError(w, http.StatusUnprocessableEntity, "file_type must be one of: vector_with_kato, vector, raster")
		return
	}

	meta, ok := optionalJSONFormValue(w, r, "meta")
	if !ok {
		return
	}

	automated, err := optionalBoolFormValue(r, "automated")
	if err != nil {
		httputil.WriteError(w, http.StatusUnprocessableEntity, "automated must be a boolean")
		return
	}

	// The dataset group is optional; the period is validated by the service.
	var groupID *uuid.UUID
	if v := optionalFormValue(r, "dg_id"); v != nil {
		gid, err := uuid.Parse(*v)
		if err != nil {
			httputil.WriteError(w, http.StatusUnprocessableEntity, "dg_id must be a valid UUID")
			return
		}
		groupID = &gid
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "file is required")
		return
	}
	defer file.Close()

	contentType := header.Header.Get("Content-Type")
	if contentType == "" {
		contentType = "application/octet-stream"
	}

	dataset, err := h.svc.Upload(r.Context(), service.UploadInput{
		CategoryID:     categoryID,
		Name:           r.FormValue("name"),
		Description:    optionalFormValue(r, "description"),
		Unit:           optionalFormValue(r, "unit"),
		Meta:           meta,
		Automated:      automated,
		Filename:       header.Filename,
		FileType:       fileType,
		ContentType:    contentType,
		Size:           header.Size,
		Reader:         file,
		DatasetGroupID: groupID,
		StartDate:      optionalFormValue(r, "start_date"),
		EndDate:        optionalFormValue(r, "end_date"),
	})
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusCreated, dataset)
}

// optionalFormValue returns a pointer to a trimmed form value, or nil when the
// field is absent or blank, so nullable columns stay NULL.
func optionalFormValue(r *http.Request, name string) *string {
	v := strings.TrimSpace(r.FormValue(name))
	if v == "" {
		return nil
	}
	return &v
}

// optionalJSONFormValue reads a form field expected to contain JSON. A blank
// value yields (nil, true); invalid JSON writes a 422 and yields (nil, false).
func optionalJSONFormValue(w http.ResponseWriter, r *http.Request, name string) (json.RawMessage, bool) {
	v := strings.TrimSpace(r.FormValue(name))
	if v == "" {
		return nil, true
	}
	if !json.Valid([]byte(v)) {
		httputil.WriteError(w, http.StatusUnprocessableEntity, name+" must be valid JSON")
		return nil, false
	}
	return json.RawMessage(v), true
}

// optionalBoolFormValue parses an optional boolean form field, defaulting to
// false when the field is absent or blank.
func optionalBoolFormValue(r *http.Request, name string) (bool, error) {
	v := strings.TrimSpace(r.FormValue(name))
	if v == "" {
		return false, nil
	}
	return strconv.ParseBool(v)
}

func (h *DatasetHandler) list(w http.ResponseWriter, r *http.Request) {
	categoryID, ok := parseOptionalUUIDQuery(w, r, "category_id")
	if !ok {
		return
	}
	var categoryCode *string
	if v := strings.TrimSpace(r.URL.Query().Get("category_code")); v != "" {
		categoryCode = &v
	}

	groupID, ok := parseOptionalUUIDQuery(w, r, "dg_id")
	if !ok {
		return
	}

	filter := domain.DatasetFilter{CategoryID: categoryID, DatasetGroupID: groupID}

	for _, b := range []struct {
		name string
		dst  **string
	}{
		{"start_date_from", &filter.StartDateFrom},
		{"start_date_to", &filter.StartDateTo},
		{"end_date_from", &filter.EndDateFrom},
		{"end_date_to", &filter.EndDateTo},
	} {
		v := strings.TrimSpace(r.URL.Query().Get(b.name))
		if v == "" {
			continue
		}
		if _, err := time.Parse("2006-01-02", v); err != nil {
			httputil.WriteError(w, http.StatusBadRequest, b.name+" must be YYYY-MM-DD")
			return
		}
		*b.dst = &v
	}

	if v := strings.TrimSpace(r.URL.Query().Get("file_type")); v != "" {
		ft := domain.FileType(v)
		if !ft.Valid() {
			httputil.WriteError(w, http.StatusBadRequest, "file_type must be one of: vector_with_kato, vector, raster")
			return
		}
		filter.FileType = &ft
	}

	if v := strings.TrimSpace(r.URL.Query().Get("automated")); v != "" {
		b, err := strconv.ParseBool(v)
		if err != nil {
			httputil.WriteError(w, http.StatusBadRequest, "automated must be a boolean")
			return
		}
		filter.Automated = &b
	}

	if v := strings.TrimSpace(r.URL.Query().Get("status")); v != "" {
		if !domain.ValidDatasetStatus(v) {
			httputil.WriteError(w, http.StatusBadRequest, "invalid status")
			return
		}
		filter.Status = &v
	}

	page, ok := parsePositiveIntQuery(w, r, "page", 1)
	if !ok {
		return
	}
	pageSize, ok := parsePositiveIntQuery(w, r, "page_size", service.DefaultPageSize)
	if !ok {
		return
	}

	res, err := h.svc.ListSummaries(r.Context(), filter, categoryCode, page, pageSize)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, newPaginated(res.Items, res.Page, res.PageSize, res.Total))
}

func (h *DatasetHandler) observations(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	page, ok := parsePositiveIntQuery(w, r, "page", 1)
	if !ok {
		return
	}
	pageSize, ok := parsePositiveIntQuery(w, r, "page_size", service.DefaultPageSize)
	if !ok {
		return
	}

	var katoCode *string
	if v := strings.TrimSpace(r.URL.Query().Get("kato_code")); v != "" {
		katoCode = &v
	}

	res, err := h.svc.ListObservations(r.Context(), id, katoCode, page, pageSize)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, newPaginated(res.Items, res.Page, res.PageSize, res.Total))
}

func (h *DatasetHandler) get(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	dataset, err := h.svc.Get(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, dataset)
}

// datasetPutRequest is the full JSON body for PUT /datasets/{id}: every
// editable field is required. Description and Unit are pointers so an empty
// string is distinguishable from an absent field; a blank value clears the
// (nullable) column. File-related and lifecycle fields are not editable here.
type datasetPutRequest struct {
	CategoryID  string  `json:"category_id" validate:"required,uuid"`
	Name        string  `json:"name" validate:"required,max=255"`
	Description *string `json:"description" validate:"required"`
	Unit        *string `json:"unit" validate:"required"`
	// DgID, StartDate and EndDate are optional; omitting them (or sending null
	// or a blank string) clears the corresponding nullable column.
	DgID      *string `json:"dg_id" validate:"omitempty,uuid"`
	StartDate *string `json:"start_date" validate:"omitempty,datetime=2006-01-02"`
	EndDate   *string `json:"end_date" validate:"omitempty,datetime=2006-01-02"`
}

// update replaces a dataset's editable metadata (category, name, description,
// unit, dataset group, period).
func (h *DatasetHandler) update(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	req, err := httputil.DecodeJSON[datasetPutRequest](w, r)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := h.validate.Struct(req); err != nil {
		httputil.WriteValidationErrors(w, err)
		return
	}
	categoryID, err := uuid.Parse(req.CategoryID)
	if err != nil {
		httputil.WriteError(w, http.StatusUnprocessableEntity, "category_id must be a valid UUID")
		return
	}

	var groupID *uuid.UUID
	if raw := optionalPtr(req.DgID); raw != nil {
		gid, err := uuid.Parse(*raw)
		if err != nil {
			httputil.WriteError(w, http.StatusUnprocessableEntity, "dg_id must be a valid UUID")
			return
		}
		groupID = &gid
	}

	dataset, err := h.svc.Update(r.Context(), id, service.DatasetUpdateInput{
		CategoryID:     categoryID,
		Name:           strings.TrimSpace(req.Name),
		Description:    normalizeOptional(*req.Description),
		Unit:           normalizeOptional(*req.Unit),
		DatasetGroupID: groupID,
		StartDate:      optionalPtr(req.StartDate),
		EndDate:        optionalPtr(req.EndDate),
	})
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, dataset)
}

// datasetPatchFields lists the JSON keys accepted by PATCH /datasets/{id}.
var datasetPatchFields = map[string]struct{}{
	"category_id": {}, "name": {}, "description": {}, "unit": {},
	"dg_id": {}, "start_date": {}, "end_date": {},
}

// patch applies a sparse update to a dataset's editable metadata (category,
// name, description, unit, dg_id, start_date, end_date): only fields present in
// the body are changed. Sending an explicit null for a nullable field clears
// that column.
func (h *DatasetHandler) patch(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	raw, err := httputil.DecodeJSON[map[string]json.RawMessage](w, r)
	if err != nil {
		httputil.WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if len(raw) == 0 {
		httputil.WriteError(w, http.StatusBadRequest, "request body must include at least one field")
		return
	}

	var in service.DatasetPatchInput
	for key, val := range raw {
		if _, ok := datasetPatchFields[key]; !ok {
			httputil.WriteError(w, http.StatusUnprocessableEntity, "unknown field: "+key)
			return
		}
		switch key {
		case "category_id":
			var s string
			if err := json.Unmarshal(val, &s); err != nil || s == "" {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "category_id must be a valid UUID")
				return
			}
			cid, err := uuid.Parse(s)
			if err != nil {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "category_id must be a valid UUID")
				return
			}
			in.CategoryID = &cid
		case "name":
			var s string
			if err := json.Unmarshal(val, &s); err != nil || strings.TrimSpace(s) == "" {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "name must not be blank")
				return
			}
			name := strings.TrimSpace(s)
			in.Name = &name
		case "description":
			desc, err := decodeOptionalPatchString(val)
			if err != nil {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "description must be a string or null")
				return
			}
			in.Description, in.DescriptionSet = desc, true
		case "unit":
			unit, err := decodeOptionalPatchString(val)
			if err != nil {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "unit must be a string or null")
				return
			}
			in.Unit, in.UnitSet = unit, true
		case "dg_id":
			s, err := decodeOptionalPatchString(val)
			if err != nil {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "dg_id must be a valid UUID or null")
				return
			}
			if s != nil {
				gid, err := uuid.Parse(*s)
				if err != nil {
					httputil.WriteError(w, http.StatusUnprocessableEntity, "dg_id must be a valid UUID or null")
					return
				}
				in.DatasetGroupID = &gid
			}
			in.DatasetGroupIDSet = true
		case "start_date":
			date, err := decodeOptionalPatchDate(val)
			if err != nil {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "start_date must be YYYY-MM-DD or null")
				return
			}
			in.StartDate, in.StartDateSet = date, true
		case "end_date":
			date, err := decodeOptionalPatchDate(val)
			if err != nil {
				httputil.WriteError(w, http.StatusUnprocessableEntity, "end_date must be YYYY-MM-DD or null")
				return
			}
			in.EndDate, in.EndDateSet = date, true
		}
	}

	dataset, err := h.svc.Patch(r.Context(), id, in)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, dataset)
}

// optionalPtr normalizes an optional request field: a nil or blank value
// yields nil, so an omitted or empty field clears a nullable column.
func optionalPtr(v *string) *string {
	if v == nil {
		return nil
	}
	return normalizeOptional(*v)
}

// normalizeOptional trims s and returns nil when blank, so an empty string
// clears a nullable column rather than being stored verbatim.
func normalizeOptional(s string) *string {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	return &s
}

// decodeOptionalPatchString unmarshals a PATCH field that may be a JSON string
// or null, returning nil (clear the column) for null or a blank string.
func decodeOptionalPatchString(raw json.RawMessage) (*string, error) {
	if string(raw) == "null" {
		return nil, nil
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, err
	}
	return normalizeOptional(s), nil
}

// decodeOptionalPatchDate unmarshals a PATCH date field that may be a
// YYYY-MM-DD JSON string or null, returning nil (clear the column) for null or
// a blank string.
func decodeOptionalPatchDate(raw json.RawMessage) (*string, error) {
	v, err := decodeOptionalPatchString(raw)
	if err != nil || v == nil {
		return nil, err
	}
	if _, err := time.Parse("2006-01-02", *v); err != nil {
		return nil, err
	}
	return v, nil
}

// geojson returns the dataset's pre-assembled GeoJSON FeatureCollection (RFC
// 7946), generated and stored at processing time. A vector_with_kato dataset's
// collection joins the districts table on KATO code (one feature per KATO with
// the observation values mapped onto its district polygon); a plain vector
// dataset's collection wraps its own geometry as a single feature.
func (h *DatasetHandler) geojson(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	fc, err := h.svc.GeoJSON(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/geo+json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(fc)
}

// cog streams the raster dataset's Cloud-Optimized GeoTIFF. It responds 422 for
// a non-raster dataset and 409 when the raster's COG has not been produced yet.
func (h *DatasetHandler) cog(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	dataset, obj, err := h.svc.COG(r.Context(), id)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrNotRaster):
			httputil.WriteError(w, http.StatusUnprocessableEntity, "dataset is not a raster")
		case errors.Is(err, service.ErrCOGNotReady):
			httputil.WriteError(w, http.StatusConflict, "cog is not ready")
		default:
			respondDomainError(w, err)
		}
		return
	}
	defer obj.Close()

	w.Header().Set("Content-Type", "image/tiff")
	w.Header().Set("Content-Disposition", `inline; filename="`+cogFilename(dataset.Filename)+`"`)
	w.WriteHeader(http.StatusOK)
	if _, err := io.Copy(w, obj); err != nil {
		// Headers are already sent; nothing useful to return to the client.
		return
	}
}

// cogFilename derives a .cog.tif download name from the source filename.
func cogFilename(filename string) string {
	if i := strings.LastIndex(filename, "."); i > 0 {
		filename = filename[:i]
	}
	return filename + ".cog.tif"
}

func (h *DatasetHandler) download(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	dataset, obj, err := h.svc.Download(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	defer obj.Close()

	w.Header().Set("Content-Type", dataset.ContentType)
	w.Header().Set("Content-Disposition", `attachment; filename="`+dataset.Filename+`"`)
	if dataset.SizeBytes > 0 {
		w.Header().Set("Content-Length", strconv.FormatInt(dataset.SizeBytes, 10))
	}
	if _, err := io.Copy(w, obj); err != nil {
		// Headers are already sent; nothing useful to return to the client.
		return
	}
}

// uploadSLD stores (or replaces) a dataset's SLD (Styled Layer Descriptor)
// style file. Multipart upload, mirroring POST /datasets. Applies to every
// file_type.
func (h *DatasetHandler) uploadSLD(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
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
		contentType = "application/xml"
	}

	dataset, err := h.svc.UploadSLD(r.Context(), id, service.UploadSLDInput{
		Filename:    header.Filename,
		ContentType: contentType,
		Size:        header.Size,
		Reader:      file,
	})
	if err != nil {
		respondDomainError(w, err)
		return
	}
	httputil.WriteJSON(w, http.StatusOK, dataset)
}

// downloadSLD streams a dataset's stored SLD style file. Returns 404 if none
// has been uploaded yet.
func (h *DatasetHandler) downloadSLD(w http.ResponseWriter, r *http.Request) {
	id, ok := parseUUIDParam(w, r, "id")
	if !ok {
		return
	}
	dataset, obj, err := h.svc.DownloadSLD(r.Context(), id)
	if err != nil {
		respondDomainError(w, err)
		return
	}
	defer obj.Close()

	contentType := "application/xml"
	if dataset.SldContentType != nil && *dataset.SldContentType != "" {
		contentType = *dataset.SldContentType
	}
	filename := "style.sld"
	if dataset.SldFilename != nil && *dataset.SldFilename != "" {
		filename = *dataset.SldFilename
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Disposition", `attachment; filename="`+filename+`"`)
	if _, err := io.Copy(w, obj); err != nil {
		// Headers are already sent; nothing useful to return to the client.
		return
	}
}

func (h *DatasetHandler) delete(w http.ResponseWriter, r *http.Request) {
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
