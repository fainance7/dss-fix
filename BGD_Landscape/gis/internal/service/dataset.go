package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// maxParseBytes caps how much of a file is read into memory for parsing.
const maxParseBytes = 256 << 20 // 256 MiB

var (
	// ErrNotRaster is returned when a raster-only operation is requested for a
	// non-raster dataset.
	ErrNotRaster = errors.New("dataset is not a raster")
	// ErrCOGNotReady is returned when a raster's Cloud-Optimized GeoTIFF has not
	// been produced yet.
	ErrCOGNotReady = errors.New("cog is not ready")
	// ErrSLDNotFound is returned when a dataset has no SLD style file uploaded.
	ErrSLDNotFound = fmt.Errorf("%w: dataset has no sld", domain.ErrNotFound)
)

// DatasetRepository is the persistence behaviour DatasetService needs.
type DatasetRepository interface {
	Create(ctx context.Context, d domain.Dataset) (domain.Dataset, error)
	GetByID(ctx context.Context, id uuid.UUID) (domain.Dataset, error)
	UpdateFields(ctx context.Context, id uuid.UUID, f domain.DatasetFields) (domain.Dataset, error)
	LatestInGroup(ctx context.Context, groupID uuid.UUID, status *string, fileType *domain.FileType) (domain.Dataset, error)
	ListSummaries(ctx context.Context, filter domain.DatasetFilter, limit, offset int) ([]domain.DatasetSummary, error)
	Count(ctx context.Context, filter domain.DatasetFilter) (int, error)
	Delete(ctx context.Context, id uuid.UUID) error
	MarkParsed(ctx context.Context, id uuid.UUID, cols []domain.AttributeColumn) error
	MarkParseFailed(ctx context.Context, id uuid.UUID, reason string) error
	MarkReady(ctx context.Context, id uuid.UUID, geometry []byte) error
	MarkConverted(ctx context.Context, id uuid.UUID, cogKey string, footprint []byte) error
	SetProperties(ctx context.Context, id uuid.UUID, properties, geometry []byte) error
	SetGeoJSON(ctx context.Context, id uuid.UUID, geojson []byte) error
	ClearGeometryAndGeoJSON(ctx context.Context, id uuid.UUID) error
	SaveMapping(ctx context.Context, id uuid.UUID, katoColumn string, years []domain.YearColumn) (domain.Dataset, error)
	SetSLD(ctx context.Context, id uuid.UUID, key, filename, contentType string) (domain.Dataset, error)
	ReplaceObservations(ctx context.Context, datasetID uuid.UUID, obs []domain.Observation) error
	ListObservations(ctx context.Context, datasetID uuid.UUID, katoCode *string, limit, offset int) ([]domain.Observation, error)
	CountObservations(ctx context.Context, datasetID uuid.UUID, katoCode *string) (int, error)
	ListAllObservations(ctx context.Context, datasetID uuid.UUID) ([]domain.Observation, error)
	DistrictGeometriesByKato(ctx context.Context, katos []string) (map[string]domain.District, error)
}

// Pagination defaults for dataset listings.
const (
	DefaultPageSize = 20
	MaxPageSize     = 100
)

// DatasetPage is a page of dataset summaries with pagination metadata.
type DatasetPage struct {
	Items    []domain.DatasetSummary
	Page     int
	PageSize int
	Total    int
}

// ObjectStore is the object-storage behaviour DatasetService needs.
type ObjectStore interface {
	Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error
	Get(ctx context.Context, key string) (io.ReadCloser, error)
	Remove(ctx context.Context, key string) error
}

// categoryReader lets the dataset service verify a category exists before upload
// and resolve a category code to its id for list filtering.
type categoryReader interface {
	GetByID(ctx context.Context, id uuid.UUID) (domain.Category, error)
	GetByCode(ctx context.Context, code string) (domain.Category, error)
}

// groupReader lets the dataset service verify a dataset group exists before a
// dataset is filed under it, and resolve its shared SLD as a fallback.
type groupReader interface {
	GetByID(ctx context.Context, id uuid.UUID) (domain.DatasetGroup, error)
}

// JobEnqueuer schedules asynchronous dataset jobs.
type JobEnqueuer interface {
	EnqueueParse(ctx context.Context, datasetID uuid.UUID) error
	EnqueueProperties(ctx context.Context, datasetID uuid.UUID) error
	EnqueueExtract(ctx context.Context, datasetID uuid.UUID) error
	EnqueueConvert(ctx context.Context, datasetID uuid.UUID) error
}

// ColumnParser detects attribute columns from a file's raw bytes.
type ColumnParser func(filename string, data []byte) ([]domain.AttributeColumn, error)

// RowParser reads every attribute row from a file's raw bytes as name->value maps.
type RowParser func(filename string, data []byte) ([]map[string]string, error)

// RasterConverter converts a raster file to a Cloud-Optimized GeoTIFF, reads its
// footprint, and reads a vector file's per-feature geometry. It operates on
// local file paths.
type RasterConverter interface {
	ToCOG(ctx context.Context, srcPath, dstPath string) error
	Footprint(ctx context.Context, srcPath string) ([]byte, error)
	// VectorFeatures returns each feature's geometry as GeoJSON in EPSG:4326, in
	// file order (nil if the file has no features). A feature with no geometry
	// yields a nil entry at its index, keeping the result aligned by position
	// with attribute rows read from the same file via RowParser.
	VectorFeatures(ctx context.Context, srcPath string) ([]json.RawMessage, error)
}

// UploadInput carries everything needed to store a new dataset.
type UploadInput struct {
	CategoryID  uuid.UUID
	Name        string
	Description *string
	Unit        *string
	Meta        json.RawMessage
	Automated   bool
	Filename    string
	FileType    domain.FileType
	ContentType string
	Size        int64
	Reader      io.Reader
	// DatasetGroupID optionally files the dataset under a dataset group.
	DatasetGroupID *uuid.UUID
	// StartDate / EndDate is the period the dataset describes (YYYY-MM-DD).
	StartDate *string
	EndDate   *string
}

// DatasetService implements dataset business rules and object storage handling.
type DatasetService struct {
	repo         DatasetRepository
	store        ObjectStore
	categories   categoryReader
	groups       groupReader
	jobs         JobEnqueuer
	parseColumns ColumnParser
	parseRows    RowParser
	converter    RasterConverter
}

// NewDatasetService wires the dataset repository, object store, category reader
// (for parent validation), the dataset-group reader (for group validation and
// the SLD fallback), the job enqueuer, the column/row parsers, and the raster
// converter.
func NewDatasetService(
	repo DatasetRepository,
	store ObjectStore,
	categories categoryReader,
	groups groupReader,
	jobs JobEnqueuer,
	parseColumns ColumnParser,
	parseRows RowParser,
	converter RasterConverter,
) *DatasetService {
	return &DatasetService{
		repo:         repo,
		store:        store,
		categories:   categories,
		groups:       groups,
		jobs:         jobs,
		parseColumns: parseColumns,
		parseRows:    parseRows,
		converter:    converter,
	}
}

// Upload validates input, stores the object, and persists the dataset. If the
// database write fails after upload, the stored object is removed.
func (s *DatasetService) Upload(ctx context.Context, in UploadInput) (domain.Dataset, error) {
	if !in.FileType.Valid() {
		return domain.Dataset{}, fmt.Errorf("%w: unknown file_type %q", domain.ErrValidation, in.FileType)
	}

	ext := strings.ToLower(filepath.Ext(in.Filename))
	if !domain.ExtensionAllowedFor(in.FileType, ext) {
		return domain.Dataset{}, fmt.Errorf("%w: extension %q is not allowed for file_type %q (allowed: %s)",
			domain.ErrValidation, ext, in.FileType, strings.Join(domain.AllowedExtensions(in.FileType), ", "))
	}

	// Sniff the file's leading bytes to reject mislabeled uploads up front, then
	// reconstruct the full stream for storage.
	head := make([]byte, 512)
	n, err := io.ReadFull(in.Reader, head)
	if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
		return domain.Dataset{}, fmt.Errorf("read upload: %w", err)
	}
	head = head[:n]
	if err := domain.ValidateFileContent(ext, head); err != nil {
		return domain.Dataset{}, fmt.Errorf("%w: %s", domain.ErrValidation, err)
	}
	content := io.MultiReader(bytes.NewReader(head), in.Reader)

	if err := s.ensureCategoryExists(ctx, in.CategoryID); err != nil {
		return domain.Dataset{}, err
	}
	if err := s.ensureGroupExists(ctx, in.DatasetGroupID); err != nil {
		return domain.Dataset{}, err
	}
	if err := domain.ValidateDatePeriod(in.StartDate, in.EndDate); err != nil {
		return domain.Dataset{}, err
	}

	name := in.Name
	if name == "" {
		name = in.Filename
	}

	// Every uploaded file is processed asynchronously: vector_with_kato is parsed
	// for column selection; plain vector has its attribute table extracted into
	// properties; raster is converted to a COG.
	status := domain.DatasetStatusProcessing
	if in.FileType == domain.FileTypeVectorWithKato {
		status = domain.DatasetStatusParsing
	}

	objectName, err := safeObjectName(in.Filename)
	if err != nil {
		return domain.Dataset{}, err
	}
	storageKey := fmt.Sprintf("%s/%s", uuid.New().String(), objectName)
	if err := s.store.Put(ctx, storageKey, content, in.Size, in.ContentType); err != nil {
		return domain.Dataset{}, err
	}

	dataset, err := s.repo.Create(ctx, domain.Dataset{
		CategoryID:     in.CategoryID,
		DatasetGroupID: in.DatasetGroupID,
		StartDate:      in.StartDate,
		EndDate:        in.EndDate,
		Name:           name,
		Description:    in.Description,
		Unit:           in.Unit,
		Meta:           in.Meta,
		Automated:      in.Automated,
		Status:         status,
		Filename:       in.Filename,
		StorageKey:     storageKey,
		FileType:       in.FileType,
		SizeBytes:      in.Size,
		ContentType:    in.ContentType,
	})
	if err != nil {
		// Compensate: the row was not written, so the object would be orphaned.
		_ = s.store.Remove(ctx, storageKey)
		return domain.Dataset{}, err
	}

	// Kick off the appropriate async job per file type. If enqueueing fails the
	// row exists, so record the failure rather than leaving it stuck.
	if err := s.enqueueProcessing(ctx, dataset); err != nil {
		_ = s.repo.MarkParseFailed(ctx, dataset.ID, "failed to enqueue processing: "+err.Error())
		return domain.Dataset{}, fmt.Errorf("enqueue processing: %w", err)
	}
	return dataset, nil
}

// UploadSLDInput carries an SLD (Styled Layer Descriptor) style file to attach
// to an existing dataset.
type UploadSLDInput struct {
	Filename    string
	ContentType string
	Size        int64
	Reader      io.Reader
}

// UploadSLD validates and stores an SLD style file for a dataset, replacing
// any previously stored one. Applies to every file_type.
func (s *DatasetService) UploadSLD(ctx context.Context, id uuid.UUID, in UploadSLDInput) (domain.Dataset, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, err
	}

	ext := strings.ToLower(filepath.Ext(in.Filename))
	if !domain.ExtensionAllowedForSLD(ext) {
		return domain.Dataset{}, fmt.Errorf("%w: sld file must have one of these extensions: %s",
			domain.ErrValidation, strings.Join(domain.AllowedSLDExtensions, ", "))
	}

	head := make([]byte, 512)
	n, err := io.ReadFull(in.Reader, head)
	if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
		return domain.Dataset{}, fmt.Errorf("read sld upload: %w", err)
	}
	head = head[:n]
	if err := domain.ValidateFileContent(ext, head); err != nil {
		return domain.Dataset{}, fmt.Errorf("%w: %s", domain.ErrValidation, err)
	}
	content := io.MultiReader(bytes.NewReader(head), in.Reader)

	key, err := deriveSLDKey(dataset.StorageKey, in.Filename)
	if err != nil {
		return domain.Dataset{}, err
	}
	if err := s.store.Put(ctx, key, content, in.Size, in.ContentType); err != nil {
		return domain.Dataset{}, err
	}

	updated, err := s.repo.SetSLD(ctx, id, key, in.Filename, in.ContentType)
	if err != nil {
		// Compensate: the row was not updated, so the object would be orphaned.
		_ = s.store.Remove(ctx, key)
		return domain.Dataset{}, err
	}

	if dataset.SldStorageKey != nil && *dataset.SldStorageKey != key {
		_ = s.store.Remove(ctx, *dataset.SldStorageKey) // best-effort: drop the replaced file
	}
	return updated, nil
}

// DownloadSLD returns the dataset metadata and a reader for its stored SLD
// style file. Fails with ErrSLDNotFound when none has been uploaded. The
// caller must close the reader.
func (s *DatasetService) DownloadSLD(ctx context.Context, id uuid.UUID) (domain.Dataset, io.ReadCloser, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, nil, err
	}
	if dataset.SldStorageKey == nil {
		// Fall back to the dataset group's shared style, if the group has one.
		if dataset.DatasetGroupID == nil {
			return domain.Dataset{}, nil, ErrSLDNotFound
		}
		group, err := s.groups.GetByID(ctx, *dataset.DatasetGroupID)
		if err != nil {
			if errors.Is(err, domain.ErrNotFound) {
				return domain.Dataset{}, nil, ErrSLDNotFound
			}
			return domain.Dataset{}, nil, err
		}
		if group.SldStorageKey == nil {
			return domain.Dataset{}, nil, ErrSLDNotFound
		}
		dataset.SldStorageKey = group.SldStorageKey
		dataset.SldFilename = group.SldFilename
		dataset.SldContentType = group.SldContentType
	}
	obj, err := s.store.Get(ctx, *dataset.SldStorageKey)
	if err != nil {
		return domain.Dataset{}, nil, err
	}
	return dataset, obj, nil
}

// CreateRasterInput describes a raster file already on local disk that should
// become a dataset (used by the Landscape Program to publish results).
type CreateRasterInput struct {
	CategoryID     uuid.UUID
	DatasetGroupID *uuid.UUID
	Name           string
	Filename       string // stored filename, e.g. "lp-z-integrated.tif"
	Path           string // local path to read from
	StartDate      *string
	EndDate        *string
	Meta           json.RawMessage
	Automated      bool
}

// CreateRasterFromFile stores a local GeoTIFF as a new raster dataset and
// enqueues the standard COG conversion, exactly like an uploaded raster.
func (s *DatasetService) CreateRasterFromFile(ctx context.Context, in CreateRasterInput) (domain.Dataset, error) {
	f, err := os.Open(in.Path)
	if err != nil {
		return domain.Dataset{}, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return domain.Dataset{}, err
	}
	return s.Upload(ctx, UploadInput{
		CategoryID:     in.CategoryID,
		DatasetGroupID: in.DatasetGroupID,
		Name:           in.Name,
		Meta:           in.Meta,
		Automated:      in.Automated,
		Filename:       in.Filename,
		FileType:       domain.FileTypeRaster,
		ContentType:    "image/tiff",
		Size:           info.Size(),
		Reader:         f,
		StartDate:      in.StartDate,
		EndDate:        in.EndDate,
	})
}

// safeObjectName reduces a user-supplied filename to a single path element so
// it can never escape the object-key prefix it is appended to. Rejects names
// that carry no usable base ("", ".", "..", "/").
func safeObjectName(name string) (string, error) {
	base := path.Base(filepath.ToSlash(strings.TrimSpace(name)))
	switch base {
	case "", ".", "..", "/":
		return "", fmt.Errorf("%w: invalid filename", domain.ErrValidation)
	}
	return base, nil
}

// deriveSLDKey places a dataset's SLD style file alongside the original under
// an sld/ prefix, named after the uploaded SLD file. The filename is
// sanitised, so the key always stays under the dataset's own prefix.
func deriveSLDKey(storageKey, filename string) (string, error) {
	name, err := safeObjectName(filename)
	if err != nil {
		return "", err
	}
	return path.Join(path.Dir(storageKey), "sld", name), nil
}

// enqueueProcessing schedules the appropriate async job for a dataset based on
// its file type: vector_with_kato is parsed for column selection, plain vector
// has its attribute table extracted into properties, and raster is converted to
// a COG.
func (s *DatasetService) enqueueProcessing(ctx context.Context, d domain.Dataset) error {
	switch d.FileType {
	case domain.FileTypeVectorWithKato:
		return s.jobs.EnqueueParse(ctx, d.ID)
	case domain.FileTypeVector:
		return s.jobs.EnqueueProperties(ctx, d.ID)
	case domain.FileTypeRaster:
		return s.jobs.EnqueueConvert(ctx, d.ID)
	default:
		return fmt.Errorf("%w: unknown file_type %q", domain.ErrValidation, d.FileType)
	}
}

// Reprocess re-enqueues the processing job for an existing dataset, restarting
// its asynchronous pipeline from the step appropriate to its file type. Useful
// for retrying after a transient failure or a worker that was behind the
// schema. Its stored geometry and GeoJSON are cleared first so a stale (e.g.
// previously miscomputed) value is never served while the job is pending; the
// worker repopulates both once the job completes.
func (s *DatasetService) Reprocess(ctx context.Context, id uuid.UUID) (domain.Dataset, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, err
	}
	if err := s.repo.ClearGeometryAndGeoJSON(ctx, id); err != nil {
		return domain.Dataset{}, fmt.Errorf("clear stale geometry/geojson: %w", err)
	}
	if err := s.enqueueProcessing(ctx, dataset); err != nil {
		return domain.Dataset{}, fmt.Errorf("enqueue processing: %w", err)
	}
	return dataset, nil
}

// ReprocessAll re-enqueues the processing job for every dataset matching
// fileType (nil matches every file type), restarting each one's asynchronous
// pipeline from the step appropriate to its file type. Each dataset's stored
// geometry and GeoJSON are cleared first (see Reprocess). It paginates through
// all matching datasets and continues past individual failures, returning how
// many jobs were enqueued and a per-dataset map of any failures.
func (s *DatasetService) ReprocessAll(ctx context.Context, fileType *domain.FileType) (enqueued int, failures map[uuid.UUID]error, err error) {
	failures = make(map[uuid.UUID]error)
	filter := domain.DatasetFilter{FileType: fileType}
	for offset := 0; ; {
		summaries, err := s.repo.ListSummaries(ctx, filter, MaxPageSize, offset)
		if err != nil {
			return enqueued, failures, err
		}
		if len(summaries) == 0 {
			break
		}
		for _, sum := range summaries {
			if e := s.repo.ClearGeometryAndGeoJSON(ctx, sum.ID); e != nil {
				failures[sum.ID] = e
				continue
			}
			if e := s.enqueueProcessing(ctx, domain.Dataset{ID: sum.ID, FileType: sum.FileType}); e != nil {
				failures[sum.ID] = e
				continue
			}
			enqueued++
		}
		if len(summaries) < MaxPageSize {
			break
		}
		offset += len(summaries)
	}
	return enqueued, failures, nil
}

// ExtractProperties reads a plain vector dataset's attribute table and
// per-feature geometry and stores them: the dissolved (unioned) geometry in
// the geometry column, and a GeoJSON FeatureCollection with one Feature per
// original polygon (its own geometry paired with its own attribute row, by
// position - the same pairing a shapefile's .shp/.dbf records have) as the
// persisted GeoJSON. Then marks the dataset ready. Invoked by the worker.
// Parse failures are recorded; storage failures are returned for retry.
// Geometry extraction is best-effort: a failure leaves geometry unset rather
// than failing the job.
func (s *DatasetService) ExtractProperties(ctx context.Context, id uuid.UUID) error {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return err
	}
	if dataset.FileType != domain.FileTypeVector {
		return nil // only plain vector populates properties
	}

	data, err := s.fetchObject(ctx, dataset.StorageKey)
	if err != nil {
		return fmt.Errorf("read dataset %s: %w", id, err) // transient
	}

	rows, err := s.parseRows(dataset.Filename, data)
	if err != nil {
		return s.repo.MarkParseFailed(ctx, id, err.Error()) // permanent
	}

	var properties []byte
	if hasAttributeData(rows) {
		if properties, err = json.Marshal(rows); err != nil {
			return err
		}
	}

	geoms := s.vectorFeatureGeometries(ctx, dataset.Filename, data)
	if err := s.repo.SetProperties(ctx, id, properties, dissolvedGeometryJSON(geoms)); err != nil {
		return err
	}

	fc := domain.FeatureCollection{
		Type:     domain.GeoJSONFeatureCollection,
		Features: buildVectorFeatures(rows, geoms),
	}
	raw, err := json.Marshal(fc)
	if err != nil {
		return err
	}
	return s.repo.SetGeoJSON(ctx, id, raw)
}

// vectorFeatureGeometries reads a vector file's per-feature geometries via the
// converter. It writes the in-memory bytes to a temp file (preserving the
// extension so the converter detects the format) because the converter
// operates on file paths. Best-effort: any failure yields nil.
func (s *DatasetService) vectorFeatureGeometries(ctx context.Context, filename string, data []byte) []json.RawMessage {
	ext := strings.ToLower(filepath.Ext(filename))
	f, err := os.CreateTemp("", "gis-vec-*"+ext)
	if err != nil {
		return nil
	}
	defer os.Remove(f.Name())
	if _, err := f.Write(data); err != nil {
		f.Close()
		return nil
	}
	if err := f.Close(); err != nil {
		return nil
	}
	geoms, _ := s.converter.VectorFeatures(ctx, f.Name())
	return geoms
}

// dissolvedGeometryJSON wraps a vector file's per-feature geometries into a
// single GeoJSON GeometryCollection for storage in the geometry column, where
// PostGIS dissolves it into the union of all features (nil entries and an
// empty/nil input are dropped, yielding nil when no geometry survives).
func dissolvedGeometryJSON(geoms []json.RawMessage) []byte {
	present := make([]json.RawMessage, 0, len(geoms))
	for _, g := range geoms {
		if len(g) == 0 || string(g) == "null" {
			continue
		}
		present = append(present, g)
	}
	if len(present) == 0 {
		return nil
	}
	b, err := json.Marshal(struct {
		Type       string            `json:"type"`
		Geometries []json.RawMessage `json:"geometries"`
	}{Type: "GeometryCollection", Geometries: present})
	if err != nil {
		return nil
	}
	return b
}

// buildVectorFeatures pairs each row with the geometry at the same position
// (rows and geoms both come from reading the same file in file order, which
// is how a shapefile's .dbf attribute records correspond to its .shp geometry
// records) into one Feature per polygon. A row or geometry beyond the other
// slice's length, or a feature with no geometry, is skipped.
func buildVectorFeatures(rows []map[string]string, geoms []json.RawMessage) []domain.Feature {
	n := len(geoms)
	if len(rows) < n {
		n = len(rows)
	}
	features := make([]domain.Feature, 0, n)
	for i := 0; i < n; i++ {
		if len(geoms[i]) == 0 || string(geoms[i]) == "null" {
			continue
		}
		props := make(map[string]any, len(rows[i]))
		for k, v := range rows[i] {
			props[k] = v
		}
		features = append(features, domain.Feature{
			Type:       domain.GeoJSONFeature,
			Geometry:   geoms[i],
			Properties: props,
		})
	}
	return features
}

// hasAttributeData reports whether any row carries at least one attribute.
func hasAttributeData(rows []map[string]string) bool {
	for _, row := range rows {
		if len(row) > 0 {
			return true
		}
	}
	return false
}

// ConvertToCOG converts a raster dataset to a Cloud-Optimized GeoTIFF, stores it
// under a new key, records the footprint geometry, and marks the dataset ready.
// Invoked by the worker. Conversion failures are recorded; storage failures are
// returned for retry.
func (s *DatasetService) ConvertToCOG(ctx context.Context, id uuid.UUID) error {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return err
	}
	if dataset.FileType != domain.FileTypeRaster {
		return nil // nothing to convert
	}

	srcPath, cleanupSrc, err := s.downloadToTemp(ctx, dataset.StorageKey, "gis-src-*.tif")
	if err != nil {
		return fmt.Errorf("download raster %s: %w", id, err) // transient
	}
	defer cleanupSrc()

	dstPath := srcPath + ".cog.tif"
	defer os.Remove(dstPath)

	footprint, _ := s.converter.Footprint(ctx, srcPath) // best-effort

	if err := s.converter.ToCOG(ctx, srcPath, dstPath); err != nil {
		return s.repo.MarkParseFailed(ctx, id, err.Error()) // permanent
	}

	cogKey := deriveCOGKey(dataset.StorageKey)
	if err := s.uploadFile(ctx, cogKey, dstPath, "image/tiff"); err != nil {
		return fmt.Errorf("upload cog %s: %w", id, err) // transient
	}
	return s.repo.MarkConverted(ctx, id, cogKey, footprint)
}

// downloadToTemp streams an object to a temp file and returns its path and a
// cleanup func.
func (s *DatasetService) downloadToTemp(ctx context.Context, key, pattern string) (string, func(), error) {
	obj, err := s.store.Get(ctx, key)
	if err != nil {
		return "", nil, err
	}
	defer obj.Close()

	f, err := os.CreateTemp("", pattern)
	if err != nil {
		return "", nil, err
	}
	if _, err := io.Copy(f, obj); err != nil {
		f.Close()
		os.Remove(f.Name())
		return "", nil, err
	}
	if err := f.Close(); err != nil {
		os.Remove(f.Name())
		return "", nil, err
	}
	return f.Name(), func() { os.Remove(f.Name()) }, nil
}

// uploadFile streams a local file to the object store.
func (s *DatasetService) uploadFile(ctx context.Context, key, filePath, contentType string) error {
	f, err := os.Open(filePath)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	return s.store.Put(ctx, key, f, info.Size(), contentType)
}

// deriveCOGKey places the COG alongside the original under a cog/ prefix.
func deriveCOGKey(storageKey string) string {
	return path.Join(path.Dir(storageKey), "cog", path.Base(storageKey))
}

// Parse reads a vector_with_kato dataset's file, detects its attribute columns,
// and moves it to awaiting_mapping. It is invoked by the worker. Permanent
// parse failures are recorded on the dataset (and not retried); transient
// failures are returned to the caller.
func (s *DatasetService) Parse(ctx context.Context, id uuid.UUID) error {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return err
	}
	if dataset.FileType != domain.FileTypeVectorWithKato {
		return nil // nothing to parse
	}

	data, err := s.fetchObject(ctx, dataset.StorageKey)
	if err != nil {
		return fmt.Errorf("read dataset %s: %w", id, err) // transient; allow retry
	}

	cols, err := s.parseColumns(dataset.Filename, data)
	if err != nil {
		// Permanent: the file could not be parsed. Record and stop.
		return s.repo.MarkParseFailed(ctx, id, err.Error())
	}
	return s.repo.MarkParsed(ctx, id, cols)
}

// MarkFailed records a terminal processing failure on a dataset, moving it out
// of any in-progress status. The worker calls this when a job returns an error
// (e.g. a PostGIS insert failure) so the dataset is not left stuck. Marking a
// dataset that no longer exists is not treated as an error.
func (s *DatasetService) MarkFailed(ctx context.Context, id uuid.UUID, reason string) error {
	if err := s.repo.MarkParseFailed(ctx, id, reason); err != nil && !errors.Is(err, domain.ErrNotFound) {
		return err
	}
	return nil
}

func (s *DatasetService) fetchObject(ctx context.Context, key string) ([]byte, error) {
	obj, err := s.store.Get(ctx, key)
	if err != nil {
		return nil, err
	}
	defer obj.Close()
	return io.ReadAll(io.LimitReader(obj, maxParseBytes))
}

// MappingInput carries the user's KATO column choice and year-column mapping.
type MappingInput struct {
	KatoColumn  string
	YearColumns []domain.YearColumn
}

// SaveMapping validates the KATO column and year mapping against the dataset's
// detected columns and marks the dataset ready.
func (s *DatasetService) SaveMapping(ctx context.Context, id uuid.UUID, in MappingInput) (domain.Dataset, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, err
	}
	if dataset.FileType != domain.FileTypeVectorWithKato {
		return domain.Dataset{}, fmt.Errorf("%w: mapping only applies to vector_with_kato datasets", domain.ErrValidation)
	}
	if dataset.Status != domain.DatasetStatusAwaitingMapping && dataset.Status != domain.DatasetStatusReady {
		return domain.Dataset{}, fmt.Errorf("%w: dataset is not ready for mapping (status %q)", domain.ErrConflict, dataset.Status)
	}

	known := make(map[string]struct{}, len(dataset.AttributeColumns))
	for _, c := range dataset.AttributeColumns {
		known[c.Name] = struct{}{}
	}
	if _, ok := known[in.KatoColumn]; !ok {
		return domain.Dataset{}, fmt.Errorf("%w: kato_column %q is not among the detected columns", domain.ErrValidation, in.KatoColumn)
	}
	if len(in.YearColumns) == 0 {
		return domain.Dataset{}, fmt.Errorf("%w: at least one year column mapping is required", domain.ErrValidation)
	}
	for _, yc := range in.YearColumns {
		if _, ok := known[yc.Column]; !ok {
			return domain.Dataset{}, fmt.Errorf("%w: year column %q is not among the detected columns", domain.ErrValidation, yc.Column)
		}
		from, err := time.Parse("2006-01-02", yc.StartDate)
		if err != nil {
			return domain.Dataset{}, fmt.Errorf("%w: invalid start_date %q for column %q (want YYYY-MM-DD)", domain.ErrValidation, yc.StartDate, yc.Column)
		}
		if yc.EndDate != nil {
			to, err := time.Parse("2006-01-02", *yc.EndDate)
			if err != nil {
				return domain.Dataset{}, fmt.Errorf("%w: invalid end_date %q for column %q (want YYYY-MM-DD)", domain.ErrValidation, *yc.EndDate, yc.Column)
			}
			if to.Before(from) {
				return domain.Dataset{}, fmt.Errorf("%w: end_date %q is before start_date %q for column %q", domain.ErrValidation, *yc.EndDate, yc.StartDate, yc.Column)
			}
		}
	}

	dataset, err = s.repo.SaveMapping(ctx, id, in.KatoColumn, in.YearColumns)
	if err != nil {
		return domain.Dataset{}, err
	}
	if err := s.jobs.EnqueueExtract(ctx, id); err != nil {
		_ = s.repo.MarkParseFailed(ctx, id, "failed to enqueue extraction: "+err.Error())
		return domain.Dataset{}, fmt.Errorf("enqueue extract: %w", err)
	}
	return dataset, nil
}

// Extract reads a mapped dataset's file, unpivots its attribute table into
// observations keyed by KATO code and date, records the dissolved feature
// geometry, and marks the dataset ready. It is invoked by the worker. Permanent
// failures (unparsable file) are recorded; transient failures (storage/DB) are
// returned for retry. Geometry extraction is best-effort: a failure leaves
// geometry unset rather than failing the job.
func (s *DatasetService) Extract(ctx context.Context, id uuid.UUID) error {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return err
	}
	if dataset.KatoColumn == nil || len(dataset.YearColumns) == 0 {
		return fmt.Errorf("dataset %s has no mapping to extract", id)
	}

	data, err := s.fetchObject(ctx, dataset.StorageKey)
	if err != nil {
		return fmt.Errorf("read dataset %s: %w", id, err) // transient
	}

	rows, err := s.parseRows(dataset.Filename, data)
	if err != nil {
		return s.repo.MarkParseFailed(ctx, id, err.Error()) // permanent
	}

	obs := buildObservations(id, *dataset.KatoColumn, dataset.YearColumns, rows)
	if err := s.repo.ReplaceObservations(ctx, id, obs); err != nil {
		return err // transient
	}

	geoms := s.vectorFeatureGeometries(ctx, dataset.Filename, data)
	if err := s.repo.MarkReady(ctx, id, dissolvedGeometryJSON(geoms)); err != nil {
		return err
	}
	// Assemble and persist the GeoJSON served by the .geojson endpoint by joining
	// the districts table on the freshly extracted observations.
	return s.generateGeoJSON(ctx, id)
}

// buildObservations unpivots rows into observations. Rows without a KATO code
// are skipped; duplicate (kato, start_date, end_date) tuples keep the last
// value. Numeric cells populate Value, others ValueText. A year column's
// EndDate (nil for a point-in-time mapping) carries through to the observation
// unchanged.
func buildObservations(datasetID uuid.UUID, katoColumn string, years []domain.YearColumn, rows []map[string]string) []domain.Observation {
	obs := make([]domain.Observation, 0, len(rows)*len(years))
	index := make(map[string]int)

	for _, row := range rows {
		kato := strings.TrimSpace(row[katoColumn])
		if kato == "" {
			continue
		}
		for _, yc := range years {
			o := domain.Observation{DatasetID: datasetID, KatoCode: kato, StartDate: yc.StartDate, EndDate: yc.EndDate}
			if raw := strings.TrimSpace(row[yc.Column]); raw != "" {
				if f, err := strconv.ParseFloat(raw, 64); err == nil {
					o.Value = &f
				} else {
					o.ValueText = &raw
				}
			}
			key := kato + "\x00" + yc.StartDate + "\x00" + endDateKey(yc.EndDate)
			if i, ok := index[key]; ok {
				obs[i] = o
			} else {
				index[key] = len(obs)
				obs = append(obs, o)
			}
		}
	}
	return obs
}

// endDateKey normalizes an optional period end date for use in a dedup key.
func endDateKey(endDate *string) string {
	if endDate == nil {
		return ""
	}
	return *endDate
}

// ObservationPage is a page of observations with pagination metadata.
type ObservationPage struct {
	Items    []domain.Observation
	Page     int
	PageSize int
	Total    int
}

// ListObservations returns a page of a dataset's observations, optionally
// filtered by KATO code.
func (s *DatasetService) ListObservations(ctx context.Context, id uuid.UUID, katoCode *string, page, pageSize int) (ObservationPage, error) {
	if _, err := s.repo.GetByID(ctx, id); err != nil {
		return ObservationPage{}, err
	}
	if page < 1 {
		page = 1
	}
	if pageSize < 1 {
		pageSize = DefaultPageSize
	}
	if pageSize > MaxPageSize {
		pageSize = MaxPageSize
	}

	items, err := s.repo.ListObservations(ctx, id, katoCode, pageSize, (page-1)*pageSize)
	if err != nil {
		return ObservationPage{}, err
	}
	total, err := s.repo.CountObservations(ctx, id, katoCode)
	if err != nil {
		return ObservationPage{}, err
	}
	return ObservationPage{Items: items, Page: page, PageSize: pageSize, Total: total}, nil
}

// emptyFeatureCollectionJSON is served when a dataset has no persisted GeoJSON
// (e.g. it carried no geometry), so the endpoint always returns a valid RFC 7946
// FeatureCollection.
var emptyFeatureCollectionJSON = json.RawMessage(`{"type":"FeatureCollection","features":[]}`)

// GeoJSON returns the pre-assembled GeoJSON FeatureCollection (RFC 7946) stored
// for a vector or vector_with_kato dataset. The collection is normally generated
// and persisted at processing time (see generateGeoJSON), and this method serves
// the stored column verbatim.
//
// For backward compatibility with datasets that became ready before the column
// existed, an empty column is assembled on demand, persisted (so subsequent
// requests are served from the cached column), and returned.
//
// Only ready datasets are served; a dataset still being processed yields a
// conflict, and unsupported file types (e.g. raster) yield a validation error.
func (s *DatasetService) GeoJSON(ctx context.Context, id uuid.UUID) (json.RawMessage, error) {
	dataset, err := s.loadGeoJSONDataset(ctx, id)
	if err != nil {
		return nil, err
	}
	if len(dataset.GeoJSON) > 0 {
		return dataset.GeoJSON, nil
	}

	// Empty column (ready dataset processed before GeoJSON was persisted at
	// processing time): assemble it now, cache it, and return it.
	raw, err := s.buildGeoJSON(ctx, id)
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 {
		raw = emptyFeatureCollectionJSON
	}
	if err := s.repo.SetGeoJSON(ctx, id, raw); err != nil {
		return nil, err
	}
	return raw, nil
}

// generateGeoJSON assembles the dataset's GeoJSON FeatureCollection and persists
// it. It is invoked at processing time for vector and vector_with_kato datasets;
// rasters (and any other type) store nothing.
func (s *DatasetService) generateGeoJSON(ctx context.Context, id uuid.UUID) error {
	raw, err := s.buildGeoJSON(ctx, id)
	if err != nil {
		return err
	}
	return s.repo.SetGeoJSON(ctx, id, raw)
}

// buildGeoJSON assembles a GeoJSON FeatureCollection (RFC 7946) for a dataset
// from its persisted state, returning the marshaled bytes (nil for unsupported
// file types).
//
// A plain vector dataset yields a single geometry-only Feature wrapping the
// dataset's own (dissolved) geometry, exposing its extracted attribute table as
// the Feature's properties (or an empty collection when it has no geometry).
//
// A vector_with_kato dataset always ignores any geometry it carries and instead
// joins the districts table on KATO code: one Feature is emitted per KATO, its
// boundary taken from the districts table and its observation values nested
// under a `data` object (keyed by date) alongside `kato` and `name`. KATO codes
// with no matching district are skipped. When any observation carries a period
// (EndDate set), its end date is additionally exposed under a `periods` object
// (keyed by the same start date used in `data`); `periods` is omitted entirely
// when every observation is point-in-time, so a dataset mapped without periods
// produces byte-identical output to before periods existed.
func (s *DatasetService) buildGeoJSON(ctx context.Context, id uuid.UUID) (json.RawMessage, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return nil, err
	}

	var fc domain.FeatureCollection
	switch dataset.FileType {
	case domain.FileTypeVector:
		// Plain vector: the dataset's own geometry as a single Feature, exposing the
		// extracted attribute table (e.g. a GeoPackage's table data) as properties.
		fc = domain.FeatureCollection{Type: domain.GeoJSONFeatureCollection, Features: []domain.Feature{}}
		if hasGeometry(dataset.Geometry) {
			fc.Features = append(fc.Features, domain.Feature{
				Type:       domain.GeoJSONFeature,
				Geometry:   dataset.Geometry,
				Properties: vectorFeatureProperties(dataset.Properties),
			})
		}
	case domain.FileTypeVectorWithKato:
		// Always ignore the dataset's own geometry; build one Feature per KATO from
		// the districts table.
		obs, err := s.repo.ListAllObservations(ctx, id)
		if err != nil {
			return nil, err
		}
		grouped, periods, order := groupObservationsByKato(obs)
		features, err := s.districtFeatures(ctx, grouped, periods, order)
		if err != nil {
			return nil, err
		}
		fc = domain.FeatureCollection{Type: domain.GeoJSONFeatureCollection, Features: features}
	default:
		return nil, nil // rasters carry no GeoJSON
	}

	return json.Marshal(fc)
}

// loadGeoJSONDataset fetches a dataset for the GeoJSON endpoint and validates
// that it is ready and of a supported file type (vector or vector_with_kato).
func (s *DatasetService) loadGeoJSONDataset(ctx context.Context, id uuid.UUID) (domain.Dataset, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, err
	}

	supported := dataset.FileType == domain.FileTypeVectorWithKato ||
		dataset.FileType == domain.FileTypeVector
	if !supported {
		return domain.Dataset{}, fmt.Errorf("%w: geojson is only available for vector and vector_with_kato datasets", domain.ErrValidation)
	}
	if dataset.Status != domain.DatasetStatusReady {
		return domain.Dataset{}, fmt.Errorf("%w: dataset is not ready (status %q)", domain.ErrConflict, dataset.Status)
	}
	return dataset, nil
}

// districtFeatures builds one Feature per KATO from the districts table, with
// the grouped observation values (keyed by date) nested under a `data` object
// alongside `kato` and `name` in each Feature's properties. When periods holds
// an entry for the KATO (at least one observation had a period), its date->
// end_date map is additionally nested under a `periods` object; a KATO with no
// period observations gets no `periods` key at all. KATO codes with no
// matching district are skipped. order drives the deterministic feature order.
func (s *DatasetService) districtFeatures(ctx context.Context, grouped map[string]map[string]any, periods map[string]map[string]string, order []string) ([]domain.Feature, error) {
	districts, err := s.repo.DistrictGeometriesByKato(ctx, order)
	if err != nil {
		return nil, err
	}
	features := make([]domain.Feature, 0, len(order))
	for _, kato := range order {
		dist, ok := districts[kato]
		if !ok {
			continue // skip KATO codes with no district boundary
		}
		props := map[string]any{"kato": kato, "name": dist.Name, "data": grouped[kato]}
		if p, ok := periods[kato]; ok {
			props["periods"] = p
		}
		features = append(features, domain.Feature{
			Type:       domain.GeoJSONFeature,
			Geometry:   dist.Geometry,
			Properties: props,
		})
	}
	return features, nil
}

// hasGeometry reports whether a dataset's GeoJSON geometry (as produced by
// ST_AsGeoJSON) is a real geometry rather than absent or JSON null.
func hasGeometry(geom json.RawMessage) bool {
	t := bytes.TrimSpace(geom)
	return len(t) > 0 && !bytes.Equal(t, []byte("null"))
}

// vectorFeatureProperties turns a plain vector dataset's stored attribute table
// into a Feature's properties object. The table is persisted as a JSON array of
// row objects: a single row becomes the top-level properties directly, multiple
// rows are kept under a "rows" key (so no data is lost while the value stays a
// valid GeoJSON properties object), and an empty/absent table yields {}.
func vectorFeatureProperties(raw json.RawMessage) map[string]any {
	if len(bytes.TrimSpace(raw)) == 0 {
		return map[string]any{}
	}
	var rows []map[string]any
	if err := json.Unmarshal(raw, &rows); err == nil {
		switch len(rows) {
		case 0:
			return map[string]any{}
		case 1:
			return rows[0]
		default:
			return map[string]any{"rows": rows}
		}
	}
	// Fallback: the column already holds a plain object.
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err == nil {
		return obj
	}
	return map[string]any{}
}

// groupObservationsByKato groups observations by KATO code into date->value
// maps. The value is the numeric Value when present, otherwise ValueText,
// otherwise nil (an empty cell). It also groups, per KATO, a start_date->end_date map
// for observations that carry a period (EndDate set); a KATO with no period
// observations gets no entry in periods at all, so callers can tell "no
// periods" apart from "empty periods". order lists KATO codes in first-seen
// order so the resulting feature order is deterministic.
func groupObservationsByKato(obs []domain.Observation) (grouped map[string]map[string]any, periods map[string]map[string]string, order []string) {
	grouped = make(map[string]map[string]any)
	periods = make(map[string]map[string]string)
	order = make([]string, 0)
	for _, o := range obs {
		values, ok := grouped[o.KatoCode]
		if !ok {
			values = make(map[string]any)
			grouped[o.KatoCode] = values
			order = append(order, o.KatoCode)
		}
		values[o.StartDate] = observationValue(o)

		if o.EndDate != nil {
			p, ok := periods[o.KatoCode]
			if !ok {
				p = make(map[string]string)
				periods[o.KatoCode] = p
			}
			p[o.StartDate] = *o.EndDate
		}
	}
	return grouped, periods, order
}

// observationValue returns the typed cell value: numeric Value, else ValueText,
// else nil.
func observationValue(o domain.Observation) any {
	switch {
	case o.Value != nil:
		return *o.Value
	case o.ValueText != nil:
		return *o.ValueText
	default:
		return nil
	}
}

// Get returns a dataset by id.
func (s *DatasetService) Get(ctx context.Context, id uuid.UUID) (domain.Dataset, error) {
	return s.repo.GetByID(ctx, id)
}

// DatasetUpdateInput carries the full set of editable metadata fields for a
// PUT replace. Description and Unit being nil clears those (nullable)
// columns.
type DatasetUpdateInput struct {
	CategoryID  uuid.UUID
	Name        string
	Description *string
	Unit        *string
	// DatasetGroupID, StartDate and EndDate are nullable: nil clears them.
	DatasetGroupID *uuid.UUID
	StartDate      *string
	EndDate        *string
}

// Update replaces a dataset's editable metadata (category, name, description,
// unit). File-related and lifecycle fields are untouched.
func (s *DatasetService) Update(ctx context.Context, id uuid.UUID, in DatasetUpdateInput) (domain.Dataset, error) {
	if err := s.ensureCategoryExists(ctx, in.CategoryID); err != nil {
		return domain.Dataset{}, err
	}
	if err := s.ensureGroupExists(ctx, in.DatasetGroupID); err != nil {
		return domain.Dataset{}, err
	}
	if err := domain.ValidateDatePeriod(in.StartDate, in.EndDate); err != nil {
		return domain.Dataset{}, err
	}
	return s.repo.UpdateFields(ctx, id, domain.DatasetFields{
		CategoryID:     in.CategoryID,
		Name:           in.Name,
		Description:    in.Description,
		Unit:           in.Unit,
		DatasetGroupID: in.DatasetGroupID,
		StartDate:      in.StartDate,
		EndDate:        in.EndDate,
	})
}

// DatasetPatchInput carries a sparse set of metadata field changes. A nil
// CategoryID/Name leaves that column unchanged. Description/Unit are only
// applied when their paired Set flag is true; a nil value with Set true
// clears the (nullable) column.
type DatasetPatchInput struct {
	CategoryID     *uuid.UUID
	Name           *string
	Description    *string
	DescriptionSet bool
	Unit           *string
	UnitSet        bool

	DatasetGroupID    *uuid.UUID
	DatasetGroupIDSet bool
	StartDate         *string
	StartDateSet      bool
	EndDate           *string
	EndDateSet        bool
}

// Patch applies a sparse update to a dataset's editable metadata (category,
// name, description, unit). Fields absent from the input are left unchanged.
func (s *DatasetService) Patch(ctx context.Context, id uuid.UUID, in DatasetPatchInput) (domain.Dataset, error) {
	current, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, err
	}

	categoryID := current.CategoryID
	if in.CategoryID != nil {
		categoryID = *in.CategoryID
		if err := s.ensureCategoryExists(ctx, categoryID); err != nil {
			return domain.Dataset{}, err
		}
	}
	name := current.Name
	if in.Name != nil {
		name = *in.Name
	}
	description := current.Description
	if in.DescriptionSet {
		description = in.Description
	}
	unit := current.Unit
	if in.UnitSet {
		unit = in.Unit
	}
	groupID := current.DatasetGroupID
	if in.DatasetGroupIDSet {
		groupID = in.DatasetGroupID
		if err := s.ensureGroupExists(ctx, groupID); err != nil {
			return domain.Dataset{}, err
		}
	}
	startDate := current.StartDate
	if in.StartDateSet {
		startDate = in.StartDate
	}
	endDate := current.EndDate
	if in.EndDateSet {
		endDate = in.EndDate
	}
	if err := domain.ValidateDatePeriod(startDate, endDate); err != nil {
		return domain.Dataset{}, err
	}

	return s.repo.UpdateFields(ctx, id, domain.DatasetFields{
		CategoryID:     categoryID,
		Name:           name,
		Description:    description,
		Unit:           unit,
		DatasetGroupID: groupID,
		StartDate:      startDate,
		EndDate:        endDate,
	})
}

// ensureCategoryExists validates that categoryID references an existing
// category, mirroring the check performed on upload.
func (s *DatasetService) ensureCategoryExists(ctx context.Context, categoryID uuid.UUID) error {
	if _, err := s.categories.GetByID(ctx, categoryID); err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return fmt.Errorf("%w: category does not exist", domain.ErrValidation)
		}
		return err
	}
	return nil
}

// ensureGroupExists validates that a non-nil dataset group id references an
// existing group. A nil id (no group) is always valid.
func (s *DatasetService) ensureGroupExists(ctx context.Context, id *uuid.UUID) error {
	if id == nil {
		return nil
	}
	if _, err := s.groups.GetByID(ctx, *id); err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return fmt.Errorf("%w: dataset group %s not found", domain.ErrValidation, id)
		}
		return err
	}
	return nil
}

// Status-wait bounds and polling cadence for long polling.
const (
	DefaultStatusWait  = 25 * time.Second
	MaxStatusWait      = 60 * time.Second
	statusPollInterval = 1 * time.Second
)

// DatasetStatusInfo is the minimal status view returned by long polling.
type DatasetStatusInfo struct {
	ID         uuid.UUID `json:"id"`
	Status     string    `json:"status"`
	ParseError *string   `json:"parse_error"`
}

// WaitForStatus implements long polling. If current is empty or already differs
// from the dataset's status it returns immediately; otherwise it waits (up to
// wait, clamped to MaxStatusWait) for the status to change, returning the latest
// status on change or on timeout.
func (s *DatasetService) WaitForStatus(ctx context.Context, id uuid.UUID, current string, wait time.Duration) (DatasetStatusInfo, error) {
	if wait <= 0 {
		wait = DefaultStatusWait
	}
	if wait > MaxStatusWait {
		wait = MaxStatusWait
	}
	deadline := time.Now().Add(wait)

	for {
		d, err := s.repo.GetByID(ctx, id)
		if err != nil {
			return DatasetStatusInfo{}, err
		}
		if current == "" || d.Status != current || !time.Now().Before(deadline) {
			return DatasetStatusInfo{ID: d.ID, Status: d.Status, ParseError: d.ParseError}, nil
		}

		sleep := statusPollInterval
		if rem := time.Until(deadline); rem < sleep {
			sleep = rem
		}
		select {
		case <-ctx.Done():
			return DatasetStatusInfo{}, ctx.Err()
		case <-time.After(sleep):
		}
	}
}

// ListSummaries returns a page of dataset summaries matching filter, always
// ordered by created_at descending. page is 1-based; page and pageSize are
// clamped to sane bounds. When categoryCode is set it is resolved to its
// category id (overriding filter.CategoryID); an unknown code yields an empty
// page.
func (s *DatasetService) ListSummaries(ctx context.Context, filter domain.DatasetFilter, categoryCode *string, page, pageSize int) (DatasetPage, error) {
	if page < 1 {
		page = 1
	}
	if pageSize < 1 {
		pageSize = DefaultPageSize
	}
	if pageSize > MaxPageSize {
		pageSize = MaxPageSize
	}

	if categoryCode != nil {
		category, err := s.categories.GetByCode(ctx, *categoryCode)
		if err != nil {
			if errors.Is(err, domain.ErrNotFound) {
				return DatasetPage{Items: []domain.DatasetSummary{}, Page: page, PageSize: pageSize}, nil
			}
			return DatasetPage{}, err
		}
		filter.CategoryID = &category.ID
	}

	items, err := s.repo.ListSummaries(ctx, filter, pageSize, (page-1)*pageSize)
	if err != nil {
		return DatasetPage{}, err
	}
	total, err := s.repo.Count(ctx, filter)
	if err != nil {
		return DatasetPage{}, err
	}
	return DatasetPage{Items: items, Page: page, PageSize: pageSize, Total: total}, nil
}

// Download returns the dataset metadata and a reader for its stored object. The
// caller must close the reader.
func (s *DatasetService) Download(ctx context.Context, id uuid.UUID) (domain.Dataset, io.ReadCloser, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, nil, err
	}
	obj, err := s.store.Get(ctx, dataset.StorageKey)
	if err != nil {
		return domain.Dataset{}, nil, err
	}
	return dataset, obj, nil
}

// COG returns the dataset metadata and a reader for its Cloud-Optimized GeoTIFF.
// It fails with ErrNotRaster for non-raster datasets and ErrCOGNotReady when the
// raster has not been converted yet. The caller must close the reader.
func (s *DatasetService) COG(ctx context.Context, id uuid.UUID) (domain.Dataset, io.ReadCloser, error) {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.Dataset{}, nil, err
	}
	if dataset.FileType != domain.FileTypeRaster {
		return domain.Dataset{}, nil, ErrNotRaster
	}
	if dataset.CogStorageKey == nil {
		return domain.Dataset{}, nil, ErrCOGNotReady
	}
	obj, err := s.store.Get(ctx, *dataset.CogStorageKey)
	if err != nil {
		return domain.Dataset{}, nil, err
	}
	return dataset, obj, nil
}

// Delete removes the dataset row and its stored object.
func (s *DatasetService) Delete(ctx context.Context, id uuid.UUID) error {
	dataset, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return err
	}
	if err := s.repo.Delete(ctx, id); err != nil {
		return err
	}
	if err := s.store.Remove(ctx, dataset.StorageKey); err != nil {
		// The row is already gone; surface the object-store failure to the caller.
		return err
	}
	return nil
}
