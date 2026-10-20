package rabbitmq

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"testing"

	"github.com/google/uuid"
	amqp "github.com/rabbitmq/amqp091-go"
)

// fakeProcessor records MarkFailed calls and returns a preset error from jobs.
type fakeProcessor struct {
	jobErr     error
	failedID   uuid.UUID
	failedWith string
	failedCnt  int
}

func (f *fakeProcessor) Parse(context.Context, uuid.UUID) error             { return f.jobErr }
func (f *fakeProcessor) ExtractProperties(context.Context, uuid.UUID) error { return f.jobErr }
func (f *fakeProcessor) Extract(context.Context, uuid.UUID) error           { return f.jobErr }
func (f *fakeProcessor) ConvertToCOG(context.Context, uuid.UUID) error      { return f.jobErr }

func (f *fakeProcessor) MarkFailed(_ context.Context, id uuid.UUID, reason string) error {
	f.failedCnt++
	f.failedID = id
	f.failedWith = reason
	return nil
}

func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func delivery(t *testing.T, id uuid.UUID) amqp.Delivery {
	t.Helper()
	body, err := json.Marshal(DatasetJob{DatasetID: id})
	if err != nil {
		t.Fatalf("marshal job: %v", err)
	}
	return amqp.Delivery{Body: body}
}

func TestJobHandler_MarksDatasetFailedOnJobError(t *testing.T) {
	id := uuid.New()
	fp := &fakeProcessor{jobErr: errors.New("insert into postgis failed")}
	h := NewPropertiesHandler(fp, discardLogger())

	err := h.Handle(context.Background(), delivery(t, id))
	if err == nil {
		t.Fatal("expected the job error to be returned so the delivery is nacked")
	}
	if fp.failedCnt != 1 {
		t.Fatalf("expected MarkFailed to be called once, got %d", fp.failedCnt)
	}
	if fp.failedID != id {
		t.Fatalf("MarkFailed called with wrong id: got %s want %s", fp.failedID, id)
	}
	if fp.failedWith != "insert into postgis failed" {
		t.Fatalf("MarkFailed called with wrong reason: %q", fp.failedWith)
	}
}

func TestJobHandler_DoesNotMarkFailedOnSuccess(t *testing.T) {
	fp := &fakeProcessor{jobErr: nil}
	h := NewExtractHandler(fp, discardLogger())

	if err := h.Handle(context.Background(), delivery(t, uuid.New())); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if fp.failedCnt != 0 {
		t.Fatalf("expected MarkFailed not to be called on success, got %d", fp.failedCnt)
	}
}
