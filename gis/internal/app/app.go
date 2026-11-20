// Package app is the composition root: it builds and wires every dependency
// (config, logger, database, object store, messaging, repositories, services,
// and HTTP handlers) and exposes them to the CLI commands.
package app

import (
	"context"
	"fmt"
	"log/slog"
	stdhttp "net/http"

	"gis/api"
	"gis/internal/config"
	"gis/internal/domain"
	"gis/internal/messaging/rabbitmq"
	"gis/internal/parser"
	"gis/internal/platform/logger"
	"gis/internal/raster"
	"gis/internal/repository/postgres"
	"gis/internal/service"
	"gis/internal/storage/s3"
	transporthttp "gis/internal/transport/http"

	"github.com/go-playground/validator/v10"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

// App holds the wired application dependencies.
type App struct {
	Cfg *config.Config
	Log *slog.Logger

	pool   *pgxpool.Pool
	store  *s3.Client
	rabbit *rabbitmq.Connection

	publisher  *rabbitmq.Publisher
	categories *service.CategoryService
	datasets   *service.DatasetService
	groups     *service.DatasetGroupService
	landscape  *service.LandscapeService
	eventRepo  *postgres.EventRepository
}

// New builds the application from configuration. The caller must call Close.
func New(ctx context.Context) (*App, error) {
	cfg, err := config.Load()
	if err != nil {
		return nil, err
	}
	log := logger.New("json", "info")

	pool, err := postgres.Connect(ctx, cfg.DB.URL)
	if err != nil {
		return nil, fmt.Errorf("connect postgres: %w", err)
	}

	store, err := s3.New(ctx, cfg.S3)
	if err != nil {
		pool.Close()
		return nil, fmt.Errorf("connect s3: %w", err)
	}

	rabbit, err := rabbitmq.Connect(cfg.RabbitMQ)
	if err != nil {
		pool.Close()
		return nil, fmt.Errorf("connect rabbitmq: %w", err)
	}

	categoryRepo := postgres.NewCategoryRepository(pool)
	datasetRepo := postgres.NewDatasetRepository(pool)
	groupRepo := postgres.NewDatasetGroupRepository(pool)
	eventRepo := postgres.NewEventRepository(pool)

	lpRepo := postgres.NewLandscapeRepository(pool)

	publisher := rabbitmq.NewPublisher(rabbit)
	jobPublisher := rabbitmq.NewDatasetJobPublisher(publisher)
	lpJobs := rabbitmq.NewLandscapeJobPublisher(publisher)

	datasetsSvc := service.NewDatasetService(datasetRepo, store, categoryRepo, groupRepo, jobPublisher, parser.Columns, parser.Rows, raster.NewGDALConverter())

	return &App{
		Cfg:        cfg,
		Log:        log,
		pool:       pool,
		store:      store,
		rabbit:     rabbit,
		publisher:  publisher,
		categories: service.NewCategoryService(categoryRepo),
		datasets:   datasetsSvc,
		groups:     service.NewDatasetGroupService(groupRepo, datasetRepo, store),
		// A nil ComputeFunc selects landscape.ComputeWithWorkers, so each node
		// gets the per-node share of the configured worker budget.
		landscape: service.NewLandscapeService(
			lpRepo, datasetRepo, groupRepo, categoryRepo, datasetsSvc, store,
			lpJobs, raster.NewGodalIO(), nil, log,
			service.LandscapeOptions{
				MaxWorkers:         cfg.Landscape.MaxWorkers,
				MaxConcurrentNodes: cfg.Landscape.MaxConcurrentNodes,
			},
		),
		eventRepo: eventRepo,
	}, nil
}

// Handler builds the HTTP handler with all routes and readiness checks wired.
func (a *App) Handler() stdhttp.Handler {
	validate := validator.New(validator.WithRequiredStructEnabled())
	validate.RegisterValidation("slug", func(fl validator.FieldLevel) bool {
		return domain.ValidSlug(fl.Field().String())
	})

	health := transporthttp.NewHealthHandler(map[string]transporthttp.ReadinessCheck{
		"postgres": func(ctx context.Context) error { return a.pool.Ping(ctx) },
		"s3":       func(ctx context.Context) error { return a.store.Ping(ctx) },
		"rabbitmq": func(_ context.Context) error { return a.rabbit.Ping() },
	})

	return transporthttp.NewRouter(transporthttp.RouterDeps{
		Logger:        a.Log,
		Health:        health,
		Categories:    transporthttp.NewCategoryHandler(a.categories, validate),
		Datasets:      transporthttp.NewDatasetHandler(a.datasets, validate),
		DatasetGroups: transporthttp.NewDatasetGroupHandler(a.groups, validate),
		Landscape:     transporthttp.NewLandscapeHandler(a.landscape, validate),
		OpenAPISpec:   api.Spec,
		APIKey:        a.Cfg.HTTP.APIKey,
		AuthDisabled:  a.Cfg.HTTP.AuthDisabled,
		DocsEnabled:   a.Cfg.HTTP.DocsEnabled,
		Journal:       journalAdapter{postgres.NewSecurityJournal(a.pool)},
		ReadKeys:      a.Cfg.HTTP.ReadKeys,
	})
}

// journalAdapter connects the transport layer to the security journal table.
type journalAdapter struct{ j *postgres.SecurityJournal }

func (a journalAdapter) RecordEvent(ctx context.Context, e transporthttp.SecurityEvent) error {
	return a.j.Record(ctx, postgres.SecurityEvent{
		Event: e.Event, RemoteAddr: e.RemoteAddr, Method: e.Method, Path: e.Path, Status: e.Status, Detail: e.Detail,
	})
}

// Server builds the HTTP server.
func (a *App) Server() *transporthttp.Server {
	return transporthttp.NewServer(a.Cfg.HTTP, a.Handler(), a.Log)
}

// Consumers returns all RabbitMQ consumers the worker should run.
func (a *App) Consumers() []*rabbitmq.Consumer {
	return []*rabbitmq.Consumer{
		a.ParseConsumer(),
		a.PropertiesConsumer(),
		a.ExtractConsumer(),
		a.ConvertConsumer(),
		a.LandscapeConsumer(),
		a.ExampleConsumer(),
	}
}

// LandscapeConsumer builds the Landscape Program run consumer.
func (a *App) LandscapeConsumer() *rabbitmq.Consumer {
	handler := rabbitmq.NewLandscapeRunHandler(a.landscape, a.Log)
	return rabbitmq.NewConsumer(
		a.rabbit, rabbitmq.LandscapeRunQueue, rabbitmq.LandscapeRunRoutingKey,
		"gis-landscape-runner", handler, a.Log,
	)
}

// PropertiesConsumer builds the plain-vector properties-extraction consumer.
func (a *App) PropertiesConsumer() *rabbitmq.Consumer {
	handler := rabbitmq.NewPropertiesHandler(a.datasets, a.Log)
	return rabbitmq.NewConsumer(
		a.rabbit, rabbitmq.DatasetPropertiesQueue, rabbitmq.DatasetPropertiesRoutingKey,
		"gis-dataset-properties", handler, a.Log,
	)
}

// ParseConsumer builds the dataset attribute-table parse consumer.
func (a *App) ParseConsumer() *rabbitmq.Consumer {
	handler := rabbitmq.NewParseHandler(a.datasets, a.Log)
	return rabbitmq.NewConsumer(
		a.rabbit, rabbitmq.DatasetParseQueue, rabbitmq.DatasetParseRoutingKey,
		"gis-dataset-parser", handler, a.Log,
	)
}

// ExtractConsumer builds the dataset extraction (unpivot) consumer.
func (a *App) ExtractConsumer() *rabbitmq.Consumer {
	handler := rabbitmq.NewExtractHandler(a.datasets, a.Log)
	return rabbitmq.NewConsumer(
		a.rabbit, rabbitmq.DatasetExtractQueue, rabbitmq.DatasetExtractRoutingKey,
		"gis-dataset-extractor", handler, a.Log,
	)
}

// ConvertConsumer builds the raster COG-conversion consumer.
func (a *App) ConvertConsumer() *rabbitmq.Consumer {
	handler := rabbitmq.NewConvertHandler(a.datasets, a.Log)
	return rabbitmq.NewConsumer(
		a.rabbit, rabbitmq.DatasetConvertQueue, rabbitmq.DatasetConvertRoutingKey,
		"gis-dataset-converter", handler, a.Log,
	)
}

// ExampleConsumer builds the generic example RabbitMQ consumer.
func (a *App) ExampleConsumer() *rabbitmq.Consumer {
	handler := rabbitmq.NewExampleHandler(a.eventRepo, a.Log)
	return rabbitmq.NewConsumer(
		a.rabbit, a.Cfg.RabbitMQ.Queue, rabbitmq.ExampleBindingKey,
		"gis-example-consumer", handler, a.Log,
	)
}

// Publisher returns the RabbitMQ publisher.
func (a *App) Publisher() *rabbitmq.Publisher { return a.publisher }

// ReprocessDataset re-enqueues the processing job for the dataset with the given
// id, restarting its asynchronous pipeline.
func (a *App) ReprocessDataset(ctx context.Context, id uuid.UUID) (domain.Dataset, error) {
	return a.datasets.Reprocess(ctx, id)
}

// ReprocessAllDatasets re-enqueues the processing job for every dataset
// matching fileType (nil matches every file type), restarting each one's
// asynchronous pipeline. It returns how many jobs were enqueued and a
// per-dataset map of any failures.
func (a *App) ReprocessAllDatasets(ctx context.Context, fileType *domain.FileType) (int, map[uuid.UUID]error, error) {
	return a.datasets.ReprocessAll(ctx, fileType)
}

// Close releases all resources in reverse order of acquisition.
func (a *App) Close() {
	if a.rabbit != nil {
		if err := a.rabbit.Close(); err != nil {
			a.Log.Warn("close rabbitmq", "error", err)
		}
	}
	if a.pool != nil {
		a.pool.Close()
	}
}
