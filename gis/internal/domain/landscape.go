package domain

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// LPRunStatus is the lifecycle status of a Landscape Program run.
type LPRunStatus string

// Landscape Program run statuses.
const (
	LPRunQueued    LPRunStatus = "queued"
	LPRunRunning   LPRunStatus = "running"
	LPRunSucceeded LPRunStatus = "succeeded"
	LPRunFailed    LPRunStatus = "failed"
)

// LPNodeStatus is the lifecycle status of a single node within a Landscape
// Program run.
type LPNodeStatus string

// Landscape Program run node statuses.
const (
	LPNodePending   LPNodeStatus = "pending"
	LPNodeRunning   LPNodeStatus = "running"
	LPNodeSkipped   LPNodeStatus = "skipped"
	LPNodeSucceeded LPNodeStatus = "succeeded"
	LPNodeFailed    LPNodeStatus = "failed"
)

// LPDatasetRef references a dataset produced for, or consumed from, a
// dataset group slot in the Landscape Program graph.
type LPDatasetRef struct {
	Group     string    `json:"group"`
	DatasetID uuid.UUID `json:"dataset_id"`
}

// LPRun is a single execution of the Landscape Program graph.
type LPRun struct {
	ID         uuid.UUID            `json:"id"`
	Status     LPRunStatus          `json:"status"`
	FromStage  *int                 `json:"from_stage"`
	Params     json.RawMessage      `json:"params"`
	Overrides  map[string]uuid.UUID `json:"overrides"`
	Error      *string              `json:"error"`
	CreatedAt  time.Time            `json:"created_at"`
	StartedAt  *time.Time           `json:"started_at"`
	FinishedAt *time.Time           `json:"finished_at"`
	Nodes      []LPRunNode          `json:"nodes"`
}

// LPRunNode is the execution state of a single graph node within an LPRun.
type LPRunNode struct {
	ID          uuid.UUID      `json:"id"`
	RunID       uuid.UUID      `json:"run_id"`
	NodeCode    string         `json:"node_code"`
	Stage       int            `json:"stage"`
	Status      LPNodeStatus   `json:"status"`
	Fingerprint string         `json:"fingerprint"`
	Inputs      []LPDatasetRef `json:"inputs"`
	Outputs     []LPDatasetRef `json:"outputs"`
	Error       *string        `json:"error"`
	StartedAt   *time.Time     `json:"started_at"`
	FinishedAt  *time.Time     `json:"finished_at"`
}
