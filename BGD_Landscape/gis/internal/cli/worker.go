package cli

import (
	"context"
	"errors"

	"gis/internal/app"
	"gis/internal/messaging/rabbitmq"

	"github.com/spf13/cobra"
	"golang.org/x/sync/errgroup"
)

var publishExample bool

var workerCmd = &cobra.Command{
	Use:   "worker",
	Short: "Run the RabbitMQ consumers (dataset processing, landscape runs + example)",
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx, cancel := signalContext()
		defer cancel()

		application, err := app.New(ctx)
		if err != nil {
			return err
		}
		defer application.Close()

		if publishExample {
			if err := rabbitmq.PublishExample(ctx, application.Publisher()); err != nil {
				return err
			}
			application.Log.Info("published example message")
		}

		// Run every consumer concurrently; cancel all if one fails.
		g, gctx := errgroup.WithContext(ctx)
		for _, c := range application.Consumers() {
			c := c
			g.Go(func() error { return c.Run(gctx) })
		}

		// Graceful shutdown (context cancelled) is not an error.
		if err := g.Wait(); err != nil && !errors.Is(err, context.Canceled) {
			return err
		}
		return nil
	},
}

func init() {
	workerCmd.Flags().BoolVar(&publishExample, "publish-example", false, "publish one example message before consuming")
}
