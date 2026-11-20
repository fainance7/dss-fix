package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// stubDatasetRepo is an in-memory DatasetRepository for tests.
type stubDatasetRepo struct {
	store                 map[uuid.UUID]domain.Dataset
	observations          map[uuid.UUID][]domain.Observation
	districts             map[string]domain.District
	createErr             error
	deleted               []uuid.UUID
	cleared               []uuid.UUID
	lastLimit, lastOffset int
	lastFilter            domain.DatasetFilter
}

func newStubDatasetRepo() *stubDatasetRepo {
	return &stubDatasetRepo{
		store:        map[uuid.UUID]domain.Dataset{},
		observations: map[uuid.UUID][]domain.Observation{},
		districts:    map[string]domain.District{},
	}
}

func (r *stubDatasetRepo) Create(_ context.Context, d domain.Dataset) (domain.Dataset, error) {
	if r.createErr != nil {
		return domain.Dataset{}, r.createErr
	}
	if d.ID == uuid.Nil {
		d.ID = uuid.New()
	}
	r.store[d.ID] = d
	return d, nil
}

func (r *stubDatasetRepo) GetByID(_ context.Context, id uuid.UUID) (domain.Dataset, error) {
	d, ok := r.store[id]
	if !ok {
		return domain.Dataset{}, domain.ErrNotFound
	}
	return d, nil
}

func (r *stubDatasetRepo) UpdateFields(_ context.Context, id uuid.UUID, f domain.DatasetFields) (domain.Dataset, error) {
	d, ok := r.store[id]
	if !ok {
		return domain.Dataset{}, domain.ErrNotFound
	}
	d.CategoryID = f.CategoryID
	d.Name = f.Name
	d.Description = f.Description
	d.Unit = f.Unit
	d.DatasetGroupID = f.DatasetGroupID
	d.StartDate = f.StartDate
	d.EndDate = f.EndDate
	r.store[id] = d
	return d, nil
}

func (r *stubDatasetRepo) LatestInGroup(_ context.Context, groupID uuid.UUID, status *string, fileType *domain.FileType) (domain.Dataset, error) {
	var best *domain.Dataset
	for _, d := range r.store {
		d := d
		if d.DatasetGroupID == nil || *d.DatasetGroupID != groupID {
			continue
		}
		if status != nil && d.Status != *status {
			continue
		}
		if fileType != nil && d.FileType != *fileType {
			continue
		}
		if best == nil || laterPeriod(d, *best) {
			best = &d
		}
	}
	if best == nil {
		return domain.Dataset{}, domain.ErrNotFound
	}
	return *best, nil
}

// laterPeriod reports whether a is fresher than b: greater end_date, NULL
// end_date last, then newer created_at.
func laterPeriod(a, b domain.Dataset) bool {
	switch {
	case a.EndDate != nil && b.EndDate == nil:
		return true
	case a.EndDate == nil && b.EndDate != nil:
		return false
	case a.EndDate != nil && b.EndDate != nil && *a.EndDate != *b.EndDate:
		return *a.EndDate > *b.EndDate
	default:
		return a.CreatedAt.After(b.CreatedAt)
	}
}

func (r *stubDatasetRepo) ListSummaries(_ context.Context, filter domain.DatasetFilter, limit, offset int) ([]domain.DatasetSummary, error) {
	r.lastFilter = filter
	r.lastLimit = limit
	r.lastOffset = offset

	var matched []domain.DatasetSummary
	for _, d := range r.store {
		if filter.FileType != nil && d.FileType != *filter.FileType {
			continue
		}
		matched = append(matched, domain.DatasetSummary{ID: d.ID, FileType: d.FileType})
	}
	sort.Slice(matched, func(i, j int) bool { return matched[i].ID.String() < matched[j].ID.String() })

	if offset >= len(matched) {
		return nil, nil
	}
	end := offset + limit
	if end > len(matched) {
		end = len(matched)
	}
	return matched[offset:end], nil
}

func (r *stubDatasetRepo) Count(_ context.Context, _ domain.DatasetFilter) (int, error) {
	return len(r.store), nil
}

func (r *stubDatasetRepo) Delete(_ context.Context, id uuid.UUID) error {
	r.deleted = append(r.deleted, id)
	delete(r.store, id)
	return nil
}

func (r *stubDatasetRepo) MarkParsed(_ context.Context, id uuid.UUID, cols []domain.AttributeColumn) error {
	d, ok := r.store[id]
	if !ok {
		return domain.ErrNotFound
	}
	d.AttributeColumns = cols
	d.Status = domain.DatasetStatusAwaitingMapping
	r.store[id] = d
	return nil
}

func (r *stubDatasetRepo) MarkParseFailed(_ context.Context, id uuid.UUID, reason string) error {
	d, ok := r.store[id]
	if !ok {
		return domain.ErrNotFound
	}
	d.Status = domain.DatasetStatusFailed
	d.ParseError = &reason
	r.store[id] = d
	return nil
}

func (r *stubDatasetRepo) SaveMapping(_ context.Context, id uuid.UUID, kato string, years []domain.YearColumn) (domain.Dataset, error) {
	d, ok := r.store[id]
	if !ok {
		return domain.Dataset{}, domain.ErrNotFound
	}
	d.KatoColumn = &kato
	d.YearColumns = years
	d.Status = domain.DatasetStatusExtracting
	r.store[id] = d
	return d, nil
}

func (r *stubDatasetRepo) SetSLD(_ context.Context, id uuid.UUID, key, filename, contentType string) (domain.Dataset, error) {
	d, ok := r.store[id]
	if !ok {
		return domain.Dataset{}, domain.ErrNotFound
	}
	d.SldStorageKey = &key
	d.SldFilename = &filename
	d.SldContentType = &contentType
	r.store[id] = d
	return d, nil
}

func (r *stubDatasetRepo) MarkReady(_ context.Context, id uuid.UUID, geometry []byte) error {
	d, ok := r.store[id]
	if !ok {
		return domain.ErrNotFound
	}
	if len(geometry) > 0 {
		d.Geometry = geometry
	}
	d.Status = domain.DatasetStatusReady
	r.store[id] = d
	return nil
}

func (r *stubDatasetRepo) MarkConverted(_ context.Context, id uuid.UUID, cogKey string, footprint []byte) error {
	d, ok := r.store[id]
	if !ok {
		return domain.ErrNotFound
	}
	d.CogStorageKey = &cogKey
	if len(footprint) > 0 {
		d.Geometry = footprint
	}
	d.Status = domain.DatasetStatusReady
	r.store[id] = d
	return nil
}

func (r *stubDatasetRepo) SetProperties(_ context.Context, id uuid.UUID, properties, geometry []byte) error {
	d, ok := r.store[id]
	if !ok {
		return domain.ErrNotFound
	}
	d.Properties = properties
	if len(geometry) > 0 {
		d.Geometry = geometry
	}
	d.Status = domain.DatasetStatusReady
	r.store[id] = d
	return nil
}

func (r *stubDatasetRepo) ClearGeometryAndGeoJSON(_ context.Context, id uuid.UUID) error {
	d, ok := r.store[id]
	if !ok {
		return domain.ErrNotFound
	}
	d.Geometry = nil
	d.GeoJSON = nil
	r.store[id] = d
	r.cleared = append(r.cleared, id)
	return nil
}

func (r *stubDatasetRepo) SetGeoJSON(_ context.Context, id uuid.UUID, geojson []byte) error {
	d, ok := r.store[id]
	if !ok {
		return domain.ErrNotFound
	}
	d.GeoJSON = geojson
	r.store[id] = d
	return nil
}

func (r *stubDatasetRepo) ReplaceObservations(_ context.Context, id uuid.UUID, obs []domain.Observation) error {
	r.observations[id] = obs
	return nil
}

func (r *stubDatasetRepo) ListObservations(_ context.Context, id uuid.UUID, _ *string, _, _ int) ([]domain.Observation, error) {
	return r.observations[id], nil
}

func (r *stubDatasetRepo) CountObservations(_ context.Context, id uuid.UUID, _ *string) (int, error) {
	return len(r.observations[id]), nil
}

func (r *stubDatasetRepo) ListAllObservations(_ context.Context, id uuid.UUID) ([]domain.Observation, error) {
	return r.observations[id], nil
}

func (r *stubDatasetRepo) DistrictGeometriesByKato(_ context.Context, katos []string) (map[string]domain.District, error) {
	out := make(map[string]domain.District)
	for _, k := range katos {
		if d, ok := r.districts[k]; ok {
			out[k] = d
		}
	}
	return out, nil
}

// stubEnqueuer records parse, properties, extract, and convert enqueues.
type stubEnqueuer struct {
	enqueued   []uuid.UUID
	properties []uuid.UUID
	extracted  []uuid.UUID
	converted  []uuid.UUID
	err        error
}

func (s *stubEnqueuer) EnqueueParse(_ context.Context, id uuid.UUID) error {
	if s.err != nil {
		return s.err
	}
	s.enqueued = append(s.enqueued, id)
	return nil
}

func (s *stubEnqueuer) EnqueueProperties(_ context.Context, id uuid.UUID) error {
	if s.err != nil {
		return s.err
	}
	s.properties = append(s.properties, id)
	return nil
}

func (s *stubEnqueuer) EnqueueExtract(_ context.Context, id uuid.UUID) error {
	if s.err != nil {
		return s.err
	}
	s.extracted = append(s.extracted, id)
	return nil
}

func (s *stubEnqueuer) EnqueueConvert(_ context.Context, id uuid.UUID) error {
	if s.err != nil {
		return s.err
	}
	s.converted = append(s.converted, id)
	return nil
}

// stubConverter records raster conversions.
type stubConverter struct {
	cogCalls         int
	toCOGErr         error
	footprint        []byte
	footprintFn      func(src string) ([]byte, error)
	vectorFeatures   []json.RawMessage
	vectorFeaturesFn func(src string) ([]json.RawMessage, error)
}

func (c *stubConverter) ToCOG(_ context.Context, _, dst string) error {
	c.cogCalls++
	if c.toCOGErr != nil {
		return c.toCOGErr
	}
	return os.WriteFile(dst, []byte("COG"), 0o600)
}

func (c *stubConverter) Footprint(_ context.Context, src string) ([]byte, error) {
	if c.footprintFn != nil {
		return c.footprintFn(src)
	}
	return c.footprint, nil
}

func (c *stubConverter) VectorFeatures(_ context.Context, src string) ([]json.RawMessage, error) {
	if c.vectorFeaturesFn != nil {
		return c.vectorFeaturesFn(src)
	}
	return c.vectorFeatures, nil
}

var (
	noopParser    ColumnParser = func(string, []byte) ([]domain.AttributeColumn, error) { return nil, nil }
	noopRowParser RowParser    = func(string, []byte) ([]map[string]string, error) { return nil, nil }
)

// stubStore records object-store interactions and keeps stored bodies so tests
// can read them back.
type stubStore struct {
	put     []string
	removed []string
	putErr  error
	objects map[string][]byte
}

func newStubStore() *stubStore { return &stubStore{objects: map[string][]byte{}} }

func (s *stubStore) Put(_ context.Context, key string, r io.Reader, _ int64, _ string) error {
	if s.putErr != nil {
		return s.putErr
	}
	s.put = append(s.put, key)
	if s.objects != nil {
		body, err := io.ReadAll(r)
		if err != nil {
			return err
		}
		s.objects[key] = body
	}
	return nil
}

func (s *stubStore) Get(_ context.Context, key string) (io.ReadCloser, error) {
	if body, ok := s.objects[key]; ok {
		return io.NopCloser(bytes.NewReader(body)), nil
	}
	return io.NopCloser(strings.NewReader("")), nil
}

func (s *stubStore) Remove(_ context.Context, key string) error {
	s.removed = append(s.removed, key)
	return nil
}

// stubCategoryReader satisfies the unexported categoryReader dependency.
type stubCategoryReader struct {
	exists bool
}

func (s stubCategoryReader) GetByID(_ context.Context, id uuid.UUID) (domain.Category, error) {
	if !s.exists {
		return domain.Category{}, domain.ErrNotFound
	}
	return domain.Category{ID: id}, nil
}

func (s stubCategoryReader) GetByCode(_ context.Context, code string) (domain.Category, error) {
	if !s.exists {
		return domain.Category{}, domain.ErrNotFound
	}
	return domain.Category{ID: uuid.New(), Code: code}, nil
}

func validUpload() UploadInput {
	body := `{"type":"FeatureCollection","features":[]}`
	return UploadInput{
		CategoryID: uuid.New(),
		Name:       "Population",
		Filename:   "data.geojson",
		FileType:   domain.FileTypeVector,
		Size:       int64(len(body)),
		Reader:     strings.NewReader(body),
	}
}

func newDatasetService(repo *stubDatasetRepo, store *stubStore, catExists bool) *DatasetService {
	return newDatasetServiceWithGroups(repo, store, catExists, newStubGroupRepo())
}

// newDatasetServiceWithGroups builds a DatasetService over a caller-supplied
// group repository, for tests that need a pre-populated dataset group.
func newDatasetServiceWithGroups(repo *stubDatasetRepo, store *stubStore, catExists bool, groups *stubGroupRepo) *DatasetService {
	return NewDatasetService(repo, store, stubCategoryReader{exists: catExists}, groups, &stubEnqueuer{}, noopParser, noopRowParser, &stubConverter{})
}

func TestDatasetService_Upload_Validation(t *testing.T) {
	ctx := context.Background()

	tests := []struct {
		name   string
		mutate func(*UploadInput)
	}{
		{"invalid file type", func(in *UploadInput) { in.FileType = "bogus" }},
		{"unknown extension", func(in *UploadInput) { in.Filename = "data.txt" }},
		{"extension/type mismatch", func(in *UploadInput) { in.Filename = "data.tif" }}, // .tif is raster
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			repo := newStubDatasetRepo()
			store := &stubStore{}
			svc := newDatasetService(repo, store, true)

			in := validUpload()
			tt.mutate(&in)

			_, err := svc.Upload(ctx, in)
			if !errors.Is(err, domain.ErrValidation) {
				t.Fatalf("want ErrValidation, got %v", err)
			}
			if len(store.put) != 0 {
				t.Fatalf("nothing should be uploaded on validation failure, got %v", store.put)
			}
		})
	}
}

func TestDatasetService_Upload_RejectsMismatchedContent(t *testing.T) {
	repo := newStubDatasetRepo()
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	// Declared raster .tif but the bytes are JSON, not a TIFF.
	in := validUpload()
	in.FileType = domain.FileTypeRaster
	in.Filename = "fake.tif"
	in.Reader = strings.NewReader(`{"type":"FeatureCollection"}`)

	_, err := svc.Upload(context.Background(), in)
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
	if len(store.put) != 0 {
		t.Fatalf("mismatched file should not be stored, got %v", store.put)
	}
}

func TestDatasetService_Upload_MissingCategory(t *testing.T) {
	svc := newDatasetService(newStubDatasetRepo(), &stubStore{}, false)
	_, err := svc.Upload(context.Background(), validUpload())
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetService_Upload_Success(t *testing.T) {
	repo := newStubDatasetRepo()
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	in := validUpload()
	in.Name = "" // should fall back to filename

	got, err := svc.Upload(context.Background(), in)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Name != in.Filename {
		t.Fatalf("name should default to filename, got %q", got.Name)
	}
	if len(store.put) != 1 {
		t.Fatalf("want one stored object, got %v", store.put)
	}
	if got.StorageKey != store.put[0] {
		t.Fatalf("dataset storage key %q != stored key %q", got.StorageKey, store.put[0])
	}
}

func TestDatasetService_Upload_CompensatesOnDBFailure(t *testing.T) {
	repo := newStubDatasetRepo()
	repo.createErr = errors.New("insert failed")
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	_, err := svc.Upload(context.Background(), validUpload())
	if err == nil {
		t.Fatal("expected an error")
	}
	if len(store.put) != 1 || len(store.removed) != 1 {
		t.Fatalf("orphaned object not cleaned up: put=%v removed=%v", store.put, store.removed)
	}
	if store.put[0] != store.removed[0] {
		t.Fatalf("removed key %q != stored key %q", store.removed[0], store.put[0])
	}
}

func TestDatasetService_Upload_VectorWithKato_EnqueuesParse(t *testing.T) {
	repo := newStubDatasetRepo()
	enq := &stubEnqueuer{}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), enq, noopParser, noopRowParser, &stubConverter{})

	in := validUpload()
	in.FileType = domain.FileTypeVectorWithKato
	in.Filename = "regions.geojson"

	got, err := svc.Upload(context.Background(), in)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Status != domain.DatasetStatusParsing {
		t.Fatalf("want status parsing, got %q", got.Status)
	}
	if len(enq.enqueued) != 1 || enq.enqueued[0] != got.ID {
		t.Fatalf("parse not enqueued for dataset: %v", enq.enqueued)
	}
}

func TestDatasetService_Upload_Vector_EnqueuesProperties(t *testing.T) {
	repo := newStubDatasetRepo()
	enq := &stubEnqueuer{}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), enq, noopParser, noopRowParser, &stubConverter{})

	got, err := svc.Upload(context.Background(), validUpload()) // plain vector .geojson
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Status != domain.DatasetStatusProcessing {
		t.Fatalf("want status processing, got %q", got.Status)
	}
	if len(enq.properties) != 1 || enq.properties[0] != got.ID {
		t.Fatalf("properties extraction not enqueued: %v", enq.properties)
	}
}

func TestDatasetService_ExtractProperties(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector,
		Filename: "d.geojson", StorageKey: "k", Status: domain.DatasetStatusProcessing,
	}
	rows := []map[string]string{
		{"name": "Astana", "pop": "1000"},
		{"name": "Almaty", "pop": "2000"},
	}
	rp := RowParser(func(string, []byte) ([]map[string]string, error) { return rows, nil })
	geoms := []json.RawMessage{
		json.RawMessage(`{"type":"Polygon","coordinates":[[[1,1],[2,2],[3,1],[1,1]]]}`),
		json.RawMessage(`{"type":"Polygon","coordinates":[[[4,4],[5,5],[6,4],[4,4]]]}`),
	}
	conv := &stubConverter{vectorFeatures: geoms}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, noopParser, rp, conv)

	if err := svc.ExtractProperties(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := repo.store[id]
	if got.Status != domain.DatasetStatusReady {
		t.Fatalf("want ready, got %q", got.Status)
	}
	var parsed []map[string]string
	if err := json.Unmarshal(got.Properties, &parsed); err != nil {
		t.Fatalf("properties not valid JSON: %v (%s)", err, got.Properties)
	}
	if len(parsed) != 2 {
		t.Fatalf("want 2 rows in properties, got %d", len(parsed))
	}
	wantGeometry := `{"type":"GeometryCollection","geometries":[` + string(geoms[0]) + `,` + string(geoms[1]) + `]}`
	if string(got.Geometry) != wantGeometry {
		t.Fatalf("want dissolved geometry %s, got %s", wantGeometry, got.Geometry)
	}

	// Regression: each polygon must keep its own attribute row as its Feature's
	// properties, not a single dissolved geometry sharing every row.
	var fc domain.FeatureCollection
	if err := json.Unmarshal(got.GeoJSON, &fc); err != nil {
		t.Fatalf("geojson not persisted / invalid: %v (%s)", err, got.GeoJSON)
	}
	if len(fc.Features) != 2 {
		t.Fatalf("want 2 features (one per polygon), got %d: %s", len(fc.Features), got.GeoJSON)
	}
	if string(fc.Features[0].Geometry) != string(geoms[0]) || fc.Features[0].Properties["name"] != "Astana" {
		t.Fatalf("feature 0 not paired with its own geometry/row: %+v", fc.Features[0])
	}
	if string(fc.Features[1].Geometry) != string(geoms[1]) || fc.Features[1].Properties["name"] != "Almaty" {
		t.Fatalf("feature 1 not paired with its own geometry/row: %+v", fc.Features[1])
	}
}

func TestDatasetService_ExtractProperties_GeometryBestEffort(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector,
		Filename: "d.geojson", StorageKey: "k", Status: domain.DatasetStatusProcessing,
	}
	rp := RowParser(func(string, []byte) ([]map[string]string, error) {
		return []map[string]string{{"name": "Astana"}}, nil
	})
	// Geometry extraction fails; the job must still succeed with properties set.
	conv := &stubConverter{vectorFeaturesFn: func(string) ([]json.RawMessage, error) {
		return nil, errors.New("ogr2ogr boom")
	}}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, noopParser, rp, conv)

	if err := svc.ExtractProperties(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := repo.store[id]
	if got.Status != domain.DatasetStatusReady {
		t.Fatalf("want ready, got %q", got.Status)
	}
	if got.Geometry != nil {
		t.Fatalf("expected nil geometry on extraction failure, got %s", got.Geometry)
	}
	if got.Properties == nil {
		t.Fatalf("expected properties to be set despite geometry failure")
	}
}

func TestDatasetService_ExtractProperties_NoTable(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector,
		Filename: "d.geojson", StorageKey: "k", Status: domain.DatasetStatusProcessing,
	}
	// features with no attributes
	rp := RowParser(func(string, []byte) ([]map[string]string, error) {
		return []map[string]string{{}, {}}, nil
	})
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, noopParser, rp, &stubConverter{})

	if err := svc.ExtractProperties(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := repo.store[id]
	if got.Status != domain.DatasetStatusReady {
		t.Fatalf("want ready, got %q", got.Status)
	}
	if got.Properties != nil {
		t.Fatalf("expected nil properties for empty table, got %s", got.Properties)
	}
}

func TestDatasetService_Upload_Raster_EnqueuesConvert(t *testing.T) {
	repo := newStubDatasetRepo()
	enq := &stubEnqueuer{}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), enq, noopParser, noopRowParser, &stubConverter{})

	in := validUpload()
	in.FileType = domain.FileTypeRaster
	in.Filename = "dem.tif"
	in.Reader = bytes.NewReader([]byte("II*\x00\x08\x00\x00\x00")) // TIFF magic

	got, err := svc.Upload(context.Background(), in)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Status != domain.DatasetStatusProcessing {
		t.Fatalf("want status processing, got %q", got.Status)
	}
	if len(enq.converted) != 1 || enq.converted[0] != got.ID {
		t.Fatalf("conversion not enqueued: %v", enq.converted)
	}
}

func TestDatasetService_ConvertToCOG(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeRaster,
		Filename: "dem.tif", StorageKey: "uid/dem.tif",
		Status: domain.DatasetStatusProcessing,
	}
	store := &stubStore{}
	footprint := []byte(`{"type":"Polygon","coordinates":[[[70,50],[72,50],[72,52],[70,52],[70,50]]]}`)
	conv := &stubConverter{footprint: footprint}
	svc := NewDatasetService(repo, store, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, noopParser, noopRowParser, conv)

	if err := svc.ConvertToCOG(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := repo.store[id]
	if got.Status != domain.DatasetStatusReady {
		t.Fatalf("want ready, got %q", got.Status)
	}
	if got.CogStorageKey == nil || *got.CogStorageKey != "uid/cog/dem.tif" {
		t.Fatalf("unexpected cog key: %v", got.CogStorageKey)
	}
	if string(got.Geometry) != string(footprint) {
		t.Fatalf("footprint not stored: %s", got.Geometry)
	}
	if conv.cogCalls != 1 {
		t.Fatalf("ToCOG calls = %d, want 1", conv.cogCalls)
	}
	if len(store.put) != 1 || store.put[0] != "uid/cog/dem.tif" {
		t.Fatalf("cog not uploaded: %v", store.put)
	}
}

func TestDatasetService_ConvertToCOG_RecordsFailure(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeRaster,
		Filename: "dem.tif", StorageKey: "uid/dem.tif",
		Status: domain.DatasetStatusProcessing,
	}
	conv := &stubConverter{toCOGErr: errors.New("gdal failed")}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, noopParser, noopRowParser, conv)

	if err := svc.ConvertToCOG(context.Background(), id); err != nil {
		t.Fatalf("conversion failure should be recorded, not returned: %v", err)
	}
	if repo.store[id].Status != domain.DatasetStatusFailed {
		t.Fatalf("want failed, got %q", repo.store[id].Status)
	}
}

func TestDatasetService_Parse(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVectorWithKato,
		Filename: "r.geojson", StorageKey: "k", Status: domain.DatasetStatusParsing,
	}
	cols := []domain.AttributeColumn{{Name: "като"}, {Name: "F_2023"}}
	parser := ColumnParser(func(string, []byte) ([]domain.AttributeColumn, error) { return cols, nil })
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, parser, noopRowParser, &stubConverter{})

	if err := svc.Parse(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := repo.store[id]
	if got.Status != domain.DatasetStatusAwaitingMapping {
		t.Fatalf("want awaiting_mapping, got %q", got.Status)
	}
	if len(got.AttributeColumns) != 2 {
		t.Fatalf("columns not stored: %v", got.AttributeColumns)
	}
}

func TestDatasetService_Parse_RecordsFailure(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVectorWithKato,
		Filename: "r.zip", StorageKey: "k", Status: domain.DatasetStatusParsing,
	}
	parser := ColumnParser(func(string, []byte) ([]domain.AttributeColumn, error) {
		return nil, errors.New("corrupt archive")
	})
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, parser, noopRowParser, &stubConverter{})

	if err := svc.Parse(context.Background(), id); err != nil {
		t.Fatalf("parse failure should be recorded, not returned: %v", err)
	}
	got := repo.store[id]
	if got.Status != domain.DatasetStatusFailed {
		t.Fatalf("want failed, got %q", got.Status)
	}
	if got.ParseError == nil || *got.ParseError == "" {
		t.Fatal("expected parse error to be recorded")
	}
}

func TestDatasetService_SaveMapping(t *testing.T) {
	ctx := context.Background()
	id := uuid.New()
	base := domain.Dataset{
		ID: id, FileType: domain.FileTypeVectorWithKato,
		Status:           domain.DatasetStatusAwaitingMapping,
		AttributeColumns: []domain.AttributeColumn{{Name: "като"}, {Name: "F_2023"}},
	}
	validYears := []domain.YearColumn{{Column: "F_2023", StartDate: "2023-01-01"}}

	newSvc := func() (*stubDatasetRepo, *DatasetService) {
		repo := newStubDatasetRepo()
		repo.store[id] = base
		return repo, newDatasetService(repo, &stubStore{}, true)
	}

	t.Run("unknown kato column", func(t *testing.T) {
		_, svc := newSvc()
		_, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "missing", YearColumns: validYears})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("unknown year column", func(t *testing.T) {
		_, svc := newSvc()
		_, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "като", YearColumns: []domain.YearColumn{{Column: "X", StartDate: "2023-01-01"}}})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("bad start_date", func(t *testing.T) {
		_, svc := newSvc()
		_, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "като", YearColumns: []domain.YearColumn{{Column: "F_2023", StartDate: "2023"}}})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("bad end_date", func(t *testing.T) {
		_, svc := newSvc()
		_, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "като", YearColumns: []domain.YearColumn{{Column: "F_2023", StartDate: "2023-01-01", EndDate: strPtr("2023")}}})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("end_date before start_date", func(t *testing.T) {
		_, svc := newSvc()
		_, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "като", YearColumns: []domain.YearColumn{{Column: "F_2023", StartDate: "2023-12-31", EndDate: strPtr("2023-01-01")}}})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("period mapping succeeds", func(t *testing.T) {
		repo, svc := newSvc()
		got, err := svc.SaveMapping(ctx, id, MappingInput{
			KatoColumn:  "като",
			YearColumns: []domain.YearColumn{{Column: "F_2023", StartDate: "2020-01-01", EndDate: strPtr("2023-12-31")}},
		})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(got.YearColumns) != 1 || got.YearColumns[0].EndDate == nil || *got.YearColumns[0].EndDate != "2023-12-31" {
			t.Fatalf("period not saved: %+v", got.YearColumns)
		}
		if len(repo.store[id].YearColumns) != 1 {
			t.Fatalf("period not persisted: %+v", repo.store[id].YearColumns)
		}
	})

	t.Run("no year columns", func(t *testing.T) {
		_, svc := newSvc()
		_, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "като"})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("wrong state is a conflict", func(t *testing.T) {
		repo := newStubDatasetRepo()
		d := base
		d.Status = domain.DatasetStatusParsing
		repo.store[id] = d
		svc := newDatasetService(repo, &stubStore{}, true)
		_, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "като", YearColumns: validYears})
		if !errors.Is(err, domain.ErrConflict) {
			t.Fatalf("want ErrConflict, got %v", err)
		}
	})

	t.Run("success moves to extracting and enqueues extraction", func(t *testing.T) {
		repo := newStubDatasetRepo()
		repo.store[id] = base
		enq := &stubEnqueuer{}
		svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), enq, noopParser, noopRowParser, &stubConverter{})

		got, err := svc.SaveMapping(ctx, id, MappingInput{KatoColumn: "като", YearColumns: validYears})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got.Status != domain.DatasetStatusExtracting {
			t.Fatalf("want extracting, got %q", got.Status)
		}
		if got.KatoColumn == nil || *got.KatoColumn != "като" {
			t.Fatalf("kato column not saved: %+v", got.KatoColumn)
		}
		if len(enq.extracted) != 1 || enq.extracted[0] != id {
			t.Fatalf("extraction not enqueued: %v", enq.extracted)
		}
	})
}

func TestBuildObservations(t *testing.T) {
	id := uuid.New()
	years := []domain.YearColumn{
		{Column: "F_2023", StartDate: "2023-01-01"},
		{Column: "D_2025", StartDate: "2025-01-01"},
	}
	rows := []map[string]string{
		{"като": "751010000", "F_2023": "100", "D_2025": "n/a"},
		{"като": "751020000", "F_2023": "150", "D_2025": "250"},
		{"като": "", "F_2023": "999"}, // skipped: no KATO code
	}

	obs := buildObservations(id, "като", years, rows)
	if len(obs) != 4 { // 2 valid rows x 2 years
		t.Fatalf("want 4 observations, got %d", len(obs))
	}

	byKey := map[string]domain.Observation{}
	for _, o := range obs {
		byKey[o.KatoCode+"|"+o.StartDate] = o
	}
	if o := byKey["751010000|2023-01-01"]; o.Value == nil || *o.Value != 100 {
		t.Errorf("numeric cell not stored as value: %+v", o)
	}
	if o := byKey["751010000|2025-01-01"]; o.ValueText == nil || *o.ValueText != "n/a" {
		t.Errorf("non-numeric cell not stored as value_text: %+v", o)
	}
	for _, o := range obs {
		if o.EndDate != nil {
			t.Fatalf("point-in-time mapping must not set EndDate, got %+v", o)
		}
	}
}

func TestBuildObservations_Period(t *testing.T) {
	id := uuid.New()
	years := []domain.YearColumn{
		{Column: "F_2020_2023", StartDate: "2020-01-01", EndDate: strPtr("2023-12-31")}, // period
		{Column: "F_2024", StartDate: "2024-01-01"},                                     // point-in-time, unaffected
	}
	rows := []map[string]string{
		{"като": "751010000", "F_2020_2023": "100", "F_2024": "200"},
	}

	obs := buildObservations(id, "като", years, rows)
	if len(obs) != 2 {
		t.Fatalf("want 2 observations, got %d", len(obs))
	}

	byStartDate := map[string]domain.Observation{}
	for _, o := range obs {
		byStartDate[o.StartDate] = o
	}

	period := byStartDate["2020-01-01"]
	if period.EndDate == nil || *period.EndDate != "2023-12-31" {
		t.Fatalf("period observation missing end_date: %+v", period)
	}
	if period.Value == nil || *period.Value != 100 {
		t.Fatalf("period observation value wrong: %+v", period)
	}

	point := byStartDate["2024-01-01"]
	if point.EndDate != nil {
		t.Fatalf("point-in-time observation must have nil end_date (backward compat), got %+v", point)
	}
}

func TestDatasetService_Extract(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	kato := "като"
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVectorWithKato,
		Filename: "r.geojson", StorageKey: "k",
		Status:      domain.DatasetStatusExtracting,
		KatoColumn:  &kato,
		YearColumns: []domain.YearColumn{{Column: "F_2023", StartDate: "2023-01-01"}},
	}
	repo.districts["751010000"] = domain.District{
		Kato: "751010000", Name: "Almaty",
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[76,43],[77,43],[77,44],[76,43]]]}`),
	}
	rows := []map[string]string{{"като": "751010000", "F_2023": "100"}}
	rp := RowParser(func(string, []byte) ([]map[string]string, error) { return rows, nil })
	geoms := []json.RawMessage{json.RawMessage(`{"type":"Polygon","coordinates":[[[76,43],[77,43],[77,44],[76,43]]]}`)}
	geom := []byte(`{"type":"GeometryCollection","geometries":[` + string(geoms[0]) + `]}`)
	conv := &stubConverter{vectorFeatures: geoms}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), &stubEnqueuer{}, noopParser, rp, conv)

	if err := svc.Extract(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if repo.store[id].Status != domain.DatasetStatusReady {
		t.Fatalf("want ready, got %q", repo.store[id].Status)
	}
	if string(repo.store[id].Geometry) != string(geom) {
		t.Fatalf("want geometry %s, got %s", geom, repo.store[id].Geometry)
	}
	got := repo.observations[id]
	if len(got) != 1 || got[0].KatoCode != "751010000" || got[0].Value == nil || *got[0].Value != 100 {
		t.Fatalf("unexpected observations: %+v", got)
	}
	// Extraction also assembles and persists the GeoJSON (district-joined).
	var fc domain.FeatureCollection
	if err := json.Unmarshal(repo.store[id].GeoJSON, &fc); err != nil {
		t.Fatalf("geojson not persisted / invalid: %v", err)
	}
	if len(fc.Features) != 1 || fc.Features[0].Properties["name"] != "Almaty" {
		t.Fatalf("unexpected persisted geojson: %s", repo.store[id].GeoJSON)
	}
}

// mustGeoJSON generates and persists the dataset's GeoJSON (as the worker does),
// then reads it back through the .geojson accessor and decodes it into a
// FeatureCollection so tests can assert on its content.
func mustGeoJSON(t *testing.T, svc *DatasetService, id uuid.UUID) domain.FeatureCollection {
	t.Helper()
	if err := svc.generateGeoJSON(context.Background(), id); err != nil {
		t.Fatalf("generateGeoJSON: %v", err)
	}
	raw, err := svc.GeoJSON(context.Background(), id)
	if err != nil {
		t.Fatalf("GeoJSON: %v", err)
	}
	var fc domain.FeatureCollection
	if err := json.Unmarshal(raw, &fc); err != nil {
		t.Fatalf("decode geojson: %v", err)
	}
	return fc
}

func TestDatasetService_GeoJSON_DistrictJoin(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeVectorWithKato, Status: domain.DatasetStatusReady}

	v2020, v2021 := 100.0, 150.0
	repo.observations[id] = []domain.Observation{
		{KatoCode: "710000000", StartDate: "2020-01-01", Value: &v2020},
		{KatoCode: "710000000", StartDate: "2021-01-01", Value: &v2021},
		{KatoCode: "999999999", StartDate: "2020-01-01", Value: &v2020}, // no district -> skipped
	}
	repo.districts["710000000"] = domain.District{
		Kato: "710000000", Name: "Astana",
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[71,51],[72,51],[72,52],[71,51]]]}`),
	}

	svc := newDatasetService(repo, &stubStore{}, true)
	fc := mustGeoJSON(t, svc, id)
	if fc.Type != domain.GeoJSONFeatureCollection {
		t.Fatalf("type = %q", fc.Type)
	}
	if len(fc.Features) != 1 {
		t.Fatalf("want 1 feature (the unmatched KATO is skipped), got %d", len(fc.Features))
	}
	f := fc.Features[0]
	if f.Type != domain.GeoJSONFeature {
		t.Fatalf("feature type = %q", f.Type)
	}
	if len(f.Geometry) == 0 {
		t.Fatal("feature geometry should come from the district")
	}
	if f.Properties["kato"] != "710000000" || f.Properties["name"] != "Astana" {
		t.Fatalf("unexpected properties: %+v", f.Properties)
	}
	data, ok := f.Properties["data"].(map[string]any)
	if !ok {
		t.Fatalf("observations not nested under data: %+v", f.Properties)
	}
	if data["2020-01-01"] != 100.0 || data["2021-01-01"] != 150.0 {
		t.Fatalf("year values not nested under data: %+v", data)
	}

	// The whole thing must marshal to valid GeoJSON.
	if _, err := json.Marshal(fc); err != nil {
		t.Fatalf("feature collection not valid JSON: %v", err)
	}
}

func TestDatasetService_GeoJSON_Period(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeVectorWithKato, Status: domain.DatasetStatusReady}

	period, point := 100.0, 200.0
	repo.observations[id] = []domain.Observation{
		{KatoCode: "710000000", StartDate: "2020-01-01", EndDate: strPtr("2023-12-31"), Value: &period},
		{KatoCode: "710000000", StartDate: "2024-01-01", Value: &point}, // point-in-time, no period
	}
	repo.districts["710000000"] = domain.District{
		Kato: "710000000", Name: "Astana",
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[71,51],[72,51],[72,52],[71,51]]]}`),
	}

	svc := newDatasetService(repo, &stubStore{}, true)
	fc := mustGeoJSON(t, svc, id)
	if len(fc.Features) != 1 {
		t.Fatalf("want 1 feature, got %d", len(fc.Features))
	}
	f := fc.Features[0]

	data, ok := f.Properties["data"].(map[string]any)
	if !ok {
		t.Fatalf("observations not nested under data: %+v", f.Properties)
	}
	if data["2020-01-01"] != 100.0 || data["2024-01-01"] != 200.0 {
		t.Fatalf("unexpected data: %+v", data)
	}

	periods, ok := f.Properties["periods"].(map[string]any)
	if !ok {
		t.Fatalf("periods not exposed as a map: %+v", f.Properties)
	}
	if len(periods) != 1 || periods["2020-01-01"] != "2023-12-31" {
		t.Fatalf("period end date not exposed: %+v", periods)
	}
	if _, has := periods["2024-01-01"]; has {
		t.Fatalf("point-in-time date must not appear in periods: %+v", periods)
	}
}

func TestDatasetService_GeoJSON_NoPeriods_OmitsPeriodsKey(t *testing.T) {
	// Regression: a dataset mapped without any period must produce output
	// byte-identical to before periods existed - no "periods" key at all.
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeVectorWithKato, Status: domain.DatasetStatusReady}
	v := 100.0
	repo.observations[id] = []domain.Observation{{KatoCode: "710000000", StartDate: "2020-01-01", Value: &v}}
	repo.districts["710000000"] = domain.District{
		Kato: "710000000", Name: "Astana",
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[71,51],[72,51],[72,52],[71,51]]]}`),
	}

	svc := newDatasetService(repo, &stubStore{}, true)
	fc := mustGeoJSON(t, svc, id)
	if _, has := fc.Features[0].Properties["periods"]; has {
		t.Fatalf("periods key must be absent when no observation has a period: %+v", fc.Features[0].Properties)
	}
}

func TestDatasetService_GeoJSON_IgnoresDatasetGeometry(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	// Dataset HAS its own geometry, which GeoJSON must ignore entirely for a
	// vector_with_kato dataset, joining the districts table on KATO code instead.
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVectorWithKato, Status: domain.DatasetStatusReady,
		Name: "Population", Geometry: json.RawMessage(`{"type":"MultiPolygon","coordinates":[[[[0,0]]]]}`),
	}
	v := 100.0
	repo.observations[id] = []domain.Observation{
		{KatoCode: "710000000", StartDate: "2020-01-01", Value: &v},
		{KatoCode: "999999999", StartDate: "2020-01-01", Value: &v}, // no district -> skipped
	}
	district := json.RawMessage(`{"type":"Polygon","coordinates":[[[71,51],[72,51],[72,52],[71,51]]]}`)
	repo.districts["710000000"] = domain.District{Kato: "710000000", Name: "Astana", Geometry: district}

	svc := newDatasetService(repo, &stubStore{}, true)
	fc := mustGeoJSON(t, svc, id)
	if len(fc.Features) != 1 {
		t.Fatalf("want 1 feature (unmatched KATO skipped), got %d", len(fc.Features))
	}
	f := fc.Features[0]
	if string(f.Geometry) != string(district) {
		t.Fatalf("geometry must come from the district, not the dataset: %s", f.Geometry)
	}
	if f.Properties["kato"] != "710000000" || f.Properties["name"] != "Astana" {
		t.Fatalf("unexpected properties: %+v", f.Properties)
	}
	data, ok := f.Properties["data"].(map[string]any)
	if !ok {
		t.Fatalf("observations must be nested under data: %+v", f.Properties)
	}
	if data["2020-01-01"] != 100.0 {
		t.Fatalf("observation value not mapped under data: %+v", data)
	}
	if _, flat := f.Properties["2020-01-01"]; flat {
		t.Fatalf("observation dates must not be flat in properties: %+v", f.Properties)
	}
}

func TestDatasetService_GeoJSON_ReturnsStoredColumn(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	stored := json.RawMessage(`{"type":"FeatureCollection","features":[{"type":"Feature","geometry":null,"properties":{"kato":"710000000"}}]}`)
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVectorWithKato, Status: domain.DatasetStatusReady,
		GeoJSON: stored,
	}
	// Observations/districts are intentionally left empty: GeoJSON must serve the
	// persisted column verbatim without re-assembling anything.
	svc := newDatasetService(repo, &stubStore{}, true)

	raw, err := svc.GeoJSON(context.Background(), id)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if string(raw) != string(stored) {
		t.Fatalf("want stored column verbatim, got %s", raw)
	}
}

func TestDatasetService_GeoJSON_LazilyGeneratesAndCaches(t *testing.T) {
	// A dataset that became ready before the geojson column existed: the first
	// request assembles it, persists it, and serves it from the cache thereafter.
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeVectorWithKato, Status: domain.DatasetStatusReady}
	v := 100.0
	repo.observations[id] = []domain.Observation{{KatoCode: "710000000", StartDate: "2020-01-01", Value: &v}}
	repo.districts["710000000"] = domain.District{
		Kato: "710000000", Name: "Astana",
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[71,51],[72,51],[72,52],[71,51]]]}`),
	}
	svc := newDatasetService(repo, &stubStore{}, true)

	raw, err := svc.GeoJSON(context.Background(), id)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var fc domain.FeatureCollection
	if err := json.Unmarshal(raw, &fc); err != nil {
		t.Fatalf("decode geojson: %v", err)
	}
	if len(fc.Features) != 1 || fc.Features[0].Properties["name"] != "Astana" {
		t.Fatalf("lazy-assembled geojson wrong: %s", raw)
	}
	// It must be cached on the dataset so subsequent requests skip re-assembly.
	if string(repo.store[id].GeoJSON) != string(raw) {
		t.Fatalf("geojson not cached after first request: %s", repo.store[id].GeoJSON)
	}
}

func TestDatasetService_GeoJSON_ConflictWhenNotReady(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeVectorWithKato, Status: domain.DatasetStatusExtracting}
	svc := newDatasetService(repo, &stubStore{}, true)

	if _, err := svc.GeoJSON(context.Background(), id); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("want ErrConflict for non-ready dataset, got %v", err)
	}
}

func TestDatasetService_GeoJSON_RejectsRaster(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeRaster, Status: domain.DatasetStatusReady}
	svc := newDatasetService(repo, &stubStore{}, true)

	if _, err := svc.GeoJSON(context.Background(), id); !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetService_GeoJSON_Vector_GeometryOnly(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	geom := json.RawMessage(`{"type":"MultiPolygon","coordinates":[[[[1,1],[2,2],[3,1],[1,1]]]]}`)
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector, Status: domain.DatasetStatusReady,
		Name: "Roads", Geometry: geom,
	}
	svc := newDatasetService(repo, &stubStore{}, true)

	fc := mustGeoJSON(t, svc, id)
	if len(fc.Features) != 1 {
		t.Fatalf("want a single geometry-only feature, got %d", len(fc.Features))
	}
	f := fc.Features[0]
	if string(f.Geometry) != string(geom) {
		t.Fatalf("feature should reuse the dataset geometry, got %s", f.Geometry)
	}
	if len(f.Properties) != 0 {
		t.Fatalf("vector feature should have empty properties, got %+v", f.Properties)
	}
}

func TestDatasetService_GeoJSON_Vector_TableDataAsProperties(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	geom := json.RawMessage(`{"type":"Polygon","coordinates":[[[1,1],[2,2],[3,1],[1,1]]]}`)
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector, Status: domain.DatasetStatusReady,
		Geometry:   geom,
		Properties: json.RawMessage(`[{"name":"Astana","pop":"1000"}]`), // single gpkg row
	}
	svc := newDatasetService(repo, &stubStore{}, true)

	fc := mustGeoJSON(t, svc, id)
	if len(fc.Features) != 1 {
		t.Fatalf("want 1 feature, got %d", len(fc.Features))
	}
	props := fc.Features[0].Properties
	if props["name"] != "Astana" || props["pop"] != "1000" {
		t.Fatalf("table data not exposed as top-level properties: %+v", props)
	}
}

func TestDatasetService_GeoJSON_Vector_MultiRowTableData(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	geom := json.RawMessage(`{"type":"Polygon","coordinates":[[[1,1],[2,2],[3,1],[1,1]]]}`)
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector, Status: domain.DatasetStatusReady,
		Geometry:   geom,
		Properties: json.RawMessage(`[{"name":"Astana"},{"name":"Almaty"}]`),
	}
	svc := newDatasetService(repo, &stubStore{}, true)

	fc := mustGeoJSON(t, svc, id)
	// After the JSON round trip through the stored column, the nested rows decode
	// into a generic []any of objects.
	rows, ok := fc.Features[0].Properties["rows"].([]any)
	if !ok || len(rows) != 2 {
		t.Fatalf("multi-row table data not kept under \"rows\": %+v", fc.Features[0].Properties)
	}
}

func TestDatasetService_GeoJSON_Vector_NoGeometry(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeVector, Status: domain.DatasetStatusReady}
	svc := newDatasetService(repo, &stubStore{}, true)

	fc := mustGeoJSON(t, svc, id)
	if fc.Type != domain.GeoJSONFeatureCollection {
		t.Fatalf("type = %q", fc.Type)
	}
	if len(fc.Features) != 0 {
		t.Fatalf("want empty feature collection when vector has no geometry, got %d", len(fc.Features))
	}
}

func TestDatasetService_ListSummaries_ClampsPaging(t *testing.T) {
	repo := newStubDatasetRepo()
	repo.store[uuid.New()] = domain.Dataset{}
	svc := newDatasetService(repo, &stubStore{}, true)

	// page < 1 -> 1, pageSize > max -> MaxPageSize, offset = 0.
	res, err := svc.ListSummaries(context.Background(), domain.DatasetFilter{}, nil, 0, 10_000)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if res.Page != 1 || res.PageSize != MaxPageSize {
		t.Fatalf("clamp failed: page=%d pageSize=%d", res.Page, res.PageSize)
	}
	if repo.lastLimit != MaxPageSize || repo.lastOffset != 0 {
		t.Fatalf("repo got limit=%d offset=%d", repo.lastLimit, repo.lastOffset)
	}
	if res.Total != 1 {
		t.Fatalf("total = %d, want 1", res.Total)
	}

	// page 3, pageSize 20 -> offset 40.
	if _, err := svc.ListSummaries(context.Background(), domain.DatasetFilter{}, nil, 3, 20); err != nil {
		t.Fatal(err)
	}
	if repo.lastOffset != 40 || repo.lastLimit != 20 {
		t.Fatalf("expected limit=20 offset=40, got limit=%d offset=%d", repo.lastLimit, repo.lastOffset)
	}
}

func TestDatasetService_WaitForStatus(t *testing.T) {
	ctx := context.Background()
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, Status: domain.DatasetStatusReady}
	svc := newDatasetService(repo, &stubStore{}, true)

	t.Run("returns immediately when status differs from current", func(t *testing.T) {
		info, err := svc.WaitForStatus(ctx, id, domain.DatasetStatusProcessing, time.Minute)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if info.Status != domain.DatasetStatusReady || info.ID != id {
			t.Fatalf("unexpected info: %+v", info)
		}
	})

	t.Run("returns immediately when no current is given", func(t *testing.T) {
		info, err := svc.WaitForStatus(ctx, id, "", time.Minute)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if info.Status != domain.DatasetStatusReady {
			t.Fatalf("status = %q", info.Status)
		}
	})

	t.Run("times out returning the unchanged status", func(t *testing.T) {
		start := time.Now()
		info, err := svc.WaitForStatus(ctx, id, domain.DatasetStatusReady, 30*time.Millisecond)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if info.Status != domain.DatasetStatusReady {
			t.Fatalf("status = %q", info.Status)
		}
		if elapsed := time.Since(start); elapsed > time.Second {
			t.Fatalf("timed out too slowly: %v", elapsed)
		}
	})

	t.Run("not found", func(t *testing.T) {
		_, err := svc.WaitForStatus(ctx, uuid.New(), "", time.Minute)
		if !errors.Is(err, domain.ErrNotFound) {
			t.Fatalf("want ErrNotFound, got %v", err)
		}
	})
}

func TestDatasetService_Reprocess_ClearsStaleGeometryAndGeoJSON(t *testing.T) {
	repo := newStubDatasetRepo()
	id := uuid.New()
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector, Status: domain.DatasetStatusReady,
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[1,1],[2,2],[3,1],[1,1]]]}`),
		GeoJSON:  json.RawMessage(`{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"rows":[]}}]}`),
	}
	enq := &stubEnqueuer{}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), enq, noopParser, noopRowParser, &stubConverter{})

	if _, err := svc.Reprocess(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := repo.store[id]
	if got.Geometry != nil || got.GeoJSON != nil {
		t.Fatalf("want stale geometry/geojson cleared, got geometry=%s geojson=%s", got.Geometry, got.GeoJSON)
	}
	if len(enq.properties) != 1 || enq.properties[0] != id {
		t.Fatalf("want properties job enqueued for %s, got %+v", id, enq.properties)
	}
}

func TestDatasetService_ReprocessAll_FiltersByFileType(t *testing.T) {
	repo := newStubDatasetRepo()
	vectorID, rasterID := uuid.New(), uuid.New()
	repo.store[vectorID] = domain.Dataset{
		ID: vectorID, FileType: domain.FileTypeVector,
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[1,1],[2,2],[3,1],[1,1]]]}`),
		GeoJSON:  json.RawMessage(`{"type":"FeatureCollection","features":[]}`),
	}
	repo.store[rasterID] = domain.Dataset{
		ID: rasterID, FileType: domain.FileTypeRaster,
		Geometry: json.RawMessage(`{"type":"Polygon","coordinates":[[[1,1],[2,2],[3,1],[1,1]]]}`),
	}
	enq := &stubEnqueuer{}
	svc := NewDatasetService(repo, &stubStore{}, stubCategoryReader{exists: true}, newStubGroupRepo(), enq, noopParser, noopRowParser, &stubConverter{})

	vectorType := domain.FileTypeVector
	enqueued, failures, err := svc.ReprocessAll(context.Background(), &vectorType)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(failures) != 0 {
		t.Fatalf("unexpected failures: %+v", failures)
	}
	if enqueued != 1 {
		t.Fatalf("want 1 dataset enqueued, got %d", enqueued)
	}
	if len(enq.properties) != 1 || enq.properties[0] != vectorID {
		t.Fatalf("want only the vector dataset's properties job enqueued, got %+v", enq.properties)
	}
	if len(enq.converted) != 0 {
		t.Fatalf("raster dataset must not be reprocessed when --type=vector, got %+v", enq.converted)
	}
	if got := repo.store[vectorID]; got.Geometry != nil || got.GeoJSON != nil {
		t.Fatalf("vector dataset's stale geometry/geojson must be cleared, got geometry=%s geojson=%s", got.Geometry, got.GeoJSON)
	}
	if got := repo.store[rasterID]; got.Geometry == nil {
		t.Fatalf("raster dataset outside the --type filter must be left untouched")
	}
}

func validSLDUpload() UploadSLDInput {
	body := `<?xml version="1.0"?><StyledLayerDescriptor></StyledLayerDescriptor>`
	return UploadSLDInput{
		Filename:    "style.sld",
		ContentType: "application/xml",
		Size:        int64(len(body)),
		Reader:      strings.NewReader(body),
	}
}

func TestDatasetService_UploadSLD_Success(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeRaster, StorageKey: "uid/dem.tif"}
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	got, err := svc.UploadSLD(context.Background(), id, validSLDUpload())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.SldStorageKey == nil || *got.SldStorageKey != "uid/sld/style.sld" {
		t.Fatalf("unexpected sld storage key: %v", got.SldStorageKey)
	}
	if got.SldFilename == nil || *got.SldFilename != "style.sld" {
		t.Fatalf("sld filename not stored: %v", got.SldFilename)
	}
	if len(store.put) != 1 || store.put[0] != "uid/sld/style.sld" {
		t.Fatalf("sld not uploaded to store: %v", store.put)
	}
}

func TestDatasetService_UploadSLD_ReplacesPrevious(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	oldKey := "uid/sld/old.sld"
	repo.store[id] = domain.Dataset{
		ID: id, FileType: domain.FileTypeVector, StorageKey: "uid/data.geojson",
		SldStorageKey: &oldKey,
	}
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	in := validSLDUpload()
	in.Filename = "new.sld"
	got, err := svc.UploadSLD(context.Background(), id, in)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.SldStorageKey == nil || *got.SldStorageKey != "uid/sld/new.sld" {
		t.Fatalf("unexpected sld storage key: %v", got.SldStorageKey)
	}
	if len(store.removed) != 1 || store.removed[0] != oldKey {
		t.Fatalf("old sld object not removed: %v", store.removed)
	}
}

func TestDatasetService_UploadSLD_RejectsBadExtension(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, StorageKey: "uid/data.geojson"}
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	in := validSLDUpload()
	in.Filename = "style.zip"
	_, err := svc.UploadSLD(context.Background(), id, in)
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
	if len(store.put) != 0 {
		t.Fatalf("nothing should be uploaded on validation failure, got %v", store.put)
	}
}

func TestDatasetService_UploadSLD_RejectsMismatchedContent(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, StorageKey: "uid/data.geojson"}
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	in := validSLDUpload()
	in.Reader = strings.NewReader(`{"not":"xml"}`)
	_, err := svc.UploadSLD(context.Background(), id, in)
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetService_DownloadSLD(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, StorageKey: "uid/data.geojson"}
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	t.Run("not found before upload", func(t *testing.T) {
		_, _, err := svc.DownloadSLD(context.Background(), id)
		if !errors.Is(err, ErrSLDNotFound) {
			t.Fatalf("want ErrSLDNotFound, got %v", err)
		}
	})

	t.Run("streams after upload", func(t *testing.T) {
		if _, err := svc.UploadSLD(context.Background(), id, validSLDUpload()); err != nil {
			t.Fatalf("upload: %v", err)
		}
		_, obj, err := svc.DownloadSLD(context.Background(), id)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		defer obj.Close()
		if _, err := io.ReadAll(obj); err != nil {
			t.Fatalf("read: %v", err)
		}
	})
}

func TestDatasetService_Delete_RemovesObject(t *testing.T) {
	repo := newStubDatasetRepo()
	id := uuid.New()
	repo.store[id] = domain.Dataset{ID: id, StorageKey: "key/data.geojson"}
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	if err := svc.Delete(context.Background(), id); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(repo.deleted) != 1 || repo.deleted[0] != id {
		t.Fatalf("row not deleted: %v", repo.deleted)
	}
	if len(store.removed) != 1 || store.removed[0] != "key/data.geojson" {
		t.Fatalf("object not removed: %v", store.removed)
	}
}

func strPtr(s string) *string { return &s }

func TestDatasetService_Update_Success(t *testing.T) {
	repo := newStubDatasetRepo()
	id := uuid.New()
	repo.store[id] = domain.Dataset{ID: id, Name: "Old", Description: strPtr("old desc"), Unit: strPtr("old unit")}
	svc := newDatasetService(repo, &stubStore{}, true)

	newCategory := uuid.New()
	got, err := svc.Update(context.Background(), id, DatasetUpdateInput{
		CategoryID:  newCategory,
		Name:        "New",
		Description: strPtr("new desc"),
		Unit:        nil,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.CategoryID != newCategory || got.Name != "New" {
		t.Fatalf("update not applied: %+v", got)
	}
	if got.Description == nil || *got.Description != "new desc" {
		t.Fatalf("description not updated: %v", got.Description)
	}
	if got.Unit != nil {
		t.Fatalf("unit should be cleared, got %v", *got.Unit)
	}
}

func TestDatasetService_Update_MissingCategory(t *testing.T) {
	repo := newStubDatasetRepo()
	id := uuid.New()
	repo.store[id] = domain.Dataset{ID: id, Name: "Old"}
	svc := newDatasetService(repo, &stubStore{}, false)

	_, err := svc.Update(context.Background(), id, DatasetUpdateInput{CategoryID: uuid.New(), Name: "New"})
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetService_Update_NotFound(t *testing.T) {
	repo := newStubDatasetRepo()
	svc := newDatasetService(repo, &stubStore{}, true)

	_, err := svc.Update(context.Background(), uuid.New(), DatasetUpdateInput{CategoryID: uuid.New(), Name: "New"})
	if !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestDatasetService_Patch_PartialUpdate(t *testing.T) {
	repo := newStubDatasetRepo()
	id := uuid.New()
	origCategory := uuid.New()
	repo.store[id] = domain.Dataset{
		ID: id, CategoryID: origCategory, Name: "Old",
		Description: strPtr("old desc"), Unit: strPtr("old unit"),
	}
	svc := newDatasetService(repo, &stubStore{}, true)

	// Only Name is set; everything else must be left unchanged.
	got, err := svc.Patch(context.Background(), id, DatasetPatchInput{Name: strPtr("New")})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Name != "New" {
		t.Fatalf("name not updated: %+v", got)
	}
	if got.CategoryID != origCategory {
		t.Fatalf("category should be unchanged, got %v", got.CategoryID)
	}
	if got.Description == nil || *got.Description != "old desc" {
		t.Fatalf("description should be unchanged, got %v", got.Description)
	}
	if got.Unit == nil || *got.Unit != "old unit" {
		t.Fatalf("unit should be unchanged, got %v", got.Unit)
	}
}

func TestDatasetService_Patch_ClearsNullableField(t *testing.T) {
	repo := newStubDatasetRepo()
	id := uuid.New()
	repo.store[id] = domain.Dataset{ID: id, Name: "Old", Description: strPtr("old desc")}
	svc := newDatasetService(repo, &stubStore{}, true)

	got, err := svc.Patch(context.Background(), id, DatasetPatchInput{DescriptionSet: true, Description: nil})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Description != nil {
		t.Fatalf("description should be cleared, got %v", *got.Description)
	}
}

func TestDatasetService_Patch_MissingCategory(t *testing.T) {
	repo := newStubDatasetRepo()
	id := uuid.New()
	repo.store[id] = domain.Dataset{ID: id, Name: "Old"}
	svc := newDatasetService(repo, &stubStore{}, false)

	newCategory := uuid.New()
	_, err := svc.Patch(context.Background(), id, DatasetPatchInput{CategoryID: &newCategory})
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetService_Patch_NotFound(t *testing.T) {
	repo := newStubDatasetRepo()
	svc := newDatasetService(repo, &stubStore{}, true)

	_, err := svc.Patch(context.Background(), uuid.New(), DatasetPatchInput{Name: strPtr("New")})
	if !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestDatasetService_Upload_StoresPeriodAndGroup(t *testing.T) {
	repo := newStubDatasetRepo()
	groups := newStubGroupRepo()
	svc := newDatasetServiceWithGroups(repo, newStubStore(), true, groups)

	gid := uuid.New()
	groups.store[gid] = domain.DatasetGroup{ID: gid, Code: "pop", Name: "Population"}
	start, end := "2020-01-01", "2020-12-31"
	in := validUpload()
	in.DatasetGroupID = &gid
	in.StartDate = &start
	in.EndDate = &end

	ds, err := svc.Upload(context.Background(), in)
	if err != nil {
		t.Fatal(err)
	}
	if ds.DatasetGroupID == nil || *ds.DatasetGroupID != gid || *ds.StartDate != start || *ds.EndDate != end {
		t.Fatalf("period/group not stored: %+v", ds)
	}
}

func TestDatasetService_Upload_RejectsReversedPeriod(t *testing.T) {
	store := &stubStore{}
	svc := newDatasetService(newStubDatasetRepo(), store, true)

	start, end := "2021-01-01", "2020-12-31"
	in := validUpload()
	in.StartDate = &start
	in.EndDate = &end

	if _, err := svc.Upload(context.Background(), in); !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
	if len(store.put) != 0 {
		t.Fatalf("nothing should be uploaded on validation failure, got %v", store.put)
	}
}

func TestDatasetService_DownloadSLD_FallsBackToGroup(t *testing.T) {
	ctx := context.Background()
	repo := newStubDatasetRepo()
	store := newStubStore()
	groups := newStubGroupRepo()
	svc := newDatasetServiceWithGroups(repo, store, true, groups)

	groupSLD := "groups/style.sld"
	filename, contentType := "group.sld", "application/xml"
	group := domain.DatasetGroup{
		ID: uuid.New(), Code: "pop", Name: "Population",
		SldStorageKey: &groupSLD, SldFilename: &filename, SldContentType: &contentType,
	}
	groups.store[group.ID] = group
	store.objects[groupSLD] = []byte("<StyledLayerDescriptor/>")

	id := uuid.New()
	repo.store[id] = domain.Dataset{ID: id, DatasetGroupID: &group.ID}

	dataset, obj, err := svc.DownloadSLD(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	defer obj.Close()

	if dataset.SldStorageKey == nil || *dataset.SldStorageKey != groupSLD {
		t.Fatalf("want group sld key %s, got %v", groupSLD, dataset.SldStorageKey)
	}
	if dataset.SldFilename == nil || *dataset.SldFilename != filename {
		t.Fatalf("want group sld filename %s, got %v", filename, dataset.SldFilename)
	}
	body, err := io.ReadAll(obj)
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "<StyledLayerDescriptor/>" {
		t.Fatalf("want the group's sld content, got %q", body)
	}
}

func TestDatasetService_DownloadSLD_NoFallbackWhenGroupHasNoSLD(t *testing.T) {
	repo := newStubDatasetRepo()
	groups := newStubGroupRepo()
	svc := newDatasetServiceWithGroups(repo, newStubStore(), true, groups)

	group := domain.DatasetGroup{ID: uuid.New(), Code: "pop", Name: "Population"}
	groups.store[group.ID] = group
	id := uuid.New()
	repo.store[id] = domain.Dataset{ID: id, DatasetGroupID: &group.ID}

	if _, _, err := svc.DownloadSLD(context.Background(), id); !errors.Is(err, ErrSLDNotFound) {
		t.Fatalf("want ErrSLDNotFound, got %v", err)
	}
}

func TestDatasetService_Upload_RejectsUnknownGroup(t *testing.T) {
	svc := newDatasetService(newStubDatasetRepo(), newStubStore(), true)
	in := validUpload()
	missing := uuid.New()
	in.DatasetGroupID = &missing

	if _, err := svc.Upload(context.Background(), in); !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetService_CreateRasterFromFile(t *testing.T) {
	ctx := context.Background()
	repo := newStubDatasetRepo()
	store := newStubStore()
	groups := newStubGroupRepo()
	jobs := &stubEnqueuer{}
	svc := NewDatasetService(repo, store, stubCategoryReader{exists: true}, groups, jobs, noopParser, noopRowParser, &stubConverter{})

	group := domain.DatasetGroup{ID: uuid.New(), Code: "lp-z", Name: "Landscape Z"}
	groups.store[group.ID] = group

	// A minimal little-endian TIFF header is enough for content sniffing.
	path := filepath.Join(t.TempDir(), "lp-z-integrated.tif")
	if err := os.WriteFile(path, []byte("II*\x00 raster payload"), 0o600); err != nil {
		t.Fatal(err)
	}
	end := "2024-12-31"

	dataset, err := svc.CreateRasterFromFile(ctx, CreateRasterInput{
		CategoryID:     uuid.New(),
		DatasetGroupID: &group.ID,
		Name:           "Landscape Z integrated",
		Filename:       "lp-z-integrated.tif",
		Path:           path,
		EndDate:        &end,
		Automated:      true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if dataset.FileType != domain.FileTypeRaster {
		t.Fatalf("want raster file type, got %q", dataset.FileType)
	}
	if dataset.DatasetGroupID == nil || *dataset.DatasetGroupID != group.ID {
		t.Fatalf("want group %s, got %v", group.ID, dataset.DatasetGroupID)
	}
	if dataset.EndDate == nil || *dataset.EndDate != end {
		t.Fatalf("want end date %s, got %v", end, dataset.EndDate)
	}
	if !dataset.Automated {
		t.Fatal("want automated dataset")
	}
	if dataset.SizeBytes != int64(len("II*\x00 raster payload")) {
		t.Fatalf("want the file size, got %d", dataset.SizeBytes)
	}
	if len(store.put) != 1 {
		t.Fatalf("want the raster stored once, got %v", store.put)
	}
	if len(jobs.converted) != 1 || jobs.converted[0] != dataset.ID {
		t.Fatalf("want a convert job for %s, got %v", dataset.ID, jobs.converted)
	}
}

func TestDatasetService_CreateRasterFromFile_MissingFile(t *testing.T) {
	svc := newDatasetService(newStubDatasetRepo(), newStubStore(), true)
	_, err := svc.CreateRasterFromFile(context.Background(), CreateRasterInput{
		CategoryID: uuid.New(),
		Filename:   "missing.tif",
		Path:       filepath.Join(t.TempDir(), "missing.tif"),
	})
	if err == nil {
		t.Fatal("want an error for a missing file")
	}
}

func TestSafeObjectName(t *testing.T) {
	cases := []struct {
		name    string
		in      string
		want    string
		wantErr bool
	}{
		{"plain name", "style.sld", "style.sld", false},
		{"trims space", "  style.sld  ", "style.sld", false},
		{"nested path reduced to base", "a/b/style.sld", "style.sld", false},
		{"traversal reduced to base", "../../x.sld", "x.sld", false},
		{"absolute path reduced to base", "/etc/passwd", "passwd", false},
		{"dotdot alone", "..", "", true},
		{"dot alone", ".", "", true},
		{"root alone", "/", "", true},
		{"empty", "", "", true},
		{"blank", "   ", "", true},
		{"trailing slash", "a/b/", "b", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := safeObjectName(c.in)
			if c.wantErr {
				if err == nil {
					t.Fatalf("want error, got %q", got)
				}
				if !errors.Is(err, domain.ErrValidation) {
					t.Fatalf("want ErrValidation, got %v", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != c.want {
				t.Fatalf("safeObjectName(%q) = %q, want %q", c.in, got, c.want)
			}
		})
	}
}

func TestDatasetService_UploadSLD_RejectsPathTraversal(t *testing.T) {
	id := uuid.New()
	repo := newStubDatasetRepo()
	repo.store[id] = domain.Dataset{ID: id, FileType: domain.FileTypeRaster, StorageKey: "uid/dem.tif"}
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	in := validSLDUpload()
	in.Filename = "../../other/sld/evil.sld"
	got, err := svc.UploadSLD(context.Background(), id, in)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.SldStorageKey == nil || *got.SldStorageKey != "uid/sld/evil.sld" {
		t.Fatalf("sld key escaped the dataset prefix: %v", got.SldStorageKey)
	}
	if len(store.put) != 1 || !strings.HasPrefix(store.put[0], "uid/sld/") || !strings.HasSuffix(store.put[0], "evil.sld") {
		t.Fatalf("object stored outside the dataset sld prefix: %v", store.put)
	}
}

func TestDatasetService_Upload_SanitisesTraversalFilename(t *testing.T) {
	repo := newStubDatasetRepo()
	store := &stubStore{}
	svc := newDatasetService(repo, store, true)

	in := validUpload()
	in.Filename = "../../evil.geojson"
	got, err := svc.Upload(context.Background(), in)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// The key is "<uuid>/<basename>": exactly two segments, no traversal.
	parts := strings.Split(got.StorageKey, "/")
	if len(parts) != 2 || parts[1] != "evil.geojson" {
		t.Fatalf("storage key escaped its uuid prefix: %q", got.StorageKey)
	}
	if len(store.put) != 1 || store.put[0] != got.StorageKey {
		t.Fatalf("object stored under an unexpected key: %v", store.put)
	}
}
