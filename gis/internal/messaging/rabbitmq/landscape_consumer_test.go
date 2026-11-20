package rabbitmq

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/google/uuid"
	amqp "github.com/rabbitmq/amqp091-go"
)

// fakeLandscapeProcessor records MarkRunFailed calls and returns a preset error
// from Execute.
type fakeLandscapeProcessor struct {
	runErr     error
	executed   []uuid.UUID
	failedID   uuid.UUID
	failedWith string
	failedCnt  int
}

func (f *fakeLandscapeProcessor) Execute(_ context.Context, runID uuid.UUID) error {
	f.executed = append(f.executed, runID)
	return f.runErr
}

func (f *fakeLandscapeProcessor) MarkRunFailed(_ context.Context, runID uuid.UUID, reason string) error {
	f.failedCnt++
	f.failedID = runID
	f.failedWith = reason
	return nil
}

func landscapeDelivery(t *testing.T, id uuid.UUID) amqp.Delivery {
	t.Helper()
	body, err := json.Marshal(LandscapeJob{RunID: id})
	if err != nil {
		t.Fatalf("marshal job: %v", err)
	}
	return amqp.Delivery{Body: body}
}

func TestLandscapeHandler_MarksRunFailedOnExecuteError(t *testing.T) {
	id := uuid.New()
	fp := &fakeLandscapeProcessor{runErr: errors.New("node 00: grid mismatch")}
	h := NewLandscapeRunHandler(fp, discardLogger())

	err := h.Handle(context.Background(), landscapeDelivery(t, id))
	if err == nil {
		t.Fatal("expected the run error to be returned so the delivery is nacked")
	}
	if len(fp.executed) != 1 || fp.executed[0] != id {
		t.Fatalf("Execute called with %v, want [%s]", fp.executed, id)
	}
	if fp.failedCnt != 1 {
		t.Fatalf("expected MarkRunFailed to be called once, got %d", fp.failedCnt)
	}
	if fp.failedID != id {
		t.Fatalf("MarkRunFailed called with wrong id: got %s want %s", fp.failedID, id)
	}
	if fp.failedWith != "node 00: grid mismatch" {
		t.Fatalf("MarkRunFailed called with wrong reason: %q", fp.failedWith)
	}
}

func TestLandscapeHandler_DoesNotMarkFailedOnSuccess(t *testing.T) {
	fp := &fakeLandscapeProcessor{runErr: nil}
	h := NewLandscapeRunHandler(fp, discardLogger())

	if err := h.Handle(context.Background(), landscapeDelivery(t, uuid.New())); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if fp.failedCnt != 0 {
		t.Fatalf("expected MarkRunFailed not to be called on success, got %d", fp.failedCnt)
	}
}

func TestLandscapeHandler_UndecodableBodyIsNacked(t *testing.T) {
	fp := &fakeLandscapeProcessor{}
	h := NewLandscapeRunHandler(fp, discardLogger())

	err := h.Handle(context.Background(), amqp.Delivery{Body: []byte("not json")})
	if err == nil {
		t.Fatal("expected a decode error so the delivery is nacked")
	}
	if len(fp.executed) != 0 {
		t.Fatalf("Execute must not run for an undecodable body, got %v", fp.executed)
	}
}
