package domain

import (
	"bytes"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// FileType classifies the kind of geo file a dataset holds.
type FileType string

const (
	FileTypeVectorWithKato FileType = "vector_with_kato"
	FileTypeVector         FileType = "vector"
	FileTypeRaster         FileType = "raster"
)

// Valid reports whether the file type is one of the known values.
func (ft FileType) Valid() bool {
	_, ok := allowedExtensions[ft]
	return ok
}

// Dataset lifecycle statuses.
const (
	// DatasetStatusPending is the initial state before any processing.
	DatasetStatusPending = "pending"
	// DatasetStatusParsing means a vector_with_kato file's attribute table is
	// being parsed asynchronously.
	DatasetStatusParsing = "parsing"
	// DatasetStatusProcessing means a raster is being converted to a
	// Cloud-Optimized GeoTIFF.
	DatasetStatusProcessing = "processing"
	// DatasetStatusAwaitingMapping means columns were detected and the user must
	// choose the KATO column and map year columns.
	DatasetStatusAwaitingMapping = "awaiting_mapping"
	// DatasetStatusExtracting means the mapping was saved and the attribute table
	// is being unpivoted into observations.
	DatasetStatusExtracting = "extracting"
	// DatasetStatusReady means the dataset is fully configured and extracted.
	DatasetStatusReady = "ready"
	// DatasetStatusFailed means parsing or extraction failed; see ParseError.
	DatasetStatusFailed = "failed"
)

// datasetStatuses is the set of valid dataset lifecycle statuses.
var datasetStatuses = map[string]struct{}{
	DatasetStatusPending:         {},
	DatasetStatusParsing:         {},
	DatasetStatusProcessing:      {},
	DatasetStatusAwaitingMapping: {},
	DatasetStatusExtracting:      {},
	DatasetStatusReady:           {},
	DatasetStatusFailed:          {},
}

// ValidDatasetStatus reports whether s is a known dataset lifecycle status.
func ValidDatasetStatus(s string) bool {
	_, ok := datasetStatuses[s]
	return ok
}

// DatasetFilter holds optional filters for listing dataset summaries. A nil
// field places no constraint on that attribute; listings are always ordered by
// created_at descending regardless of the filter.
type DatasetFilter struct {
	CategoryID *uuid.UUID
	FileType   *FileType
	Automated  *bool
	Status     *string
	// DatasetGroupID restricts the listing to one dataset group.
	DatasetGroupID *uuid.UUID
	// StartDateFrom / StartDateTo and EndDateFrom / EndDateTo are inclusive
	// YYYY-MM-DD bounds on the dataset's period columns.
	StartDateFrom *string
	StartDateTo   *string
	EndDateFrom   *string
	EndDateTo     *string
}

// Observation is a single unpivoted value from a dataset's attribute table,
// keyed by KATO code and date (optionally a StartDate..EndDate period). Exactly
// one of Value / ValueText is typically set (numeric vs non-numeric cell); both
// may be nil for an empty cell.
type Observation struct {
	ID        uuid.UUID `json:"id"`
	DatasetID uuid.UUID `json:"dataset_id"`
	KatoCode  string    `json:"kato_code"`
	StartDate string    `json:"start_date"`
	// EndDate is the period's end date. Nil for a point-in-time observation
	// (pre-existing behavior).
	EndDate   *string  `json:"end_date,omitempty"`
	Value     *float64 `json:"value"`
	ValueText *string  `json:"value_text"`
}

// allowedExtensions lists the accepted lowercase file extensions (including the
// dot) for each file type.
var allowedExtensions = map[FileType][]string{
	FileTypeVectorWithKato: {".zip", ".geojson", ".gpkg"},
	FileTypeVector:         {".geojson", ".gpkg", ".zip"},
	FileTypeRaster:         {".tif", ".tiff"},
}

// AllowedExtensions returns the accepted extensions for a file type.
func AllowedExtensions(ft FileType) []string {
	return allowedExtensions[ft]
}

// ExtensionAllowedFor reports whether ext (lowercase, with dot) is valid for ft.
func ExtensionAllowedFor(ft FileType, ext string) bool {
	for _, e := range allowedExtensions[ft] {
		if e == ext {
			return true
		}
	}
	return false
}

// ValidateFileContent performs a lightweight magic-byte/shape check that an
// uploaded file's content matches its extension, catching mislabeled uploads at
// request time. head is the first bytes of the file; the worker performs the
// full parse/convert later.
func ValidateFileContent(ext string, head []byte) error {
	switch ext {
	case ".tif", ".tiff":
		// Classic TIFF: little-endian "II*\0" or big-endian "MM\0*".
		// BigTIFF (version 43) uses "II+\0" / "MM\0+"; GDAL writes it for
		// large rasters (BIGTIFF=IF_SAFER), so the pipeline's own COG output
		// must pass this check too.
		tiffMagic := [][]byte{
			[]byte("II*\x00"), []byte("MM\x00*"),
			[]byte("II+\x00"), []byte("MM\x00+"),
		}
		ok := false
		for _, m := range tiffMagic {
			if bytes.HasPrefix(head, m) {
				ok = true
				break
			}
		}
		if !ok {
			return fmt.Errorf("file is not a valid TIFF/BigTIFF GeoTIFF")
		}
	case ".zip":
		// ZIP local-file or empty-archive signature.
		if !bytes.HasPrefix(head, []byte("PK\x03\x04")) && !bytes.HasPrefix(head, []byte("PK\x05\x06")) {
			return fmt.Errorf("file is not a valid ZIP archive")
		}
	case ".gpkg":
		// GeoPackage is an SQLite 3 database.
		if !bytes.HasPrefix(head, []byte("SQLite format 3\x00")) {
			return fmt.Errorf("file is not a valid GeoPackage (SQLite) file")
		}
	case ".geojson", ".json":
		// A GeoJSON FeatureCollection/Feature is a JSON object.
		if b, ok := firstMeaningfulByte(head); !ok || b != '{' {
			return fmt.Errorf("file is not valid GeoJSON")
		}
	case ".sld", ".xml":
		// SLD is XML; a lightweight sniff for the opening tag.
		if b, ok := firstMeaningfulByte(head); !ok || b != '<' {
			return fmt.Errorf("file is not valid XML/SLD")
		}
	}
	return nil
}

// AllowedSLDExtensions lists the accepted lowercase extensions (including the
// dot) for a dataset's SLD (Styled Layer Descriptor) style file.
var AllowedSLDExtensions = []string{".sld", ".xml"}

// ExtensionAllowedForSLD reports whether ext (lowercase, with dot) is a valid
// SLD file extension.
func ExtensionAllowedForSLD(ext string) bool {
	for _, e := range AllowedSLDExtensions {
		if e == ext {
			return true
		}
	}
	return false
}

// firstMeaningfulByte returns the first non-whitespace byte after an optional
// UTF-8 BOM.
func firstMeaningfulByte(head []byte) (byte, bool) {
	head = bytes.TrimPrefix(head, []byte{0xEF, 0xBB, 0xBF})
	for _, b := range head {
		switch b {
		case ' ', '\t', '\r', '\n':
			continue
		default:
			return b, true
		}
	}
	return 0, false
}

// AttributeColumn is a detected column from a vector file's attribute table,
// with a few sample values to help the user identify it (e.g. the KATO column).
type AttributeColumn struct {
	Name    string   `json:"name"`
	Samples []string `json:"samples,omitempty"`
}

// YearColumn maps an attribute column to the date (or date period) it
// represents, e.g. {"column": "F_2023", "start_date": "2023-01-01"} for a
// single point in time, or {"column": "F_2020_2023", "start_date":
// "2020-01-01", "end_date": "2023-12-31"} for a period. EndDate is optional;
// when nil the mapping is a single point in time, preserving the original
// (pre-period) behavior.
type YearColumn struct {
	Column    string  `json:"column"`
	StartDate string  `json:"start_date"`
	EndDate   *string `json:"end_date,omitempty"`
}

// DatasetSummary is the lightweight view of a dataset used in list responses.
// It omits the heavy geometry/attribute/JSONB fields.
type DatasetSummary struct {
	ID         uuid.UUID `json:"id"`
	CategoryID uuid.UUID `json:"category_id"`
	// DatasetGroupID links the dataset to its dataset group ("одноимённые"
	// datasets versioned by period). Nullable.
	DatasetGroupID *uuid.UUID `json:"dg_id"`
	// StartDate / EndDate is the period the dataset describes (YYYY-MM-DD).
	// Both nullable; EndDate drives the "freshest in group" rule.
	StartDate   *string   `json:"start_date"`
	EndDate     *string   `json:"end_date"`
	Name        string    `json:"name"`
	Description *string   `json:"description"`
	Unit        *string   `json:"unit"`
	FileType    FileType  `json:"file_type"`
	SizeBytes   int64     `json:"size_bytes"`
	Status      string    `json:"status"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// Dataset is a geo file stored in the object store and grouped under a category.
type Dataset struct {
	ID         uuid.UUID `json:"id"`
	CategoryID uuid.UUID `json:"category_id"`
	// DatasetGroupID links the dataset to its dataset group ("одноимённые"
	// datasets versioned by period). Nullable.
	DatasetGroupID *uuid.UUID `json:"dg_id"`
	// StartDate / EndDate is the period the dataset describes (YYYY-MM-DD).
	// Both nullable; EndDate drives the "freshest in group" rule.
	StartDate   *string `json:"start_date"`
	EndDate     *string `json:"end_date"`
	Name        string  `json:"name"`
	Description *string `json:"description"`
	Unit        *string `json:"unit"`
	Filename    string  `json:"filename"`
	StorageKey  string  `json:"storage_key"`
	// CogStorageKey points to the Cloud-Optimized GeoTIFF for rasters. Nullable.
	CogStorageKey *string  `json:"cog_storage_key"`
	FileType      FileType `json:"file_type"`
	SizeBytes     int64    `json:"size_bytes"`
	ContentType   string   `json:"content_type"`
	// SldStorageKey points to the dataset's SLD (Styled Layer Descriptor) style
	// file, if one has been uploaded. Nullable; applies to every file type.
	SldStorageKey  *string `json:"sld_storage_key"`
	SldFilename    *string `json:"sld_filename"`
	SldContentType *string `json:"sld_content_type"`
	// Properties holds tabular data extracted from the file (e.g. a shapefile's
	// attribute table). Nullable; not exposed in dataset responses.
	Properties json.RawMessage `json:"-"`
	// Meta holds arbitrary user-defined data. Nullable.
	Meta json.RawMessage `json:"meta"`
	// Automated is a user-defined flag.
	Automated bool `json:"automated"`
	// Status is the dataset's lifecycle status (see DatasetStatus* constants).
	Status string `json:"status"`
	// AttributeColumns are the columns detected from the file's attribute table
	// (vector_with_kato only). Nullable until parsed.
	AttributeColumns []AttributeColumn `json:"attribute_columns"`
	// KatoColumn is the user-selected column holding KATO codes. Nullable.
	KatoColumn *string `json:"kato_column"`
	// YearColumns maps attribute columns to dates. Nullable until mapped.
	YearColumns []YearColumn `json:"year_columns"`
	// ParseError holds the failure reason when Status is failed. Nullable.
	ParseError *string `json:"parse_error"`
	// Geometry is the dataset's spatial geometry, serialized as GeoJSON.
	// Nullable; populated from the file's spatial data. Not exposed in dataset
	// responses.
	Geometry json.RawMessage `json:"-"`
	// GeoJSON is the pre-assembled GeoJSON FeatureCollection served by the
	// .geojson endpoint. It is generated and persisted at processing time for
	// vector and vector_with_kato datasets. Nullable; not exposed on the dataset
	// response (served only by the .geojson endpoint).
	GeoJSON json.RawMessage `json:"-"`
	// BBox is the axis-aligned bounding box [minX, minY, maxX, maxY] derived
	// from the geometry. Included in responses only for raster datasets.
	BBox      []float64 `json:"bbox,omitempty"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// DatasetFields is the set of editable metadata columns written by
// UpdateFields. Nil pointers clear the corresponding nullable column.
type DatasetFields struct {
	CategoryID     uuid.UUID
	Name           string
	Description    *string
	Unit           *string
	DatasetGroupID *uuid.UUID
	StartDate      *string
	EndDate        *string
}

// ValidateDatePeriod checks that start/end (YYYY-MM-DD, either may be nil)
// parse and that end is not before start. Returns an ErrValidation-wrapped
// error naming the offending field.
func ValidateDatePeriod(start, end *string) error {
	var from, to time.Time
	var err error
	if start != nil {
		if from, err = time.Parse("2006-01-02", *start); err != nil {
			return fmt.Errorf("%w: start_date %q is not YYYY-MM-DD", ErrValidation, *start)
		}
	}
	if end != nil {
		if to, err = time.Parse("2006-01-02", *end); err != nil {
			return fmt.Errorf("%w: end_date %q is not YYYY-MM-DD", ErrValidation, *end)
		}
	}
	if start != nil && end != nil && to.Before(from) {
		return fmt.Errorf("%w: end_date %q is before start_date %q", ErrValidation, *end, *start)
	}
	return nil
}
