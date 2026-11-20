// Package cli defines the cobra command tree for the gis binary: serve, worker,
// and migrate.
package cli

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:           "gis",
	Short:         "GIS application server, worker, and migration tool",
	SilenceUsage:  true,
	SilenceErrors: true,
}

// Execute runs the root command, exiting non-zero on error.
func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func init() {
	rootCmd.AddCommand(serveCmd, workerCmd, migrateCmd, reprocessCmd, importDistrictsCmd, auditCmd)
}

// signalContext returns a context cancelled on SIGINT or SIGTERM.
func signalContext() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
}
