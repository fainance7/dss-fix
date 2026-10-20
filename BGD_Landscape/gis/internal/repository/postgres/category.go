package postgres

import (
	"context"

	"gis/internal/domain"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// CategoryRepository persists categories in Postgres.
type CategoryRepository struct {
	pool *pgxpool.Pool
}

// NewCategoryRepository returns a CategoryRepository backed by the given pool.
func NewCategoryRepository(pool *pgxpool.Pool) *CategoryRepository {
	return &CategoryRepository{pool: pool}
}

const categoryColumns = `id, parent_id, code, name, description, created_at, updated_at`

func scanCategory(row pgx.Row) (domain.Category, error) {
	var c domain.Category
	err := row.Scan(&c.ID, &c.ParentID, &c.Code, &c.Name, &c.Description, &c.CreatedAt, &c.UpdatedAt)
	return c, err
}

// Create inserts a new category and returns the stored row.
func (r *CategoryRepository) Create(ctx context.Context, c domain.Category) (domain.Category, error) {
	row := r.pool.QueryRow(ctx,
		`INSERT INTO categories (parent_id, code, name, description)
		 VALUES ($1, $2, $3, $4)
		 RETURNING `+categoryColumns,
		c.ParentID, c.Code, c.Name, c.Description,
	)
	out, err := scanCategory(row)
	return out, mapError(err)
}

// GetByID returns the category with the given id, or domain.ErrNotFound.
func (r *CategoryRepository) GetByID(ctx context.Context, id uuid.UUID) (domain.Category, error) {
	row := r.pool.QueryRow(ctx,
		`SELECT `+categoryColumns+` FROM categories WHERE id = $1`, id)
	out, err := scanCategory(row)
	return out, mapError(err)
}

// GetByCode returns the category with the given code, or domain.ErrNotFound.
func (r *CategoryRepository) GetByCode(ctx context.Context, code string) (domain.Category, error) {
	row := r.pool.QueryRow(ctx,
		`SELECT `+categoryColumns+` FROM categories WHERE code = $1`, code)
	out, err := scanCategory(row)
	return out, mapError(err)
}

// List returns categories ordered by name. When parentID is non-nil it filters
// to that parent's direct children; otherwise it returns all categories.
func (r *CategoryRepository) List(ctx context.Context, parentID *uuid.UUID) ([]domain.Category, error) {
	var (
		rows pgx.Rows
		err  error
	)
	if parentID != nil {
		rows, err = r.pool.Query(ctx,
			`SELECT `+categoryColumns+` FROM categories WHERE parent_id = $1 ORDER BY name`, *parentID)
	} else {
		rows, err = r.pool.Query(ctx,
			`SELECT `+categoryColumns+` FROM categories ORDER BY name`)
	}
	if err != nil {
		return nil, mapError(err)
	}
	defer rows.Close()

	categories := make([]domain.Category, 0)
	for rows.Next() {
		c, err := scanCategory(rows)
		if err != nil {
			return nil, mapError(err)
		}
		categories = append(categories, c)
	}
	return categories, mapError(rows.Err())
}

// Update modifies a category's parent, name, and description.
func (r *CategoryRepository) Update(ctx context.Context, c domain.Category) (domain.Category, error) {
	row := r.pool.QueryRow(ctx,
		`UPDATE categories
		 SET parent_id = $2, code = $3, name = $4, description = $5, updated_at = now()
		 WHERE id = $1
		 RETURNING `+categoryColumns,
		c.ID, c.ParentID, c.Code, c.Name, c.Description,
	)
	out, err := scanCategory(row)
	return out, mapError(err)
}

// Delete removes a category. Returns domain.ErrNotFound if it does not exist.
func (r *CategoryRepository) Delete(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `DELETE FROM categories WHERE id = $1`, id)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}
