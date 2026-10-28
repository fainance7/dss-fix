package cli

import (
	"context"
	"database/sql"
	"fmt"
	"os"

	"gis/internal/config"
	"gis/migrations"

	"github.com/jackc/pgx/v5"
	_ "github.com/jackc/pgx/v5/stdlib" // registers the "pgx" database/sql driver
	"github.com/pressly/goose/v3"
	"github.com/spf13/cobra"
)

var migrateCmd = &cobra.Command{
	Use:   "migrate <command> [args]",
	Short: "Run database migrations (up, down, status, reset, redo, fresh, version)",
	Long: "Run goose migrations from the embedded migration files.\n\n" +
		"In addition to the standard goose commands, `fresh` drops every object in\n" +
		"the public schema and re-applies all migrations from scratch.\n\n" +
		"Examples:\n" +
		"  gis migrate up\n" +
		"  gis migrate down\n" +
		"  gis migrate status\n" +
		"  gis migrate fresh\n" +
		"  gis migrate up-to 00002",
	Args: cobra.MinimumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		cfg, err := config.Load()
		if err != nil {
			return err
		}

		db, err := sql.Open("pgx", cfg.DB.URL)
		if err != nil {
			return fmt.Errorf("open db: %w", err)
		}
		defer db.Close()

		goose.SetBaseFS(migrations.FS)
		if err := goose.SetDialect("postgres"); err != nil {
			return fmt.Errorf("set dialect: %w", err)
		}

		command := args[0]
		if command == "fresh" {
			// Destructive: drops the whole schema. Requires an explicit opt-in so a
			// mistyped command or wrong DB_URL cannot wipe production data.
			if os.Getenv("GIS_ALLOW_MIGRATE_FRESH") != "true" {
				return fmt.Errorf("migrate fresh drops schema %q: set GIS_ALLOW_MIGRATE_FRESH=true to confirm (never in production)", cfg.DB.Schema)
			}
			return migrateFresh(cmd.Context(), db, cfg.DB.Schema)
		}
		return goose.RunContext(cmd.Context(), command, db, ".", args[1:]...)
	},
}

// migrateFresh drops the configured schema (every table, type, and the goose
// version table) and re-applies all migrations. This is a destructive
// development convenience, equivalent to "drop everything and rerun".
func migrateFresh(ctx context.Context, db *sql.DB, schema string) error {
	// H-4: identifiers cannot be bound as SQL parameters, so the schema name is
	// quoted with pgx.Identifier.Sanitize() — it wraps the value in double
	// quotes and escapes any embedded quotes (PG identifier-quoting rules),
	// which neutralises injection and handles mixed-case/special identifiers.
	// We build the statement by concatenation, not fmt.Sprintf, so the
	// sanitised identifier is the only value that ever reaches the SQL string.
	quoted := pgx.Identifier{schema}.Sanitize()
	stmt := "DROP SCHEMA IF EXISTS " + quoted + " CASCADE; CREATE SCHEMA " + quoted + ";"
	if _, err := db.ExecContext(ctx, stmt); err != nil {
		return fmt.Errorf("reset schema %q: %w", schema, err)
	}
	if err := goose.UpContext(ctx, db, "."); err != nil {
		return fmt.Errorf("re-apply migrations: %w", err)
	}
	return nil
}
