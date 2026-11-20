package cli

import (
	"gis/internal/app"

	"github.com/spf13/cobra"
)

var serveCmd = &cobra.Command{
	Use:   "serve",
	Short: "Run the HTTP server",
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx, cancel := signalContext()
		defer cancel()

		application, err := app.New(ctx)
		if err != nil {
			return err
		}
		defer application.Close()

		return application.Server().Run(ctx)
	},
}
