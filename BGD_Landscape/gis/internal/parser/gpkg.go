package parser

import (
	"database/sql"
	"fmt"
	"os"
	"strings"

	"gis/internal/domain"

	_ "modernc.org/sqlite" // pure-Go SQLite driver, registered as "sqlite"
)

// withGPKG writes the GeoPackage bytes to a temp file (SQLite needs a path),
// opens it, and runs fn with the feature table name and its attribute columns
// (geometry column excluded).
func withGPKG(data []byte, fn func(db *sql.DB, table string, names []string) error) error {
	tmp, err := os.CreateTemp("", "gis-*.gpkg")
	if err != nil {
		return fmt.Errorf("temp file: %w", err)
	}
	defer os.Remove(tmp.Name())

	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return fmt.Errorf("write temp gpkg: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return err
	}

	db, err := sql.Open("sqlite", tmp.Name())
	if err != nil {
		return fmt.Errorf("open gpkg: %w", err)
	}
	defer db.Close()

	var table string
	if err := db.QueryRow(
		`SELECT table_name FROM gpkg_contents WHERE data_type = 'features' ORDER BY table_name LIMIT 1`,
	).Scan(&table); err != nil {
		return fmt.Errorf("find feature table: %w", err)
	}

	var geomColumn string
	_ = db.QueryRow(
		`SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?`, table,
	).Scan(&geomColumn)

	rows, err := db.Query(fmt.Sprintf("PRAGMA table_info(%s)", quoteIdent(table)))
	if err != nil {
		return fmt.Errorf("read columns: %w", err)
	}
	defer rows.Close()

	var names []string
	for rows.Next() {
		var (
			cid, notnull, pk int
			name, ctype      string
			dflt             sql.NullString
		)
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return err
		}
		if name == geomColumn {
			continue
		}
		names = append(names, name)
	}
	if err := rows.Err(); err != nil {
		return err
	}
	if len(names) == 0 {
		return ErrNoColumns
	}

	return fn(db, table, names)
}

// gpkgColumns reads the feature table's attribute columns, with samples.
func gpkgColumns(data []byte) ([]domain.AttributeColumn, error) {
	var cols []domain.AttributeColumn
	err := withGPKG(data, func(db *sql.DB, table string, names []string) error {
		samples := gpkgScan(db, table, names, sampleRows)
		cols = make([]domain.AttributeColumn, len(names))
		for i, n := range names {
			col := domain.AttributeColumn{Name: n}
			for _, row := range samples {
				col.Samples = append(col.Samples, row[n])
			}
			cols[i] = col
		}
		return nil
	})
	return cols, err
}

// gpkgRows reads every feature row as a name->value map.
func gpkgRows(data []byte) ([]map[string]string, error) {
	var out []map[string]string
	err := withGPKG(data, func(db *sql.DB, table string, names []string) error {
		out = gpkgScan(db, table, names, -1)
		return nil
	})
	return out, err
}

// gpkgScan returns up to limit rows (limit < 0 means all) as name->value maps.
func gpkgScan(db *sql.DB, table string, names []string, limit int) []map[string]string {
	quoted := make([]string, len(names))
	for i, n := range names {
		quoted[i] = quoteIdent(n)
	}
	query := fmt.Sprintf("SELECT %s FROM %s", strings.Join(quoted, ", "), quoteIdent(table))
	if limit >= 0 {
		query += fmt.Sprintf(" LIMIT %d", limit)
	}

	rows, err := db.Query(query)
	if err != nil {
		return nil
	}
	defer rows.Close()

	var out []map[string]string
	for rows.Next() {
		cells := make([]sql.NullString, len(names))
		ptrs := make([]any, len(names))
		for i := range cells {
			ptrs[i] = &cells[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return out
		}
		row := make(map[string]string, len(names))
		for i, n := range names {
			if cells[i].Valid {
				row[n] = strings.TrimSpace(cells[i].String)
			} else {
				row[n] = ""
			}
		}
		out = append(out, row)
	}
	return out
}

// quoteIdent quotes an SQLite identifier.
func quoteIdent(s string) string {
	return `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
}
