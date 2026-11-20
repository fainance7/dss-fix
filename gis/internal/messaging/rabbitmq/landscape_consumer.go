package rabbitmq

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"github.com/google/uuid"
	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	// LandscapeRunRoutingKey routes Landscape Program run jobs.
	LandscapeRunRoutingKey = "landscape.run"
	// LandscapeRunQueue receives Landscape Program run jobs.
	LandscapeRunQueue = "gis.landscape.run"
	// markRunFailedTimeout bounds the best-effort terminal status write made
	// after Execute failed, on a context detached from the delivery's.
	markRunFailedTimeout = 10 * time.Second
)

// LandscapeJob is the message body for a Landscape Program run job.
type LandscapeJob struct {
	RunID uuid.UUID `json:"run_id"`
}

// LandscapeJobPublisher publishes Landscape Program run jobs; it implements
// service.LandscapeJobEnqueuer.
type LandscapeJobPublisher struct {
	pub *Publisher
}

// NewLandscapeJobPublisher returns a LandscapeJobPublisher.
func NewLandscapeJobPublisher(pub *Publisher) *LandscapeJobPublisher {
	return &LandscapeJobPublisher{pub: pub}
}

// EnqueueRun publishes an execution job for the given run.
func (p *LandscapeJobPublisher) EnqueueRun(ctx context.Context, runID uuid.UUID) error {
	body, err := json.Marshal(LandscapeJob{RunID: runID})
	if err != nil {
		return err
	}
	return p.pub.Publish(ctx, LandscapeRunRoutingKey, body)
}

// LandscapeProcessor executes Landscape Program runs; implemented by the service.
type LandscapeProcessor interface {
	Execute(ctx context.Context, runID uuid.UUID) error
	// MarkRunFailed records a terminal failure on the run so it does not remain
	// stuck in an in-progress status.
	MarkRunFailed(ctx context.Context, runID uuid.UUID, reason string) error
}

// landscapeHandler executes one Landscape Program run per delivery.
type landscapeHandler struct {
	proc LandscapeProcessor
	log  *slog.Logger
}

func (h landscapeHandler) Handle(ctx context.Context, d amqp.Delivery) error {
	var job LandscapeJob
	if err := json.Unmarshal(d.Body, &job); err != nil {
		return fmt.Errorf("decode landscape run job: %w", err)
	}
	h.log.Info("processing landscape run", "run_id", job.RunID)

	// Этап 7: a redelivered run was interrupted (e.g. worker killed out of
	// memory); fail it instead of looping.
	var err error
	if d.Redelivered {
		h.log.Warn("security: redelivered landscape run rejected", "run_id", job.RunID)
		err = fmt.Errorf("run interrupted on a previous attempt (possible resource exhaustion)")
	} else {
		err = h.proc.Execute(ctx, job.RunID)
	}
	if err != nil {
		// The delivery is nacked without requeue (see Consumer.dispatch), so the
		// run will not be retried. Record the failure on the run itself;
		// otherwise it stays "running" forever. This covers the errors Execute
		// returns before it can mark the run itself (a missing run, a failed
		// status write), since Execute already marks the run failed for every
		// node failure. Best-effort: log if marking fails, but still return the
		// original error so the delivery is nacked.
		// Detached from ctx: Execute may well have failed *because* ctx was
		// cancelled (shutdown), and the write must still land or the run stays
		// "running" forever.
		mctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), markRunFailedTimeout)
		if markErr := h.proc.MarkRunFailed(mctx, job.RunID, err.Error()); markErr != nil {
			h.log.Error("mark landscape run failed", "run_id", job.RunID, "error", markErr)
		}
		cancel()
	}
	return err
}

// NewLandscapeRunHandler returns a handler that executes Landscape Program runs.
func NewLandscapeRunHandler(p LandscapeProcessor, log *slog.Logger) Handler {
	return landscapeHandler{proc: p, log: log}
}
