package cli

import (
	"fmt"
	"strings"

	"gis/internal/app"
	"gis/internal/domain"

	"github.com/google/uuid"
	"github.com/spf13/cobra"
)

var (
	reprocessAll  bool
	reprocessType string
)

var reprocessCmd = &cobra.Command{
	Use:   "reprocess [dataset-id]",
	Short: "Re-enqueue the processing job for uploaded datasets",
	Long: "Re-publish the RabbitMQ message that drives an uploaded dataset's\n" +
		"asynchronous processing, selecting the right step from its file type\n" +
		"(vector_with_kato -> parse, vector -> properties, raster -> cog).\n" +
		"Before re-enqueuing, each dataset's stored geometry and GeoJSON are\n" +
		"cleared so a stale (e.g. previously miscomputed) value is never served\n" +
		"while the job is pending.\n\n" +
		"Pass a dataset id to reprocess a single dataset, or --all to reprocess\n" +
		"every dataset. --type restricts --all to one file type\n" +
		"(vector_with_kato, vector, or raster).\n\n" +
		"Examples:\n" +
		"  gis reprocess 06818b2b-1fc5-47d9-a764-db2d4cb3df75\n" +
		"  gis reprocess --all\n" +
		"  gis reprocess --all --type=vector",
	Args: cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		if reprocessAll == (len(args) == 1) {
			return fmt.Errorf("provide exactly one of <dataset-id> or --all")
		}
		if reprocessType != "" && !reprocessAll {
			return fmt.Errorf("--type can only be used together with --all")
		}

		var fileType *domain.FileType
		if reprocessType != "" {
			ft := domain.FileType(strings.TrimSpace(reprocessType))
			if !ft.Valid() {
				return fmt.Errorf("invalid --type %q: must be one of vector_with_kato, vector, raster", reprocessType)
			}
			fileType = &ft
		}

		ctx, cancel := signalContext()
		defer cancel()

		application, err := app.New(ctx)
		if err != nil {
			return err
		}
		defer application.Close()

		if reprocessAll {
			enqueued, failures, err := application.ReprocessAllDatasets(ctx, fileType)
			if err != nil {
				return err
			}
			for id, ferr := range failures {
				application.Log.Error("failed to re-enqueue dataset processing",
					"dataset_id", id, "error", ferr)
			}
			application.Log.Info("re-enqueued all dataset processing",
				"file_type", reprocessType, "enqueued", enqueued, "failed", len(failures))
			if len(failures) > 0 {
				return fmt.Errorf("%d of %d datasets failed to re-enqueue", len(failures), enqueued+len(failures))
			}
			return nil
		}

		id, err := uuid.Parse(args[0])
		if err != nil {
			return fmt.Errorf("invalid dataset id %q: %w", args[0], err)
		}

		dataset, err := application.ReprocessDataset(ctx, id)
		if err != nil {
			return err
		}
		application.Log.Info("re-enqueued dataset processing",
			"dataset_id", dataset.ID, "file_type", dataset.FileType, "status", dataset.Status)
		return nil
	},
}

func init() {
	reprocessCmd.Flags().BoolVar(&reprocessAll, "all", false, "reprocess every dataset")
	reprocessCmd.Flags().StringVar(&reprocessType, "type", "", "restrict --all to one file type (vector_with_kato, vector, raster)")
}
