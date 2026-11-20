package rabbitmq

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	// ExampleRoutingKey is the routing key used by the generic example flow.
	ExampleRoutingKey = "example.created"
	// ExampleBindingKey binds the example queue to example.* routing keys.
	ExampleBindingKey = "example.#"
)

// EventRecorder persists a received event. It is the seam between the messaging
// layer and storage for the example flow; a real domain would call its own
// service instead.
type EventRecorder interface {
	Record(ctx context.Context, kind string, payload json.RawMessage) error
}

// ExampleMessage is the payload published and consumed by the scaffold.
type ExampleMessage struct {
	Kind      string    `json:"kind"`
	Message   string    `json:"message"`
	EmittedAt time.Time `json:"emitted_at"`
}

// ExampleHandler is a generic consumer that records every delivery into the
// events table. It demonstrates the messaging -> storage path; delete it when a
// real async use case replaces the scaffold.
type ExampleHandler struct {
	recorder EventRecorder
	log      *slog.Logger
}

// NewExampleHandler returns an ExampleHandler.
func NewExampleHandler(recorder EventRecorder, log *slog.Logger) *ExampleHandler {
	return &ExampleHandler{recorder: recorder, log: log}
}

// Handle decodes the delivery (best-effort) and records it.
func (h *ExampleHandler) Handle(ctx context.Context, d amqp.Delivery) error {
	var msg ExampleMessage
	if err := json.Unmarshal(d.Body, &msg); err != nil {
		return fmt.Errorf("decode example message: %w", err)
	}

	h.log.Info("received example message",
		"routing_key", d.RoutingKey, "kind", msg.Kind, "message", msg.Message)

	if err := h.recorder.Record(ctx, "example", d.Body); err != nil {
		return fmt.Errorf("record event: %w", err)
	}
	return nil
}

// PublishExample emits a single example message; used by `gis worker --publish-example`.
func PublishExample(ctx context.Context, pub *Publisher) error {
	body, err := json.Marshal(ExampleMessage{
		Kind:      "example",
		Message:   "hello from gis worker",
		EmittedAt: time.Now().UTC(),
	})
	if err != nil {
		return err
	}
	return pub.Publish(ctx, ExampleRoutingKey, body)
}
