package cli

import (
	"fmt"

	"github.com/spf13/cobra"

	"gis/internal/config"
	"gis/internal/repository/postgres"
)

// auditCmd verifies the append-only security journal (Этап 7, ТЗ п. 4.8.3).
var auditCmd = &cobra.Command{
	Use:   "audit verify",
	Short: "Verify the hash chain of the security journal",
	Long: "Recomputes the SHA-256 chain of the security_journal table independently of\n" +
		"the database triggers and reports the first record that was altered, if any.",
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		if args[0] != "verify" {
			return fmt.Errorf("unknown audit command %q (use: audit verify)", args[0])
		}
		ctx, cancel := signalContext()
		defer cancel()
		cfg, err := config.Load()
		if err != nil {
			return err
		}
		pool, err := postgres.Connect(ctx, cfg.DB.URL)
		if err != nil {
			return err
		}
		defer pool.Close()
		checked, broken, err := postgres.NewSecurityJournal(pool).Verify(ctx)
		if err != nil {
			return err
		}
		if broken != 0 {
			return fmt.Errorf("security journal chain BROKEN at record %d (%d records verified before it)", broken, checked)
		}
		fmt.Fprintf(cmd.OutOrStdout(), "security journal OK: %d records, hash chain intact\n", checked)
		return nil
	},
}
