package rabbitmq

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"

	"github.com/google/uuid"
	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	// DatasetParseRoutingKey routes dataset attribute-table parse jobs.
	DatasetParseRoutingKey = "dataset.parse"
	// DatasetParseQueue receives dataset parse jobs.
	DatasetParseQueue = "gis.datasets.parse"
	// DatasetPropertiesRoutingKey routes plain-vector attribute-table extraction.
	DatasetPropertiesRoutingKey = "dataset.properties"
	// DatasetPropertiesQueue receives plain-vector properties jobs.
	DatasetPropertiesQueue = "gis.datasets.properties"
	// DatasetExtractRoutingKey routes dataset extraction (unpivot) jobs.
	DatasetExtractRoutingKey = "dataset.extract"
	// DatasetExtractQueue receives dataset extraction jobs.
	DatasetExtractQueue = "gis.datasets.extract"
	// DatasetConvertRoutingKey routes raster COG-conversion jobs.
	DatasetConvertRoutingKey = "dataset.cog"
	// DatasetConvertQueue receives raster COG-conversion jobs.
	DatasetConvertQueue = "gis.datasets.cog"
)

// DatasetJob is the message body for a dataset job (parse or extract).
type DatasetJob struct {
	DatasetID uuid.UUID `json:"dataset_id"`
}

// DatasetJobPublisher publishes dataset parse/extract jobs; it implements
// service.JobEnqueuer.
type DatasetJobPublisher struct {
	pub *Publisher
}

// NewDatasetJobPublisher returns a DatasetJobPublisher.
func NewDatasetJobPublisher(pub *Publisher) *DatasetJobPublisher {
	return &DatasetJobPublisher{pub: pub}
}

// EnqueueParse publishes a parse job for the given dataset.
func (p *DatasetJobPublisher) EnqueueParse(ctx context.Context, datasetID uuid.UUID) error {
	return p.publish(ctx, DatasetParseRoutingKey, datasetID)
}

// EnqueueProperties publishes a plain-vector properties-extraction job.
func (p *DatasetJobPublisher) EnqueueProperties(ctx context.Context, datasetID uuid.UUID) error {
	return p.publish(ctx, DatasetPropertiesRoutingKey, datasetID)
}

// EnqueueExtract publishes an extraction job for the given dataset.
func (p *DatasetJobPublisher) EnqueueExtract(ctx context.Context, datasetID uuid.UUID) error {
	return p.publish(ctx, DatasetExtractRoutingKey, datasetID)
}

// EnqueueConvert publishes a raster COG-conversion job for the given dataset.
func (p *DatasetJobPublisher) EnqueueConvert(ctx context.Context, datasetID uuid.UUID) error {
	return p.publish(ctx, DatasetConvertRoutingKey, datasetID)
}

func (p *DatasetJobPublisher) publish(ctx context.Context, routingKey string, datasetID uuid.UUID) error {
	body, err := json.Marshal(DatasetJob{DatasetID: datasetID})
	if err != nil {
		return err
	}
	return p.pub.Publish(ctx, routingKey, body)
}

// DatasetProcessor runs the async dataset jobs; implemented by the service.
type DatasetProcessor interface {
	Parse(ctx context.Context, datasetID uuid.UUID) error
	ExtractProperties(ctx context.Context, datasetID uuid.UUID) error
	Extract(ctx context.Context, datasetID uuid.UUID) error
	ConvertToCOG(ctx context.Context, datasetID uuid.UUID) error
	// MarkFailed records a terminal processing failure on the dataset so it does
	// not remain stuck in an in-progress status.
	MarkFailed(ctx context.Context, datasetID uuid.UUID, reason string) error
}

// jobHandler dispatches a dataset job to one processor function.
type jobHandler struct {
	name string
	fn   func(ctx context.Context, id uuid.UUID) error
	proc DatasetProcessor
	log  *slog.Logger
}

func (h jobHandler) Handle(ctx context.Context, d amqp.Delivery) error {
	var job DatasetJob
	if err := json.Unmarshal(d.Body, &job); err != nil {
		return fmt.Errorf("decode %s job: %w", h.name, err)
	}
	h.log.Info("processing dataset "+h.name, "dataset_id", job.DatasetID)

	err := h.fn(ctx, job.DatasetID)
	if err != nil {
		// The delivery is nacked without requeue (see Consumer.dispatch), so the
		// job will not be retried. Record the failure on the dataset itself;
		// otherwise it stays stuck in an in-progress status forever (e.g. when a
		// PostGIS insert returns an error mid-job). Best-effort: log if marking
		// fails, but still return the original error so the delivery is nacked.
		if markErr := h.proc.MarkFailed(ctx, job.DatasetID, err.Error()); markErr != nil {
			h.log.Error("mark dataset failed", "dataset_id", job.DatasetID, "job", h.name, "error", markErr)
		}
	}
	return err
}

// NewParseHandler returns a handler that parses datasets.
func NewParseHandler(p DatasetProcessor, log *slog.Logger) Handler {
	return jobHandler{name: "parse", fn: p.Parse, proc: p, log: log}
}

// NewPropertiesHandler returns a handler that extracts a plain vector's table.
func NewPropertiesHandler(p DatasetProcessor, log *slog.Logger) Handler {
	return jobHandler{name: "properties", fn: p.ExtractProperties, proc: p, log: log}
}

// NewExtractHandler returns a handler that extracts (unpivots) datasets.
func NewExtractHandler(p DatasetProcessor, log *slog.Logger) Handler {
	return jobHandler{name: "extract", fn: p.Extract, proc: p, log: log}
}

// NewConvertHandler returns a handler that converts rasters to COGs.
func NewConvertHandler(p DatasetProcessor, log *slog.Logger) Handler {
	return jobHandler{name: "convert", fn: p.ConvertToCOG, proc: p, log: log}
}
