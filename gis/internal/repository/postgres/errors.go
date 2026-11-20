package postgres

import (
	"errors"
	"fmt"

	"gis/internal/domain"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// mapError translates pgx/Postgres errors into domain sentinel errors so the
// service and transport layers stay decoupled from the driver.
func mapError(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.ErrNotFound
	}

	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		switch pgErr.Code {
		case "23503": // foreign_key_violation
			return fmt.Errorf("%w: %s", domain.ErrConflict, pgErr.ConstraintName)
		case "23505": // unique_violation
			return fmt.Errorf("%w: %s", domain.ErrConflict, pgErr.ConstraintName)
		case "23514": // check_violation
			return fmt.Errorf("%w: %s", domain.ErrValidation, pgErr.ConstraintName)
		}
	}
	return err
}
