package postgres

import (
	"context"
	"encoding/json"

	"gis/internal/domain"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// LandscapeRepository persists Landscape Program runs in Postgres.
type LandscapeRepository struct {
	pool *pgxpool.Pool
}

// NewLandscapeRepository returns a LandscapeRepository backed by the given pool.
func NewLandscapeRepository(pool *pgxpool.Pool) *LandscapeRepository {
	return &LandscapeRepository{pool: pool}
}

const runColumns = `id, status, from_stage, params, overrides, error, created_at, started_at, finished_at`

// scanRun scans a run row. overrides is stored as jsonb but scanned into
// []byte and unmarshalled manually so an empty/NULL value maps to an empty
// map rather than nil.
func scanRun(row pgx.Row) (domain.LPRun, error) {
	var r domain.LPRun
	var overrides []byte
	err := row.Scan(&r.ID, &r.Status, &r.FromStage, &r.Params, &overrides, &r.Error, &r.CreatedAt, &r.StartedAt, &r.FinishedAt)
	if err != nil {
		return r, err
	}
	r.Overrides = map[string]uuid.UUID{}
	if len(overrides) > 0 {
		if err := json.Unmarshal(overrides, &r.Overrides); err != nil {
			return r, err
		}
	}
	return r, nil
}

const nodeColumns = `id, run_id, node_code, stage, status, fingerprint, input_datasets, output_datasets, error, started_at, finished_at`

func scanNode(row pgx.Row) (domain.LPRunNode, error) {
	var n domain.LPRunNode
	err := row.Scan(&n.ID, &n.RunID, &n.NodeCode, &n.Stage, &n.Status, &n.Fingerprint, &n.Inputs, &n.Outputs, &n.Error, &n.StartedAt, &n.FinishedAt)
	return n, err
}

// CreateRun inserts a new run and its nodes (status pending) in one
// transaction, and returns the run with its nodes populated (in the order
// they were given).
func (r *LandscapeRepository) CreateRun(ctx context.Context, run domain.LPRun) (domain.LPRun, error) {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return domain.LPRun{}, mapError(err)
	}
	defer tx.Rollback(ctx)

	overrides := run.Overrides
	if overrides == nil {
		overrides = map[string]uuid.UUID{}
	}
	overridesJSON, err := json.Marshal(overrides)
	if err != nil {
		return domain.LPRun{}, err
	}

	row := tx.QueryRow(ctx,
		`INSERT INTO lp_runs (status, from_stage, params, overrides)
		 VALUES ($1, $2, $3, $4)
		 RETURNING `+runColumns,
		run.Status, run.FromStage, run.Params, overridesJSON,
	)
	out, err := scanRun(row)
	if err != nil {
		return domain.LPRun{}, mapError(err)
	}

	nodes := make([]domain.LPRunNode, 0, len(run.Nodes))
	for _, n := range run.Nodes {
		row := tx.QueryRow(ctx,
			`INSERT INTO lp_run_nodes (run_id, node_code, stage, status)
			 VALUES ($1, $2, $3, $4)
			 RETURNING `+nodeColumns,
			out.ID, n.NodeCode, n.Stage, domain.LPNodePending,
		)
		node, err := scanNode(row)
		if err != nil {
			return domain.LPRun{}, mapError(err)
		}
		nodes = append(nodes, node)
	}
	out.Nodes = nodes

	if err := tx.Commit(ctx); err != nil {
		return domain.LPRun{}, mapError(err)
	}
	return out, nil
}

// GetRun returns the run with the given id, with its nodes ordered by
// node_code. Returns domain.ErrNotFound if the run does not exist.
func (r *LandscapeRepository) GetRun(ctx context.Context, id uuid.UUID) (domain.LPRun, error) {
	row := r.pool.QueryRow(ctx, `SELECT `+runColumns+` FROM lp_runs WHERE id = $1`, id)
	out, err := scanRun(row)
	if err != nil {
		return domain.LPRun{}, mapError(err)
	}

	rows, err := r.pool.Query(ctx,
		`SELECT `+nodeColumns+` FROM lp_run_nodes WHERE run_id = $1 ORDER BY node_code`, id)
	if err != nil {
		return domain.LPRun{}, mapError(err)
	}
	defer rows.Close()

	nodes := make([]domain.LPRunNode, 0)
	for rows.Next() {
		n, err := scanNode(rows)
		if err != nil {
			return domain.LPRun{}, mapError(err)
		}
		nodes = append(nodes, n)
	}
	if err := rows.Err(); err != nil {
		return domain.LPRun{}, mapError(err)
	}
	out.Nodes = nodes
	return out, nil
}

// ListRuns returns a page of runs (without nodes), newest first.
func (r *LandscapeRepository) ListRuns(ctx context.Context, limit, offset int) ([]domain.LPRun, error) {
	rows, err := r.pool.Query(ctx,
		`SELECT `+runColumns+` FROM lp_runs ORDER BY created_at DESC LIMIT $1 OFFSET $2`, limit, offset)
	if err != nil {
		return nil, mapError(err)
	}
	defer rows.Close()

	runs := make([]domain.LPRun, 0)
	for rows.Next() {
		run, err := scanRun(rows)
		if err != nil {
			return nil, mapError(err)
		}
		runs = append(runs, run)
	}
	return runs, mapError(rows.Err())
}

// CountRuns returns the total number of runs.
func (r *LandscapeRepository) CountRuns(ctx context.Context) (int, error) {
	var n int
	err := r.pool.QueryRow(ctx, `SELECT count(*) FROM lp_runs`).Scan(&n)
	return n, mapError(err)
}

// SetRunStatus updates a run's status and error. Transitioning to running
// sets started_at; transitioning to succeeded or failed sets finished_at.
// Returns domain.ErrNotFound if the run does not exist.
func (r *LandscapeRepository) SetRunStatus(ctx context.Context, id uuid.UUID, status domain.LPRunStatus, errMsg *string) error {
	tag, err := r.pool.Exec(ctx,
		// $2 is used both as the assigned value (varchar column) and in the CASE
		// comparisons (text literals); the explicit ::text casts keep Postgres
		// from deducing two different types for one parameter (SQLSTATE 42P08).
		`UPDATE lp_runs SET status = $2::text, error = $3,
		   started_at  = CASE WHEN $2::text = 'running' THEN now() ELSE started_at END,
		   finished_at = CASE WHEN $2::text IN ('succeeded','failed') THEN now() ELSE finished_at END
		 WHERE id = $1`,
		id, status, errMsg,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SetNodeStarted marks a run's node as running, storing its fingerprint and
// resolved inputs, and clears any previous error. Returns domain.ErrNotFound
// if no such node exists.
func (r *LandscapeRepository) SetNodeStarted(ctx context.Context, runID uuid.UUID, code string, fingerprint string, inputs []domain.LPDatasetRef) error {
	tag, err := r.pool.Exec(ctx,
		`UPDATE lp_run_nodes
		 SET status = 'running', fingerprint = $3, input_datasets = $4, started_at = now(), error = NULL
		 WHERE run_id = $1 AND node_code = $2`,
		runID, code, fingerprint, inputs,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SetNodeFinished marks a run's node as finished with the given status,
// storing its outputs and error. Returns domain.ErrNotFound if no such node
// exists.
func (r *LandscapeRepository) SetNodeFinished(ctx context.Context, runID uuid.UUID, code string, status domain.LPNodeStatus, outputs []domain.LPDatasetRef, errMsg *string) error {
	tag, err := r.pool.Exec(ctx,
		`UPDATE lp_run_nodes
		 SET status = $3, output_datasets = $4, error = $5, finished_at = now()
		 WHERE run_id = $1 AND node_code = $2`,
		runID, code, status, outputs, errMsg,
	)
	if err != nil {
		return mapError(err)
	}
	if tag.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// FindReusableOutputs returns the outputs of the most recently succeeded node
// with the same code and fingerprint, excluding excludeRun, so its outputs
// may be reused instead of recomputed. Returns domain.ErrNotFound if none.
func (r *LandscapeRepository) FindReusableOutputs(ctx context.Context, code, fingerprint string, excludeRun uuid.UUID) ([]domain.LPDatasetRef, error) {
	var outputs []domain.LPDatasetRef
	err := r.pool.QueryRow(ctx,
		`SELECT output_datasets FROM lp_run_nodes
		 WHERE node_code = $1 AND fingerprint = $2 AND status = 'succeeded' AND run_id <> $3 AND output_datasets IS NOT NULL
		 ORDER BY finished_at DESC LIMIT 1`,
		code, fingerprint, excludeRun,
	).Scan(&outputs)
	if err != nil {
		return nil, mapError(err)
	}
	return outputs, nil
}
