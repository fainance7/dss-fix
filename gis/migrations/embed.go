// Package migrations embeds the SQL migration files so they can be applied from
// the single application binary (via the `gis migrate` subcommand).
package migrations

import "embed"

// FS holds the embedded goose migration files.
//
//go:embed *.sql
var FS embed.FS
