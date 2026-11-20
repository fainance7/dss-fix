package postgres

import (
	"context"
	"encoding/json"

	"github.com/jackc/pgx/v5/pgxpool"
)

// EventRepository records events for the generic example consumer. It is part of
// the messaging scaffold; remove it alongside the example flow.
type EventRepository struct {
	pool *pgxpool.Pool
}

// NewEventRepository returns an EventRepository backed by the given pool.
func NewEventRepository(pool *pgxpool.Pool) *EventRepository {
	return &EventRepository{pool: pool}
}

// Record inserts an event row. It satisfies rabbitmq.EventRecorder.
func (r *EventRepository) Record(ctx context.Context, kind string, payload json.RawMessage) error {
	_, err := r.pool.Exec(ctx,
		`INSERT INTO events (kind, payload) VALUES ($1, $2)`, kind, payload)
	return mapError(err)
}
