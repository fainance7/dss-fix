package postgres

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"gis/internal/domain"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

// testPool connects to the database named by TEST_DB_URL (migrations already
// applied) or skips the test. Repository SQL is otherwise only exercised by
// hand, so these tests are the guard against Postgres-only failures such as
// parameter type deduction (SQLSTATE 42P08) that no stub can reproduce.
func testPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	url := os.Getenv("TEST_DB_URL")
	if url == "" {
		t.Skip("TEST_DB_URL not set; skipping database test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	pool, err := Connect(ctx, url)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func TestLandscapeRepository_SetRunStatus_AllTransitions(t *testing.T) {
	pool := testPool(t)
	repo := NewLandscapeRepository(pool)
	ctx := context.Background()

	run, err := repo.CreateRun(ctx, domain.LPRun{
		Status: domain.LPRunQueued,
		Params: []byte(`{}`),
		Nodes:  []domain.LPRunNode{{NodeCode: "00", Stage: 0, Status: domain.LPNodePending}},
	})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	t.Cleanup(func() { _, _ = pool.Exec(ctx, `DELETE FROM lp_runs WHERE id = $1`, run.ID) })

	if err := repo.SetRunStatus(ctx, run.ID, domain.LPRunRunning, nil); err != nil {
		t.Fatalf("SetRunStatus(running): %v", err)
	}
	got, err := repo.GetRun(ctx, run.ID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if got.Status != domain.LPRunRunning || got.StartedAt == nil || got.FinishedAt != nil {
		t.Fatalf("after running: status=%s started=%v finished=%v", got.Status, got.StartedAt, got.FinishedAt)
	}

	msg := "boom"
	if err := repo.SetRunStatus(ctx, run.ID, domain.LPRunFailed, &msg); err != nil {
		t.Fatalf("SetRunStatus(failed): %v", err)
	}
	got, err = repo.GetRun(ctx, run.ID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if got.Status != domain.LPRunFailed || got.FinishedAt == nil || got.Error == nil || *got.Error != msg {
		t.Fatalf("after failed: status=%s finished=%v error=%v", got.Status, got.FinishedAt, got.Error)
	}

	if err := repo.SetRunStatus(ctx, run.ID, domain.LPRunSucceeded, nil); err != nil {
		t.Fatalf("SetRunStatus(succeeded): %v", err)
	}
}

func TestLandscapeRepository_NodeLifecycleAndReuse(t *testing.T) {
	pool := testPool(t)
	repo := NewLandscapeRepository(pool)
	ctx := context.Background()

	newRun := func() domain.LPRun {
		run, err := repo.CreateRun(ctx, domain.LPRun{
			Status:    domain.LPRunQueued,
			Params:    []byte(`{"window_size":2048}`),
			Overrides: map[string]uuid.UUID{"lp-e": uuid.New()},
			Nodes:     []domain.LPRunNode{{NodeCode: "00", Stage: 0, Status: domain.LPNodePending}},
		})
		if err != nil {
			t.Fatalf("CreateRun: %v", err)
		}
		t.Cleanup(func() { _, _ = pool.Exec(ctx, `DELETE FROM lp_runs WHERE id = $1`, run.ID) })
		return run
	}

	inputs := []domain.LPDatasetRef{{Group: "lp-z-wr", DatasetID: uuid.New()}}
	outputs := []domain.LPDatasetRef{{Group: "lp-z-integrated", DatasetID: uuid.New()}}

	first := newRun()
	if err := repo.SetNodeStarted(ctx, first.ID, "00", "fp-1", inputs); err != nil {
		t.Fatalf("SetNodeStarted: %v", err)
	}
	if err := repo.SetNodeFinished(ctx, first.ID, "00", domain.LPNodeSucceeded, outputs, nil); err != nil {
		t.Fatalf("SetNodeFinished: %v", err)
	}
	got, err := repo.GetRun(ctx, first.ID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if len(got.Nodes) != 1 || got.Nodes[0].Status != domain.LPNodeSucceeded ||
		len(got.Nodes[0].Inputs) != 1 || got.Nodes[0].Inputs[0] != inputs[0] ||
		len(got.Nodes[0].Outputs) != 1 || got.Nodes[0].Outputs[0] != outputs[0] ||
		got.Nodes[0].StartedAt == nil || got.Nodes[0].FinishedAt == nil {
		t.Fatalf("node row not round-tripped: %+v", got.Nodes)
	}
	if len(got.Overrides) != 1 {
		t.Fatalf("overrides not round-tripped: %+v", got.Overrides)
	}

	second := newRun()
	reused, err := repo.FindReusableOutputs(ctx, "00", "fp-1", second.ID)
	if err != nil {
		t.Fatalf("FindReusableOutputs: %v", err)
	}
	if len(reused) != 1 || reused[0] != outputs[0] {
		t.Fatalf("reused = %+v, want %+v", reused, outputs)
	}
	if _, err := repo.FindReusableOutputs(ctx, "00", "fp-other", second.ID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("unknown fingerprint: err = %v, want ErrNotFound", err)
	}
	if _, err := repo.FindReusableOutputs(ctx, "00", "fp-1", first.ID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("own run must be excluded: err = %v, want ErrNotFound", err)
	}

	failMsg := "grid mismatch"
	if err := repo.SetNodeFinished(ctx, second.ID, "00", domain.LPNodeFailed, nil, &failMsg); err != nil {
		t.Fatalf("SetNodeFinished(failed): %v", err)
	}
	runs, err := repo.ListRuns(ctx, 10, 0)
	if err != nil {
		t.Fatalf("ListRuns: %v", err)
	}
	if len(runs) < 2 || runs[0].ID != second.ID {
		t.Fatalf("ListRuns must be newest first: %+v", runs)
	}
	if n, err := repo.CountRuns(ctx); err != nil || n < 2 {
		t.Fatalf("CountRuns = %d, %v", n, err)
	}
}
