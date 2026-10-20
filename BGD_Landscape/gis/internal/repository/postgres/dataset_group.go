package postgres

import (
	"context"
	"fmt"

	"gis/internal/domain"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// DatasetGroupRepository persists dataset groups in Postgres.
type DatasetGroupRepository struct{ pool *pgxpool.Pool }

// NewDatasetGroupRepository returns a DatasetGroupRepository backed by pool.
func NewDatasetGroupRepository(pool *pgxpool.Pool) *DatasetGroupRepository {
	return &DatasetGroupRepository{pool: pool}
}

const datasetGroupColumns = `id, code, name, description, sld_storage_key, sld_filename, sld_content_type, created_at, updated_at`

func scanDatasetGroup(row pgx.Row) (domain.DatasetGroup, error) {
	var g domain.DatasetGroup
	err := row.Scan(&g.ID, &g.Code, &g.Name, &g.Description, &g.SldStorageKey, &g.SldFilename, &g.SldContentType, &g.CreatedAt, &g.UpdatedAt)
	return g, err
}

// Create inserts a new dataset group and returns the stored row.
func (r *DatasetGroupRepository) Create(ctx context.Context, g domain.DatasetGroup) (domain.DatasetGroup, error) {
	row := r.pool.QueryRow(ctx,
		`INSERT INTO dataset_groups (code, name, description) VALUES ($1, $2, $3) RETURNING `+datasetGroupColumns,
		g.Code, g.Name, g.Description)
	out, err := scanDatasetGroup(row)
	return out, mapError(err)
}

// GetByID returns the group with the given id.
func (r *DatasetGroupRepository) GetByID(ctx context.Context, id uuid.UUID) (domain.DatasetGroup, error) {
	out, err := scanDatasetGroup(r.pool.QueryRow(ctx, `SELECT `+datasetGroupColumns+` FROM dataset_groups WHERE id = $1`, id))
	return out, mapError(err)
}

// GetByCode returns the group with the given (unique) code.
func (r *DatasetGroupRepository) GetByCode(ctx context.Context, code string) (domain.DatasetGroup, error) {
	out, err := scanDatasetGroup(r.pool.QueryRow(ctx, `SELECT `+datasetGroupColumns+` FROM dataset_groups WHERE code = $1`, code))
	return out, mapError(err)
}

// List returns a page of groups ordered by code; code (exact) is optional.
func (r *DatasetGroupRepository) List(ctx context.Context, code *string, limit, offset int) ([]domain.DatasetGroup, error) {
	where, args := groupWhere(code)
	args = append(args, limit, offset)
	rows, err := r.pool.Query(ctx, fmt.Sprintf(`SELECT %s FROM dataset_groups%s ORDER BY code LIMIT $%d OFFSET $%d`,
		datasetGroupColumns, where, len(args)-1, len(args)), args...)
	if err != nil {
		return nil, mapError(err)
	}
	defer rows.Close()
	out := make([]domain.DatasetGroup, 0)
	for rows.Next() {
		g, err := scanDatasetGroup(rows)
		if err != nil {
			return nil, mapError(err)
		}
		out = append(out, g)
	}
	return out, mapError(rows.Err())
}

// Count returns how many groups match the optional code filter.
func (r *DatasetGroupRepository) Count(ctx context.Context, code *string) (int, error) {
	where, args := groupWhere(code)
	var n int
	err := r.pool.QueryRow(ctx, `SELECT count(*) FROM dataset_groups`+where, args...).Scan(&n)
	return n, mapError(err)
}

// groupWhere builds the optional exact-code WHERE clause and its arguments.
func groupWhere(code *string) (string, []any) {
	if code == nil {
		return "", nil
	}
	return " WHERE code = $1", []any{*code}
}

// Update replaces a group's editable fields and returns the stored row.
func (r *DatasetGroupRepository) Update(ctx context.Context, g domain.DatasetGroup) (domain.DatasetGroup, error) {
	row := r.pool.QueryRow(ctx,
		`UPDATE dataset_groups SET code = $2, name = $3, description = $4, updated_at = now() WHERE id = $1 RETURNING `+datasetGroupColumns,
		g.ID, g.Code, g.Name, g.Description)
	out, err := scanDatasetGroup(row)
	return out, mapError(err)
}

// Delete removes a group. Returns domain.ErrNotFound if absent and
// domain.ErrConflict (via the FK) if datasets still reference it.
func (r *DatasetGroupRepository) Delete(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `DELETE FROM dataset_groups WHERE id = $1`, id)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SetSLD points the group at a stored SLD style object.
func (r *DatasetGroupRepository) SetSLD(ctx context.Context, id uuid.UUID, key, filename, contentType string) (domain.DatasetGroup, error) {
	row := r.pool.QueryRow(ctx,
		`UPDATE dataset_groups SET sld_storage_key = $2, sld_filename = $3, sld_content_type = $4, updated_at = now() WHERE id = $1 RETURNING `+datasetGroupColumns,
		id, key, filename, contentType)
	out, err := scanDatasetGroup(row)
	return out, mapError(err)
}
