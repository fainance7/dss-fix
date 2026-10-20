package postgres

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"gis/internal/domain"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// DatasetRepository persists datasets in Postgres.
type DatasetRepository struct {
	pool *pgxpool.Pool
}

// NewDatasetRepository returns a DatasetRepository backed by the given pool.
func NewDatasetRepository(pool *pgxpool.Pool) *DatasetRepository {
	return &DatasetRepository{pool: pool}
}

// datasetColumns lists the dataset columns for SELECT and RETURNING. The
// geometry is exposed as GeoJSON (jsonb) rather than its raw EWKB form, and a
// bounding box array is derived for raster datasets only.
const datasetColumns = `id, category_id, dg_id, to_char(start_date, 'YYYY-MM-DD') AS start_date, to_char(end_date, 'YYYY-MM-DD') AS end_date, name, description, unit, filename, storage_key, cog_storage_key, file_type, size_bytes, content_type, properties, meta, automated, status, attribute_columns, kato_column, year_columns, parse_error, ST_AsGeoJSON(geometry)::jsonb AS geometry,
	CASE WHEN file_type = 'raster' AND geometry IS NOT NULL
	     THEN ARRAY[ST_XMin(geometry), ST_YMin(geometry), ST_XMax(geometry), ST_YMax(geometry)]
	     ELSE NULL END AS bbox,
	geojson, sld_storage_key, sld_filename, sld_content_type,
	created_at, updated_at`

func scanDataset(row pgx.Row) (domain.Dataset, error) {
	var d domain.Dataset
	err := row.Scan(
		&d.ID, &d.CategoryID, &d.DatasetGroupID, &d.StartDate, &d.EndDate, &d.Name, &d.Description, &d.Unit,
		&d.Filename, &d.StorageKey, &d.CogStorageKey, &d.FileType, &d.SizeBytes, &d.ContentType,
		&d.Properties, &d.Meta, &d.Automated, &d.Status,
		&d.AttributeColumns, &d.KatoColumn, &d.YearColumns, &d.ParseError,
		&d.Geometry, &d.BBox, &d.GeoJSON, &d.SldStorageKey, &d.SldFilename, &d.SldContentType,
		&d.CreatedAt, &d.UpdatedAt,
	)
	return d, err
}

// nullableJSON returns nil for empty JSON so the column is stored as SQL NULL
// rather than an empty/invalid value.
func nullableJSON(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	return raw
}

// Create inserts a new dataset and returns the stored row.
func (r *DatasetRepository) Create(ctx context.Context, d domain.Dataset) (domain.Dataset, error) {
	row := r.pool.QueryRow(ctx,
		`INSERT INTO datasets (category_id, name, description, unit, filename, storage_key, file_type, size_bytes, content_type, properties, meta, automated, status, dg_id, start_date, end_date)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::date, $16::date)
		 RETURNING `+datasetColumns,
		d.CategoryID, d.Name, d.Description, d.Unit, d.Filename, d.StorageKey, d.FileType, d.SizeBytes, d.ContentType,
		nullableJSON(d.Properties), nullableJSON(d.Meta), d.Automated, d.Status,
		d.DatasetGroupID, d.StartDate, d.EndDate,
	)
	out, err := scanDataset(row)
	return out, mapError(err)
}

// UpdateFields overwrites a dataset's editable metadata columns. Nil pointers
// clear the nullable columns. Returns domain.ErrNotFound if the dataset does
// not exist, or domain.ErrConflict if the category/group does not exist.
func (r *DatasetRepository) UpdateFields(ctx context.Context, id uuid.UUID, f domain.DatasetFields) (domain.Dataset, error) {
	row := r.pool.QueryRow(ctx,
		`UPDATE datasets
		 SET category_id = $2, name = $3, description = $4, unit = $5,
		     dg_id = $6, start_date = $7::date, end_date = $8::date, updated_at = now()
		 WHERE id = $1
		 RETURNING `+datasetColumns,
		id, f.CategoryID, f.Name, f.Description, f.Unit, f.DatasetGroupID, f.StartDate, f.EndDate,
	)
	out, err := scanDataset(row)
	return out, mapError(err)
}

// MarkParsed stores the detected attribute columns and moves the dataset to
// awaiting_mapping, clearing any previous parse error.
func (r *DatasetRepository) MarkParsed(ctx context.Context, id uuid.UUID, cols []domain.AttributeColumn) error {
	tag, err := r.pool.Exec(ctx,
		`UPDATE datasets
		 SET attribute_columns = $2, status = $3, parse_error = NULL, updated_at = now()
		 WHERE id = $1`,
		id, cols, domain.DatasetStatusAwaitingMapping,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// MarkParseFailed records a parse failure reason and sets the failed status.
func (r *DatasetRepository) MarkParseFailed(ctx context.Context, id uuid.UUID, reason string) error {
	tag, err := r.pool.Exec(ctx,
		`UPDATE datasets SET status = $2, parse_error = $3, updated_at = now() WHERE id = $1`,
		id, domain.DatasetStatusFailed, reason,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SaveMapping stores the KATO column and year mapping, moves the dataset to
// extracting, and returns the updated row.
func (r *DatasetRepository) SaveMapping(ctx context.Context, id uuid.UUID, katoColumn string, years []domain.YearColumn) (domain.Dataset, error) {
	row := r.pool.QueryRow(ctx,
		`UPDATE datasets
		 SET kato_column = $2, year_columns = $3, status = $4, parse_error = NULL, updated_at = now()
		 WHERE id = $1
		 RETURNING `+datasetColumns,
		id, katoColumn, years, domain.DatasetStatusExtracting,
	)
	out, err := scanDataset(row)
	return out, mapError(err)
}

// MarkConverted stores the COG storage key, optionally sets the footprint
// geometry (GeoJSON in EPSG:4326; nil keeps the existing geometry), and marks
// the dataset ready.
func (r *DatasetRepository) MarkConverted(ctx context.Context, id uuid.UUID, cogKey string, footprint []byte) error {
	var fp any // nil -> SQL NULL -> CASE keeps existing geometry
	if len(footprint) > 0 {
		fp = string(footprint)
	}
	tag, err := r.pool.Exec(ctx,
		`UPDATE datasets
		 SET cog_storage_key = $2,
		     geometry = CASE WHEN $3::text IS NULL THEN geometry
		                     ELSE ST_SetSRID(ST_GeomFromGeoJSON($3), 4326) END,
		     status = $4, parse_error = NULL, updated_at = now()
		 WHERE id = $1`,
		id, cogKey, fp, domain.DatasetStatusReady,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SetProperties stores the extracted attribute table (nil -> NULL) and the
// dissolved feature geometry (GeoJSON in EPSG:4326; nil keeps the existing
// geometry), then marks the dataset ready. The geometry is reduced to the union
// of all features via ST_UnaryUnion.
func (r *DatasetRepository) SetProperties(ctx context.Context, id uuid.UUID, properties, geometry []byte) error {
	var geom any // nil -> SQL NULL -> CASE keeps existing geometry
	if len(geometry) > 0 {
		geom = string(geometry)
	}
	tag, err := r.pool.Exec(ctx,
		`UPDATE datasets
		 SET properties = $2,
		     geometry = CASE WHEN $3::text IS NULL THEN geometry
		                     ELSE ST_UnaryUnion(ST_SetSRID(ST_GeomFromGeoJSON($3), 4326)) END,
		     status = $4, parse_error = NULL, updated_at = now()
		 WHERE id = $1`,
		id, nullableJSON(json.RawMessage(properties)), geom, domain.DatasetStatusReady,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// MarkReady sets the dataset status to ready, stores the dissolved feature
// geometry (GeoJSON in EPSG:4326; nil keeps the existing geometry, reduced to
// the union of all features via ST_UnaryUnion), and clears any error.
func (r *DatasetRepository) MarkReady(ctx context.Context, id uuid.UUID, geometry []byte) error {
	var geom any // nil -> SQL NULL -> CASE keeps existing geometry
	if len(geometry) > 0 {
		geom = string(geometry)
	}
	tag, err := r.pool.Exec(ctx,
		`UPDATE datasets
		 SET status = $2,
		     geometry = CASE WHEN $3::text IS NULL THEN geometry
		                     ELSE ST_UnaryUnion(ST_SetSRID(ST_GeomFromGeoJSON($3), 4326)) END,
		     parse_error = NULL, updated_at = now()
		 WHERE id = $1`,
		id, domain.DatasetStatusReady, geom,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SetGeoJSON stores the pre-assembled GeoJSON FeatureCollection for a dataset
// (nil -> NULL). It is written at processing time and served verbatim by the
// .geojson endpoint.
func (r *DatasetRepository) SetGeoJSON(ctx context.Context, id uuid.UUID, geojson []byte) error {
	tag, err := r.pool.Exec(ctx,
		`UPDATE datasets SET geojson = $2, updated_at = now() WHERE id = $1`,
		id, nullableJSON(json.RawMessage(geojson)),
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SetSLD stores the dataset's SLD (Styled Layer Descriptor) style file
// metadata, replacing any previous one, and returns the updated row.
func (r *DatasetRepository) SetSLD(ctx context.Context, id uuid.UUID, key, filename, contentType string) (domain.Dataset, error) {
	row := r.pool.QueryRow(ctx,
		`UPDATE datasets
		 SET sld_storage_key = $2, sld_filename = $3, sld_content_type = $4, updated_at = now()
		 WHERE id = $1
		 RETURNING `+datasetColumns,
		id, key, filename, contentType,
	)
	out, err := scanDataset(row)
	return out, mapError(err)
}

// ClearGeometryAndGeoJSON nulls out a dataset's stored geometry and persisted
// GeoJSON. Used before re-enqueuing a dataset's processing job so a stale
// (possibly incorrect) geometry/GeoJSON is never served while the job is
// pending; the async worker repopulates both once it completes.
func (r *DatasetRepository) ClearGeometryAndGeoJSON(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx,
		`UPDATE datasets SET geometry = NULL, geojson = NULL, updated_at = now() WHERE id = $1`,
		id,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// ReplaceObservations atomically replaces all observations for a dataset.
func (r *DatasetRepository) ReplaceObservations(ctx context.Context, datasetID uuid.UUID, obs []domain.Observation) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return mapError(err)
	}
	defer tx.Rollback(ctx)

	if _, err := tx.Exec(ctx, `DELETE FROM dataset_observations WHERE dataset_id = $1`, datasetID); err != nil {
		return mapError(err)
	}

	if len(obs) > 0 {
		rows := make([][]any, len(obs))
		for i, o := range obs {
			d, err := time.Parse("2006-01-02", o.StartDate)
			if err != nil {
				return fmt.Errorf("invalid observation start_date %q: %w", o.StartDate, err)
			}
			var endDate any
			if o.EndDate != nil {
				dt, err := time.Parse("2006-01-02", *o.EndDate)
				if err != nil {
					return fmt.Errorf("invalid observation end_date %q: %w", *o.EndDate, err)
				}
				endDate = dt
			}
			rows[i] = []any{datasetID, o.KatoCode, d, endDate, o.Value, o.ValueText}
		}
		_, err := tx.CopyFrom(ctx,
			pgx.Identifier{"dataset_observations"},
			[]string{"dataset_id", "kato_code", "start_date", "end_date", "value", "value_text"},
			pgx.CopyFromRows(rows),
		)
		if err != nil {
			return mapError(err)
		}
	}

	return mapError(tx.Commit(ctx))
}

const observationColumns = `id, dataset_id, kato_code, to_char(start_date, 'YYYY-MM-DD') AS start_date, to_char(end_date, 'YYYY-MM-DD') AS end_date, value, value_text`

func scanObservation(row pgx.Row) (domain.Observation, error) {
	var o domain.Observation
	err := row.Scan(&o.ID, &o.DatasetID, &o.KatoCode, &o.StartDate, &o.EndDate, &o.Value, &o.ValueText)
	return o, err
}

// ListObservations returns a page of observations for a dataset, optionally
// filtered by KATO code, ordered by (kato_code, start_date).
func (r *DatasetRepository) ListObservations(ctx context.Context, datasetID uuid.UUID, katoCode *string, limit, offset int) ([]domain.Observation, error) {
	base := `SELECT ` + observationColumns + ` FROM dataset_observations WHERE dataset_id = $1`

	var (
		rows pgx.Rows
		err  error
	)
	if katoCode != nil {
		rows, err = r.pool.Query(ctx,
			base+` AND kato_code = $2 ORDER BY kato_code, start_date LIMIT $3 OFFSET $4`,
			datasetID, *katoCode, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx,
			base+` ORDER BY kato_code, start_date LIMIT $2 OFFSET $3`,
			datasetID, limit, offset)
	}
	if err != nil {
		return nil, mapError(err)
	}
	defer rows.Close()

	out := make([]domain.Observation, 0)
	for rows.Next() {
		o, err := scanObservation(rows)
		if err != nil {
			return nil, mapError(err)
		}
		out = append(out, o)
	}
	return out, mapError(rows.Err())
}

// CountObservations counts a dataset's observations, optionally filtered by KATO.
func (r *DatasetRepository) CountObservations(ctx context.Context, datasetID uuid.UUID, katoCode *string) (int, error) {
	var n int
	var err error
	if katoCode != nil {
		err = r.pool.QueryRow(ctx,
			`SELECT count(*) FROM dataset_observations WHERE dataset_id = $1 AND kato_code = $2`,
			datasetID, *katoCode).Scan(&n)
	} else {
		err = r.pool.QueryRow(ctx,
			`SELECT count(*) FROM dataset_observations WHERE dataset_id = $1`, datasetID).Scan(&n)
	}
	return n, mapError(err)
}

// GetByID returns the dataset with the given id, or domain.ErrNotFound.
func (r *DatasetRepository) GetByID(ctx context.Context, id uuid.UUID) (domain.Dataset, error) {
	row := r.pool.QueryRow(ctx,
		`SELECT `+datasetColumns+` FROM datasets WHERE id = $1`, id)
	out, err := scanDataset(row)
	return out, mapError(err)
}

// LatestInGroup returns the freshest dataset of a group: greatest end_date
// (NULL end_date sorts last), ties broken by created_at DESC. status and
// fileType optionally restrict candidates. Returns domain.ErrNotFound when no
// dataset matches.
func (r *DatasetRepository) LatestInGroup(ctx context.Context, groupID uuid.UUID, status *string, fileType *domain.FileType) (domain.Dataset, error) {
	where, args := datasetFilterClause(domain.DatasetFilter{DatasetGroupID: &groupID, Status: status, FileType: fileType}, nil)
	row := r.pool.QueryRow(ctx,
		`SELECT `+datasetColumns+` FROM datasets`+where+
			` ORDER BY end_date DESC NULLS LAST, created_at DESC LIMIT 1`, args...)
	out, err := scanDataset(row)
	return out, mapError(err)
}

const datasetSummaryColumns = `id, category_id, dg_id, to_char(start_date, 'YYYY-MM-DD') AS start_date, to_char(end_date, 'YYYY-MM-DD') AS end_date, name, description, unit, file_type, size_bytes, status, created_at, updated_at`

func scanDatasetSummary(row pgx.Row) (domain.DatasetSummary, error) {
	var d domain.DatasetSummary
	err := row.Scan(
		&d.ID, &d.CategoryID, &d.DatasetGroupID, &d.StartDate, &d.EndDate, &d.Name, &d.Description, &d.Unit,
		&d.FileType, &d.SizeBytes, &d.Status, &d.CreatedAt, &d.UpdatedAt,
	)
	return d, err
}

// datasetFilterClause builds the WHERE fragment for the given filter, appending
// its values to args. It returns a fragment beginning with " WHERE " when any
// condition applies, or the empty string when the filter is empty.
func datasetFilterClause(f domain.DatasetFilter, args []any) (string, []any) {
	var conds []string
	if f.CategoryID != nil {
		args = append(args, *f.CategoryID)
		conds = append(conds, fmt.Sprintf("category_id = $%d", len(args)))
	}
	if f.FileType != nil {
		args = append(args, *f.FileType)
		conds = append(conds, fmt.Sprintf("file_type = $%d", len(args)))
	}
	if f.Automated != nil {
		args = append(args, *f.Automated)
		conds = append(conds, fmt.Sprintf("automated = $%d", len(args)))
	}
	if f.Status != nil {
		args = append(args, *f.Status)
		conds = append(conds, fmt.Sprintf("status = $%d", len(args)))
	}
	if f.DatasetGroupID != nil {
		args = append(args, *f.DatasetGroupID)
		conds = append(conds, fmt.Sprintf("dg_id = $%d", len(args)))
	}
	for _, b := range []struct {
		col, op string
		val     *string
	}{
		{"start_date", ">=", f.StartDateFrom}, {"start_date", "<=", f.StartDateTo},
		{"end_date", ">=", f.EndDateFrom}, {"end_date", "<=", f.EndDateTo},
	} {
		if b.val != nil {
			args = append(args, *b.val)
			conds = append(conds, fmt.Sprintf("%s %s $%d::date", b.col, b.op, len(args)))
		}
	}
	if len(conds) == 0 {
		return "", args
	}
	return " WHERE " + strings.Join(conds, " AND "), args
}

// ListSummaries returns a page of dataset summaries ordered by creation time
// (newest first), constrained by the given filter.
func (r *DatasetRepository) ListSummaries(ctx context.Context, filter domain.DatasetFilter, limit, offset int) ([]domain.DatasetSummary, error) {
	where, args := datasetFilterClause(filter, nil)
	args = append(args, limit, offset)
	query := fmt.Sprintf(`SELECT %s FROM datasets%s ORDER BY created_at DESC LIMIT $%d OFFSET $%d`,
		datasetSummaryColumns, where, len(args)-1, len(args))

	rows, err := r.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, mapError(err)
	}
	defer rows.Close()

	summaries := make([]domain.DatasetSummary, 0)
	for rows.Next() {
		d, err := scanDatasetSummary(rows)
		if err != nil {
			return nil, mapError(err)
		}
		summaries = append(summaries, d)
	}
	return summaries, mapError(rows.Err())
}

// Count returns the number of datasets matching the given filter.
func (r *DatasetRepository) Count(ctx context.Context, filter domain.DatasetFilter) (int, error) {
	where, args := datasetFilterClause(filter, nil)
	var n int
	err := r.pool.QueryRow(ctx, `SELECT count(*) FROM datasets`+where, args...).Scan(&n)
	return n, mapError(err)
}

// Delete removes a dataset. Returns domain.ErrNotFound if it does not exist.
func (r *DatasetRepository) Delete(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `DELETE FROM datasets WHERE id = $1`, id)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}
