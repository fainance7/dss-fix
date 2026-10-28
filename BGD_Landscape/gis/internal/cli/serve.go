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

		if err := application.Cfg.HTTP.ValidateAuth(); err != nil {
			return err
		}
		if application.Cfg.HTTP.AuthDisabled {
			application.Log.Warn("API key auth is DISABLED (API_AUTH_DISABLED=true) — local development only")
		}

		return application.Server().Run(ctx)
	},
}
