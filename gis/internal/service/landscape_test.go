package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"gis/internal/domain"
	"gis/internal/landscape"
	"gis/internal/raster"

	"github.com/google/uuid"
)

// GetByCode resolves a category by its code so stubCategoryRepo also satisfies
// the categoryReader dependency the landscape service needs.
func (r *stubCategoryRepo) GetByCode(_ context.Context, code string) (domain.Category, error) {
	for _, c := range r.store {
		if c.Code == code {
			return c, nil
		}
	}
	return domain.Category{}, domain.ErrNotFound
}

// lockedDatasetRepo serialises the dataset-repository calls the landscape
// service makes from concurrently executing nodes. stubDatasetRepo is a plain
// map, so without this the -race detector would flag the fan-out.
type lockedDatasetRepo struct {
	*stubDatasetRepo
	mu sync.Mutex
}

func newLockedDatasetRepo() *lockedDatasetRepo {
	return &lockedDatasetRepo{stubDatasetRepo: newStubDatasetRepo()}
}

func (r *lockedDatasetRepo) Create(ctx context.Context, d domain.Dataset) (domain.Dataset, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.stubDatasetRepo.Create(ctx, d)
}

func (r *lockedDatasetRepo) GetByID(ctx context.Context, id uuid.UUID) (domain.Dataset, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.stubDatasetRepo.GetByID(ctx, id)
}

func (r *lockedDatasetRepo) LatestInGroup(ctx context.Context, groupID uuid.UUID, status *string, fileType *domain.FileType) (domain.Dataset, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.stubDatasetRepo.LatestInGroup(ctx, groupID, status, fileType)
}

// stubLPRepo is an in-memory LandscapeRepository for tests.
// lpStatusWrite records one SetRunStatus call and whether the context it was
// handed was still live.
type lpStatusWrite struct {
	status domain.LPRunStatus
	ctxErr error
}

// lpNodeWrite records one SetNodeFinished call the same way.
type lpNodeWrite struct {
	code   string
	status domain.LPNodeStatus
	ctxErr error
}

type stubLPRepo struct {
	mu           sync.Mutex
	runs         map[uuid.UUID]*domain.LPRun
	order        []uuid.UUID
	reuseLookups []string
	clock        int64
	statusWrites []lpStatusWrite
	nodeWrites   []lpNodeWrite
}

// statusesWritten returns the run statuses written so far, in order.
func (r *stubLPRepo) statusesWritten() []domain.LPRunStatus {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]domain.LPRunStatus, 0, len(r.statusWrites))
	for _, w := range r.statusWrites {
		out = append(out, w.status)
	}
	return out
}

// statusWrite returns the last write recording the given status.
func (r *stubLPRepo) statusWrite(status domain.LPRunStatus) (lpStatusWrite, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for i := len(r.statusWrites) - 1; i >= 0; i-- {
		if r.statusWrites[i].status == status {
			return r.statusWrites[i], true
		}
	}
	return lpStatusWrite{}, false
}

// nodeWrite returns the last SetNodeFinished write recording the given status.
func (r *stubLPRepo) nodeWrite(status domain.LPNodeStatus) (lpNodeWrite, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for i := len(r.nodeWrites) - 1; i >= 0; i-- {
		if r.nodeWrites[i].status == status {
			return r.nodeWrites[i], true
		}
	}
	return lpNodeWrite{}, false
}

func newStubLPRepo() *stubLPRepo {
	return &stubLPRepo{runs: map[uuid.UUID]*domain.LPRun{}}
}

// next returns a strictly increasing timestamp so "most recent" ordering is
// deterministic regardless of wall-clock resolution.
func (r *stubLPRepo) next() time.Time {
	r.clock++
	return time.Unix(0, r.clock)
}

func copyRefs(in []domain.LPDatasetRef) []domain.LPDatasetRef {
	if in == nil {
		return nil
	}
	return append([]domain.LPDatasetRef(nil), in...)
}

func copyRun(run domain.LPRun) domain.LPRun {
	out := run
	out.Overrides = map[string]uuid.UUID{}
	for k, v := range run.Overrides {
		out.Overrides[k] = v
	}
	out.Nodes = make([]domain.LPRunNode, len(run.Nodes))
	for i, n := range run.Nodes {
		n.Inputs = copyRefs(n.Inputs)
		n.Outputs = copyRefs(n.Outputs)
		out.Nodes[i] = n
	}
	return out
}

func (r *stubLPRepo) CreateRun(_ context.Context, run domain.LPRun) (domain.LPRun, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	run.ID = uuid.New()
	run.CreatedAt = r.next()
	if run.Overrides == nil {
		run.Overrides = map[string]uuid.UUID{}
	}
	nodes := make([]domain.LPRunNode, 0, len(run.Nodes))
	for _, n := range run.Nodes {
		nodes = append(nodes, domain.LPRunNode{
			ID:       uuid.New(),
			RunID:    run.ID,
			NodeCode: n.NodeCode,
			Stage:    n.Stage,
			Status:   domain.LPNodePending,
			Inputs:   []domain.LPDatasetRef{},
		})
	}
	run.Nodes = nodes
	stored := copyRun(run)
	r.runs[run.ID] = &stored
	r.order = append(r.order, run.ID)
	return copyRun(stored), nil
}

func (r *stubLPRepo) GetRun(_ context.Context, id uuid.UUID) (domain.LPRun, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	run, ok := r.runs[id]
	if !ok {
		return domain.LPRun{}, domain.ErrNotFound
	}
	out := copyRun(*run)
	sort.Slice(out.Nodes, func(i, j int) bool { return out.Nodes[i].NodeCode < out.Nodes[j].NodeCode })
	return out, nil
}

func (r *stubLPRepo) ListRuns(_ context.Context, limit, offset int) ([]domain.LPRun, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := []domain.LPRun{}
	// Newest first.
	for i := len(r.order) - 1; i >= 0; i-- {
		run := copyRun(*r.runs[r.order[i]])
		run.Nodes = nil
		out = append(out, run)
	}
	if offset > len(out) {
		offset = len(out)
	}
	out = out[offset:]
	if limit >= 0 && limit < len(out) {
		out = out[:limit]
	}
	return out, nil
}

func (r *stubLPRepo) CountRuns(_ context.Context) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.runs), nil
}

func (r *stubLPRepo) SetRunStatus(ctx context.Context, id uuid.UUID, status domain.LPRunStatus, errMsg *string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.statusWrites = append(r.statusWrites, lpStatusWrite{status: status, ctxErr: ctx.Err()})
	run, ok := r.runs[id]
	if !ok {
		return domain.ErrNotFound
	}
	run.Status = status
	run.Error = errMsg
	switch status {
	case domain.LPRunRunning:
		t := r.next()
		run.StartedAt = &t
	case domain.LPRunSucceeded, domain.LPRunFailed:
		t := r.next()
		run.FinishedAt = &t
	}
	return nil
}

func (r *stubLPRepo) node(runID uuid.UUID, code string) (*domain.LPRunNode, error) {
	run, ok := r.runs[runID]
	if !ok {
		return nil, domain.ErrNotFound
	}
	for i := range run.Nodes {
		if run.Nodes[i].NodeCode == code {
			return &run.Nodes[i], nil
		}
	}
	return nil, domain.ErrNotFound
}

func (r *stubLPRepo) SetNodeStarted(_ context.Context, runID uuid.UUID, code, fingerprint string, inputs []domain.LPDatasetRef) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	n, err := r.node(runID, code)
	if err != nil {
		return err
	}
	n.Status = domain.LPNodeRunning
	n.Fingerprint = fingerprint
	n.Inputs = copyRefs(inputs)
	n.Error = nil
	t := r.next()
	n.StartedAt = &t
	return nil
}

func (r *stubLPRepo) SetNodeFinished(ctx context.Context, runID uuid.UUID, code string, status domain.LPNodeStatus, outputs []domain.LPDatasetRef, errMsg *string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.nodeWrites = append(r.nodeWrites, lpNodeWrite{code: code, status: status, ctxErr: ctx.Err()})
	n, err := r.node(runID, code)
	if err != nil {
		return err
	}
	n.Status = status
	n.Outputs = copyRefs(outputs)
	n.Error = errMsg
	t := r.next()
	n.FinishedAt = &t
	return nil
}

func (r *stubLPRepo) FindReusableOutputs(_ context.Context, code, fingerprint string, excludeRun uuid.UUID) ([]domain.LPDatasetRef, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.reuseLookups = append(r.reuseLookups, code)

	var best *domain.LPRunNode
	for _, run := range r.runs {
		if run.ID == excludeRun {
			continue
		}
		for i := range run.Nodes {
			n := &run.Nodes[i]
			if n.NodeCode != code || n.Fingerprint != fingerprint {
				continue
			}
			if n.Status != domain.LPNodeSucceeded || n.Outputs == nil {
				continue
			}
			if best == nil || (n.FinishedAt != nil && best.FinishedAt != nil && n.FinishedAt.After(*best.FinishedAt)) {
				best = n
			}
		}
	}
	if best == nil {
		return nil, domain.ErrNotFound
	}
	return copyRefs(best.Outputs), nil
}

// lookups returns the node codes FindReusableOutputs was called for.
func (r *stubLPRepo) lookups() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.reuseLookups...)
}

// stubPublisher implements datasetPublisher: it records the calls and inserts a
// ready raster dataset (as the COG conversion eventually would) so downstream
// nodes can resolve it.
type stubPublisher struct {
	mu       sync.Mutex
	repo     *lockedDatasetRepo
	created  []CreateRasterInput
	uploaded []UploadInput
	err      error
	// errFilename, when set, scopes err to publishes of that output file only,
	// so a test can fail exactly one node while its stage siblings succeed
	// (a sibling failing first would cancel the node under test).
	errFilename string
}

func (p *stubPublisher) CreateRasterFromFile(ctx context.Context, in CreateRasterInput) (domain.Dataset, error) {
	p.mu.Lock()
	if p.err != nil && (p.errFilename == "" || p.errFilename == in.Filename) {
		p.mu.Unlock()
		return domain.Dataset{}, p.err
	}
	p.created = append(p.created, in)
	p.mu.Unlock()

	// The compute step must have written the file the input points at.
	if _, err := os.Stat(in.Path); err != nil {
		return domain.Dataset{}, err
	}
	key := "landscape/" + uuid.NewString() + "/" + in.Filename
	return p.repo.Create(ctx, domain.Dataset{
		CategoryID:     in.CategoryID,
		DatasetGroupID: in.DatasetGroupID,
		Name:           in.Name,
		Filename:       in.Filename,
		StorageKey:     key,
		FileType:       domain.FileTypeRaster,
		ContentType:    "image/tiff",
		Status:         domain.DatasetStatusReady,
		Meta:           in.Meta,
		Automated:      in.Automated,
		StartDate:      in.StartDate,
		EndDate:        in.EndDate,
		CreatedAt:      time.Now(),
	})
}

func (p *stubPublisher) Upload(ctx context.Context, in UploadInput) (domain.Dataset, error) {
	p.mu.Lock()
	if p.err != nil {
		p.mu.Unlock()
		return domain.Dataset{}, p.err
	}
	p.uploaded = append(p.uploaded, in)
	p.mu.Unlock()
	return p.repo.Create(ctx, domain.Dataset{
		CategoryID:     in.CategoryID,
		DatasetGroupID: in.DatasetGroupID,
		Name:           in.Name,
		Filename:       in.Filename,
		StorageKey:     "landscape/" + uuid.NewString() + "/" + in.Filename,
		FileType:       in.FileType,
		ContentType:    in.ContentType,
		Status:         domain.DatasetStatusProcessing,
		StartDate:      in.StartDate,
		EndDate:        in.EndDate,
		CreatedAt:      time.Now(),
	})
}

// stubLPJobs records enqueued landscape runs.
type stubLPJobs struct {
	mu       sync.Mutex
	enqueued []uuid.UUID
	err      error
}

func (j *stubLPJobs) EnqueueRun(_ context.Context, runID uuid.UUID) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.err != nil {
		return j.err
	}
	j.enqueued = append(j.enqueued, runID)
	return nil
}

// stubComputeCall records one invocation of the compute stub.
type stubComputeCall struct {
	node string
	// workers is the per-node worker cap the service passed, or -1 when the
	// call came through the legacy worker-agnostic ComputeFunc.
	workers int
	inputs  map[string]string
	// bodies holds each input file's content at call time; seedDataset stores
	// each object under its own storage key as its body, so a test can tell
	// which dataset a node actually downloaded.
	bodies map[string]string
	params landscape.Params
}

// stubCompute stands in for landscape.Compute: it writes one empty file per
// declared output and records the inputs it was handed.
type stubCompute struct {
	mu    sync.Mutex
	calls []stubComputeCall
	errs  map[string]error
	// waitForCancel names nodes whose compute blocks until its context is
	// cancelled (or the deadline below elapses), so a test can observe whether
	// a sibling's failure cancelled them.
	waitForCancel map[string]bool
	sawCancel     map[string]bool
}

func newStubCompute() *stubCompute {
	return &stubCompute{errs: map[string]error{}, waitForCancel: map[string]bool{}, sawCancel: map[string]bool{}}
}

// fn is a legacy service.ComputeFunc: it sees no worker cap.
func (c *stubCompute) fn(ctx context.Context, rio raster.IO, node landscape.Node, inputs map[string]string, outDir string, p landscape.Params) (map[string]string, error) {
	return c.fnWithWorkers(ctx, rio, node, inputs, outDir, p, -1)
}

// observedCancel reports whether the node's compute saw a cancelled context.
func (c *stubCompute) observedCancel(code string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.sawCancel[code]
}

func (c *stubCompute) fnWithWorkers(ctx context.Context, _ raster.IO, node landscape.Node, inputs map[string]string, outDir string, p landscape.Params, workers int) (map[string]string, error) {
	c.mu.Lock()
	wait := c.waitForCancel[node.Code]
	c.mu.Unlock()
	if wait {
		select {
		case <-ctx.Done():
		case <-time.After(5 * time.Second):
		}
		c.mu.Lock()
		c.sawCancel[node.Code] = ctx.Err() != nil
		c.mu.Unlock()
	}
	// The real Compute honours cancellation, so the stub must too.
	if err := ctx.Err(); err != nil {
		return nil, err
	}

	in := map[string]string{}
	bodies := map[string]string{}
	for k, v := range inputs {
		in[k] = v
		body, err := os.ReadFile(v)
		if err != nil {
			return nil, err
		}
		bodies[k] = string(body)
	}

	c.mu.Lock()
	c.calls = append(c.calls, stubComputeCall{node: node.Code, workers: workers, inputs: in, bodies: bodies, params: p})
	err := c.errs[node.Code]
	c.mu.Unlock()
	if err != nil {
		return nil, err
	}

	out := map[string]string{}
	for _, g := range node.Outputs {
		path := filepath.Join(outDir, g+".tif")
		if err := os.WriteFile(path, []byte("TIF"), 0o600); err != nil {
			return nil, err
		}
		out[g] = path
	}
	return out, nil
}

func (c *stubCompute) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.calls)
}

func (c *stubCompute) nodeCodes() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]string, 0, len(c.calls))
	for _, call := range c.calls {
		out = append(out, call.node)
	}
	sort.Strings(out)
	return out
}

func (c *stubCompute) callFor(code string) (stubComputeCall, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, call := range c.calls {
		if call.node == code {
			return call, true
		}
	}
	return stubComputeCall{}, false
}

func (c *stubCompute) reset() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.calls = nil
}

// lpFixture wires a LandscapeService over in-memory stubs.
type lpFixture struct {
	svc      *LandscapeService
	runs     *stubLPRepo
	datasets *lockedDatasetRepo
	groups   *stubGroupRepo
	cats     *stubCategoryRepo
	pub      *stubPublisher
	store    *stubStore
	jobs     *stubLPJobs
	compute  *stubCompute
	category domain.Category
}

func newLPFixture(t *testing.T) *lpFixture {
	t.Helper()
	return newLPFixtureWith(t, LandscapeOptions{})
}

// newLPFixtureWith builds the fixture with explicit parallelism options.
func newLPFixtureWith(t *testing.T, opts LandscapeOptions) *lpFixture {
	t.Helper()
	ctx := context.Background()

	cats := newStubCategoryRepo()
	cat, err := cats.Create(ctx, domain.Category{Code: landscape.CategoryCode, Name: "Landscape"})
	if err != nil {
		t.Fatalf("seed category: %v", err)
	}

	groups := newStubGroupRepo()
	for _, s := range landscape.Slots {
		if _, err := groups.Create(ctx, domain.DatasetGroup{Code: s.Code, Name: s.Title}); err != nil {
			t.Fatalf("seed group %s: %v", s.Code, err)
		}
	}
	for _, o := range landscape.Outputs {
		if _, err := groups.Create(ctx, domain.DatasetGroup{Code: o.Code, Name: o.Title}); err != nil {
			t.Fatalf("seed group %s: %v", o.Code, err)
		}
	}

	datasets := newLockedDatasetRepo()
	store := newStubStore()
	pub := &stubPublisher{repo: datasets}
	runs := newStubLPRepo()
	jobs := &stubLPJobs{}
	comp := newStubCompute()
	log := slog.New(slog.NewTextHandler(io.Discard, nil))

	svc := NewLandscapeService(runs, datasets, groups, cats, pub, store, jobs, nil, nil, log, opts)
	// Substitute the workers-aware stub so tests can see the per-node cap the
	// service computes; NewLandscapeService's legacy-ComputeFunc wrapping is
	// covered separately.
	svc.computeWithWorkers = comp.fnWithWorkers
	return &lpFixture{
		svc: svc, runs: runs, datasets: datasets, groups: groups, cats: cats,
		pub: pub, store: store, jobs: jobs, compute: comp, category: cat,
	}
}

// groupID resolves a seeded group's id by code.
func (f *lpFixture) groupID(t *testing.T, code string) uuid.UUID {
	t.Helper()
	g, err := f.groups.GetByCode(context.Background(), code)
	if err != nil {
		t.Fatalf("group %s: %v", code, err)
	}
	return g.ID
}

// seedDataset inserts a dataset into a group and stores its object bytes.
func (f *lpFixture) seedDataset(t *testing.T, code, status string, fileType domain.FileType, endDate string) domain.Dataset {
	t.Helper()
	ctx := context.Background()
	gid := f.groupID(t, code)
	key := "uploads/" + uuid.NewString() + "/" + code + ".tif"
	end := endDate
	start := "2020-01-01"
	d, err := f.datasets.Create(ctx, domain.Dataset{
		CategoryID:     f.category.ID,
		DatasetGroupID: &gid,
		Name:           code,
		Filename:       code + ".tif",
		StorageKey:     key,
		FileType:       fileType,
		ContentType:    "image/tiff",
		Status:         status,
		StartDate:      &start,
		EndDate:        &end,
		CreatedAt:      time.Now(),
	})
	if err != nil {
		t.Fatalf("seed dataset %s: %v", code, err)
	}
	if err := f.store.Put(ctx, key, bytes.NewReader([]byte(key)), int64(len(key)), "image/tiff"); err != nil {
		t.Fatalf("seed object %s: %v", key, err)
	}
	return d
}

// seedAllSlots fills every leaf slot with a ready raster.
func (f *lpFixture) seedAllSlots(t *testing.T, except ...string) {
	t.Helper()
	skip := map[string]bool{}
	for _, c := range except {
		skip[c] = true
	}
	for _, s := range landscape.Slots {
		if skip[s.Code] {
			continue
		}
		f.seedDataset(t, s.Code, domain.DatasetStatusReady, domain.FileTypeRaster, "2024-12-31")
	}
}

// runOnce creates a run and executes it, returning the final run state.
func (f *lpFixture) runOnce(t *testing.T, in RunInput) domain.LPRun {
	t.Helper()
	ctx := context.Background()
	run, err := f.svc.CreateRun(ctx, in)
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	execErr := f.svc.Execute(ctx, run.ID)
	got, err := f.svc.GetRun(ctx, run.ID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if execErr != nil && got.Status != domain.LPRunFailed {
		t.Fatalf("Execute returned %v but run status is %s", execErr, got.Status)
	}
	return got
}

// nodeStatuses maps node code -> status for a run.
func nodeStatuses(run domain.LPRun) map[string]domain.LPNodeStatus {
	out := map[string]domain.LPNodeStatus{}
	for _, n := range run.Nodes {
		out[n.NodeCode] = n.Status
	}
	return out
}

// nodeByCode finds a run node by its code.
func nodeByCode(t *testing.T, run domain.LPRun, code string) domain.LPRunNode {
	t.Helper()
	for _, n := range run.Nodes {
		if n.NodeCode == code {
			return n
		}
	}
	t.Fatalf("run has no node %q", code)
	return domain.LPRunNode{}
}

// refsByGroup indexes dataset refs by their group code.
func refsByGroup(refs []domain.LPDatasetRef) map[string]uuid.UUID {
	out := map[string]uuid.UUID{}
	for _, r := range refs {
		out[r.Group] = r.DatasetID
	}
	return out
}

func TestLandscapeService_CreateRun_ValidatesParamsAndOverrides(t *testing.T) {
	ctx := context.Background()

	stage := func(i int) *int { return &i }

	tests := []struct {
		name  string
		setup func(t *testing.T, f *lpFixture) RunInput
	}{
		{
			name: "tie priority is not a permutation",
			setup: func(_ *testing.T, _ *lpFixture) RunInput {
				return RunInput{Params: landscape.Params{TiePriority: []int{1, 2, 3}}}
			},
		},
		{
			name: "window size out of range",
			setup: func(_ *testing.T, _ *lpFixture) RunInput {
				return RunInput{Params: landscape.Params{WindowSize: 10}}
			},
		},
		{
			name: "from_stage out of range",
			setup: func(_ *testing.T, _ *lpFixture) RunInput {
				return RunInput{FromStage: stage(4)}
			},
		},
		{
			name: "override for unknown group",
			setup: func(t *testing.T, f *lpFixture) RunInput {
				d := f.seedDataset(t, landscape.SlotZLCR, domain.DatasetStatusReady, domain.FileTypeRaster, "2024-12-31")
				return RunInput{Overrides: map[string]uuid.UUID{"lp-not-a-group": d.ID}}
			},
		},
		{
			name: "override dataset does not exist",
			setup: func(_ *testing.T, _ *lpFixture) RunInput {
				return RunInput{Overrides: map[string]uuid.UUID{landscape.SlotZLCR: uuid.New()}}
			},
		},
		{
			name: "override dataset is not ready",
			setup: func(t *testing.T, f *lpFixture) RunInput {
				d := f.seedDataset(t, landscape.SlotZLCR, domain.DatasetStatusProcessing, domain.FileTypeRaster, "2024-12-31")
				return RunInput{Overrides: map[string]uuid.UUID{landscape.SlotZLCR: d.ID}}
			},
		},
		{
			name: "override dataset is not a raster",
			setup: func(t *testing.T, f *lpFixture) RunInput {
				d := f.seedDataset(t, landscape.SlotZLCR, domain.DatasetStatusReady, domain.FileTypeVector, "2024-12-31")
				return RunInput{Overrides: map[string]uuid.UUID{landscape.SlotZLCR: d.ID}}
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			f := newLPFixture(t)
			in := tc.setup(t, f)

			_, err := f.svc.CreateRun(ctx, in)
			if !errors.Is(err, domain.ErrValidation) {
				t.Fatalf("expected ErrValidation, got %v", err)
			}
			if len(f.jobs.enqueued) != 0 {
				t.Fatalf("expected no job to be enqueued, got %d", len(f.jobs.enqueued))
			}
		})
	}

	t.Run("valid input queues a run with pending nodes", func(t *testing.T) {
		f := newLPFixture(t)
		override := f.seedDataset(t, landscape.SlotZLCR, domain.DatasetStatusReady, domain.FileTypeRaster, "2024-12-31")

		run, err := f.svc.CreateRun(ctx, RunInput{
			FromStage: stage(2),
			Overrides: map[string]uuid.UUID{landscape.SlotZLCR: override.ID},
			Params:    landscape.Params{V1MinSE12: 2},
		})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if run.Status != domain.LPRunQueued {
			t.Fatalf("status = %s, want queued", run.Status)
		}
		if len(run.Nodes) != len(landscape.Nodes) {
			t.Fatalf("nodes = %d, want %d", len(run.Nodes), len(landscape.Nodes))
		}
		for _, n := range run.Nodes {
			if n.Status != domain.LPNodePending {
				t.Fatalf("node %s status = %s, want pending", n.NodeCode, n.Status)
			}
		}
		if run.FromStage == nil || *run.FromStage != 2 {
			t.Fatalf("from_stage = %v, want 2", run.FromStage)
		}
		if got := run.Overrides[landscape.SlotZLCR]; got != override.ID {
			t.Fatalf("override = %v, want %v", got, override.ID)
		}

		// Params are persisted with the TZ defaults applied, so kernels never
		// see a short tie_priority.
		var stored landscape.Params
		if err := json.Unmarshal(run.Params, &stored); err != nil {
			t.Fatalf("params are not valid JSON: %v", err)
		}
		want := landscape.DefaultParams()
		want.V1MinSE12 = 2
		if fmt.Sprint(stored) != fmt.Sprint(want) {
			t.Fatalf("params = %+v, want %+v", stored, want)
		}

		if len(f.jobs.enqueued) != 1 || f.jobs.enqueued[0] != run.ID {
			t.Fatalf("enqueued = %v, want [%v]", f.jobs.enqueued, run.ID)
		}
	})

	t.Run("enqueue failure fails the run", func(t *testing.T) {
		f := newLPFixture(t)
		f.jobs.err = errors.New("broker down")

		_, err := f.svc.CreateRun(ctx, RunInput{})
		if err == nil {
			t.Fatal("expected an error")
		}
		runs, err := f.svc.ListRuns(ctx, 1, 10)
		if err != nil {
			t.Fatalf("ListRuns: %v", err)
		}
		if len(runs.Items) != 1 {
			t.Fatalf("runs = %d, want 1", len(runs.Items))
		}
		if runs.Items[0].Status != domain.LPRunFailed {
			t.Fatalf("status = %s, want failed", runs.Items[0].Status)
		}
	})
}

func TestLandscapeService_Execute_ResolvesInputsAndPublishesOutputs(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t)

	run := f.runOnce(t, RunInput{})

	if run.Status != domain.LPRunSucceeded {
		t.Fatalf("status = %s (error %v), want succeeded", run.Status, run.Error)
	}
	for _, n := range run.Nodes {
		if n.Status != domain.LPNodeSucceeded {
			t.Fatalf("node %s status = %s (error %v), want succeeded", n.NodeCode, n.Status, n.Error)
		}
	}
	if got := f.compute.count(); got != len(landscape.Nodes) {
		t.Fatalf("compute calls = %d, want %d", got, len(landscape.Nodes))
	}

	// Node 10 consumes what nodes 00 and 01 produced in this very run.
	n00 := refsByGroup(nodeByCode(t, run, "00").Outputs)
	n01 := refsByGroup(nodeByCode(t, run, "01").Outputs)
	n10in := refsByGroup(nodeByCode(t, run, "10").Inputs)
	if n10in[landscape.OutZIntegrated] != n00[landscape.OutZIntegrated] {
		t.Fatalf("node 10 z input = %v, want %v", n10in[landscape.OutZIntegrated], n00[landscape.OutZIntegrated])
	}
	if n10in[landscape.OutUIntegrated] != n01[landscape.OutUIntegrated] {
		t.Fatalf("node 10 u input = %v, want %v", n10in[landscape.OutUIntegrated], n01[landscape.OutUIntegrated])
	}
	call, ok := f.compute.callFor("10")
	if !ok {
		t.Fatal("compute was not called for node 10")
	}
	for _, g := range []string{landscape.OutZIntegrated, landscape.OutUIntegrated} {
		if call.inputs[g] == "" {
			t.Fatalf("node 10 compute input %q is empty (got %v)", g, call.inputs)
		}
	}

	// Every output group got exactly one published dataset.
	if len(f.pub.created) != len(landscape.Outputs) {
		t.Fatalf("created datasets = %d, want %d", len(f.pub.created), len(landscape.Outputs))
	}
	seen := map[string]bool{}
	for _, in := range f.pub.created {
		if !in.Automated {
			t.Fatalf("dataset %q is not marked automated", in.Name)
		}
		if in.CategoryID != f.category.ID {
			t.Fatalf("category = %v, want %v", in.CategoryID, f.category.ID)
		}
		if in.DatasetGroupID == nil {
			t.Fatalf("dataset %q has no group", in.Name)
		}
		group := ""
		for _, o := range landscape.Outputs {
			if f.groupID(t, o.Code) == *in.DatasetGroupID {
				group = o.Code
			}
		}
		if group == "" {
			t.Fatalf("dataset %q is filed under an unknown group %v", in.Name, *in.DatasetGroupID)
		}
		if in.Filename != group+".tif" {
			t.Fatalf("filename = %q, want %q", in.Filename, group+".tif")
		}
		seen[group] = true

		var meta map[string]any
		if err := json.Unmarshal(in.Meta, &meta); err != nil {
			t.Fatalf("meta of %q is not valid JSON: %v", in.Name, err)
		}
		if meta["lp_run_id"] != run.ID.String() {
			t.Fatalf("meta lp_run_id = %v, want %v", meta["lp_run_id"], run.ID)
		}
		if _, ok := meta["lp_node"]; !ok {
			t.Fatalf("meta of %q has no lp_node: %v", in.Name, meta)
		}
		if _, ok := meta["inputs"]; !ok {
			t.Fatalf("meta of %q has no inputs: %v", in.Name, meta)
		}
		if in.EndDate == nil || *in.EndDate != "2024-12-31" {
			t.Fatalf("end_date = %v, want 2024-12-31", in.EndDate)
		}
	}
	if len(seen) != len(landscape.Outputs) {
		t.Fatalf("groups covered = %d, want %d", len(seen), len(landscape.Outputs))
	}
}

func TestLandscapeService_Execute_ReusesFingerprintMatches(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t)

	first := f.runOnce(t, RunInput{})
	if first.Status != domain.LPRunSucceeded {
		t.Fatalf("first run status = %s (%v)", first.Status, first.Error)
	}
	f.compute.reset()

	second := f.runOnce(t, RunInput{})
	if second.Status != domain.LPRunSucceeded {
		t.Fatalf("second run status = %s (%v)", second.Status, second.Error)
	}
	if got := f.compute.count(); got != 0 {
		t.Fatalf("compute calls on rerun = %d, want 0 (%v)", got, f.compute.nodeCodes())
	}
	if got := f.runs.lookups(); len(got) != 2*len(landscape.Nodes) {
		t.Fatalf("reuse lookups = %d, want %d (once per node per run)", len(got), 2*len(landscape.Nodes))
	}
	for _, n := range second.Nodes {
		if n.Status != domain.LPNodeSkipped {
			t.Fatalf("node %s status = %s, want skipped", n.NodeCode, n.Status)
		}
	}
	for _, n := range second.Nodes {
		want := refsByGroup(nodeByCode(t, first, n.NodeCode).Outputs)
		got := refsByGroup(n.Outputs)
		if len(got) != len(want) {
			t.Fatalf("node %s outputs = %v, want %v", n.NodeCode, got, want)
		}
		for g, id := range want {
			if got[g] != id {
				t.Fatalf("node %s output %s = %v, want %v", n.NodeCode, g, got[g], id)
			}
		}
	}
}

func TestLandscapeService_Execute_RecomputesDownstreamOfChangedInput(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t)

	if first := f.runOnce(t, RunInput{}); first.Status != domain.LPRunSucceeded {
		t.Fatalf("first run status = %s (%v)", first.Status, first.Error)
	}
	f.compute.reset()

	// A fresher P-crop raster only affects the C conflicts and P max nodes and
	// everything downstream of them.
	f.seedDataset(t, landscape.SlotPCrop, domain.DatasetStatusReady, domain.FileTypeRaster, "2025-06-30")

	second := f.runOnce(t, RunInput{})
	if second.Status != domain.LPRunSucceeded {
		t.Fatalf("second run status = %s (%v)", second.Status, second.Error)
	}

	statuses := nodeStatuses(second)
	for _, code := range []string{"00", "01", "02", "03", "10"} {
		if statuses[code] != domain.LPNodeSkipped {
			t.Fatalf("node %s status = %s, want skipped", code, statuses[code])
		}
	}
	for _, code := range []string{"20", "21", "30", "31"} {
		if statuses[code] != domain.LPNodeSucceeded {
			t.Fatalf("node %s status = %s, want succeeded", code, statuses[code])
		}
	}
	want := []string{"20", "21", "30", "31"}
	got := f.compute.nodeCodes()
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("recomputed nodes = %v, want %v", got, want)
	}
}

func TestLandscapeService_Execute_FromStageForcesRecompute(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t)

	if first := f.runOnce(t, RunInput{}); first.Status != domain.LPRunSucceeded {
		t.Fatalf("first run status = %s (%v)", first.Status, first.Error)
	}
	f.compute.reset()

	from := 2
	second := f.runOnce(t, RunInput{FromStage: &from})
	if second.Status != domain.LPRunSucceeded {
		t.Fatalf("second run status = %s (%v)", second.Status, second.Error)
	}

	statuses := nodeStatuses(second)
	for _, code := range []string{"00", "01", "02", "03", "10"} {
		if statuses[code] != domain.LPNodeSkipped {
			t.Fatalf("node %s status = %s, want skipped", code, statuses[code])
		}
	}
	for _, code := range []string{"20", "21", "30", "31"} {
		if statuses[code] != domain.LPNodeSucceeded {
			t.Fatalf("node %s status = %s, want succeeded", code, statuses[code])
		}
	}
	if got, want := f.compute.nodeCodes(), []string{"20", "21", "30", "31"}; fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("recomputed nodes = %v, want %v", got, want)
	}
}

func TestLandscapeService_Execute_MissingSlotFailsRun(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t, landscape.SlotZWR)

	run := f.runOnce(t, RunInput{})

	if run.Status != domain.LPRunFailed {
		t.Fatalf("status = %s, want failed", run.Status)
	}
	if run.Error == nil || !strings.Contains(*run.Error, landscape.SlotZWR) {
		t.Fatalf("run error = %v, want it to name %q", run.Error, landscape.SlotZWR)
	}
	statuses := nodeStatuses(run)
	for _, code := range []string{"00", "02"} {
		if statuses[code] != domain.LPNodeFailed {
			t.Fatalf("node %s status = %s, want failed", code, statuses[code])
		}
		n := nodeByCode(t, run, code)
		if n.Error == nil || !strings.Contains(*n.Error, landscape.SlotZWR) {
			t.Fatalf("node %s error = %v, want it to name %q", code, n.Error, landscape.SlotZWR)
		}
	}
	// Stage 1 and beyond are never started.
	for _, code := range []string{"10", "20", "21", "30", "31"} {
		if statuses[code] != domain.LPNodePending {
			t.Fatalf("node %s status = %s, want pending", code, statuses[code])
		}
	}
}

func TestLandscapeService_UploadToSlot_UnknownSlot(t *testing.T) {
	f := newLPFixture(t)

	_, err := f.svc.UploadToSlot(context.Background(), "lp-not-a-slot", SlotUploadInput{
		Filename:    "x.tif",
		ContentType: "image/tiff",
		Size:        3,
		Reader:      bytes.NewReader([]byte("TIF")),
	})
	if !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
	if len(f.pub.uploaded) != 0 {
		t.Fatalf("expected no upload, got %d", len(f.pub.uploaded))
	}
}

func TestLandscapeService_UploadToSlot_KnownSlot(t *testing.T) {
	f := newLPFixture(t)
	ctx := context.Background()

	got, err := f.svc.UploadToSlot(ctx, landscape.SlotPSnow, SlotUploadInput{
		Filename:    "snow.tif",
		ContentType: "image/tiff",
		Size:        3,
		Reader:      bytes.NewReader([]byte("TIF")),
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(f.pub.uploaded) != 1 {
		t.Fatalf("uploads = %d, want 1", len(f.pub.uploaded))
	}
	in := f.pub.uploaded[0]
	if in.FileType != domain.FileTypeRaster {
		t.Fatalf("file_type = %s, want raster", in.FileType)
	}
	if in.CategoryID != f.category.ID {
		t.Fatalf("category = %v, want %v", in.CategoryID, f.category.ID)
	}
	gid := f.groupID(t, landscape.SlotPSnow)
	if in.DatasetGroupID == nil || *in.DatasetGroupID != gid {
		t.Fatalf("group = %v, want %v", in.DatasetGroupID, gid)
	}
	if in.Name != "snow.tif" {
		t.Fatalf("name = %q, want the filename as a default", in.Name)
	}
	if got.ID == uuid.Nil {
		t.Fatal("returned dataset has no id")
	}
}

func TestLandscapeService_Slots_ListsLatestPerSlot(t *testing.T) {
	f := newLPFixture(t)
	// Any status counts for a slot, not just ready.
	seeded := f.seedDataset(t, landscape.SlotUSoil, domain.DatasetStatusProcessing, domain.FileTypeRaster, "2024-12-31")

	slots, err := f.svc.Slots(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(slots) != len(landscape.Slots) {
		t.Fatalf("slots = %d, want %d", len(slots), len(landscape.Slots))
	}
	for _, s := range slots {
		switch s.Code {
		case landscape.SlotUSoil:
			if s.Latest == nil || s.Latest.ID != seeded.ID {
				t.Fatalf("latest for %s = %v, want %v", s.Code, s.Latest, seeded.ID)
			}
			if s.GroupID != f.groupID(t, landscape.SlotUSoil) {
				t.Fatalf("group id = %v", s.GroupID)
			}
		default:
			if s.Latest != nil {
				t.Fatalf("latest for %s = %v, want nil", s.Code, s.Latest)
			}
		}
	}
}

func TestLandscapeService_Results_ListsLatestPerOutput(t *testing.T) {
	f := newLPFixture(t)
	ctx := context.Background()

	ready := f.seedDataset(t, landscape.OutE, domain.DatasetStatusReady, domain.FileTypeRaster, "2024-12-31")
	// A not-yet-converted result must not be served as the latest result.
	f.seedDataset(t, landscape.OutV1, domain.DatasetStatusProcessing, domain.FileTypeRaster, "2025-01-01")

	eGroup := f.groupID(t, landscape.OutE)
	if _, err := f.groups.SetSLD(ctx, eGroup, "groups/e/sld/e.sld", "e.sld", "application/xml"); err != nil {
		t.Fatalf("SetSLD: %v", err)
	}

	results, err := f.svc.Results(ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(results) != len(landscape.Outputs) {
		t.Fatalf("results = %d, want %d", len(results), len(landscape.Outputs))
	}

	byGroup := map[string]ResultInfo{}
	for _, r := range results {
		byGroup[r.Group] = r
	}

	e := byGroup[landscape.OutE]
	if e.Latest == nil || e.Latest.ID != ready.ID {
		t.Fatalf("latest for %s = %v, want %v", landscape.OutE, e.Latest, ready.ID)
	}
	if e.Node != "10" {
		t.Fatalf("node for %s = %q, want 10", landscape.OutE, e.Node)
	}
	if e.Title != "E Ecological Constraint" {
		t.Fatalf("title = %q", e.Title)
	}
	wantCog := "/datasets/" + ready.ID.String() + ".cog"
	if e.CogURL == nil || *e.CogURL != wantCog {
		t.Fatalf("cog_url = %v, want %q", e.CogURL, wantCog)
	}
	wantSld := "/dataset-groups/" + eGroup.String() + "/sld"
	if e.SldURL == nil || *e.SldURL != wantSld {
		t.Fatalf("sld_url = %v, want %q", e.SldURL, wantSld)
	}

	v1 := byGroup[landscape.OutV1]
	if v1.Latest != nil {
		t.Fatalf("latest for %s = %v, want nil (not ready)", landscape.OutV1, v1.Latest)
	}
	if v1.CogURL != nil || v1.SldURL != nil {
		t.Fatalf("urls for %s = %v/%v, want nil", landscape.OutV1, v1.CogURL, v1.SldURL)
	}

	zmax := byGroup[landscape.OutZMax]
	if zmax.SldURL != nil {
		t.Fatalf("sld_url for a group without an SLD = %v, want nil", zmax.SldURL)
	}
}

func TestLandscapeService_MarkRunFailed(t *testing.T) {
	f := newLPFixture(t)
	ctx := context.Background()

	run, err := f.svc.CreateRun(ctx, RunInput{})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if err := f.svc.MarkRunFailed(ctx, run.ID, "worker died"); err != nil {
		t.Fatalf("MarkRunFailed: %v", err)
	}
	got, err := f.svc.GetRun(ctx, run.ID)
	if err != nil {
		t.Fatalf("GetRun: %v", err)
	}
	if got.Status != domain.LPRunFailed {
		t.Fatalf("status = %s, want failed", got.Status)
	}
	if got.Error == nil || *got.Error != "worker died" {
		t.Fatalf("error = %v, want %q", got.Error, "worker died")
	}
}

func TestLandscapeService_Execute_NodeFailureFailsRunAndCleansUp(t *testing.T) {
	tests := []struct {
		name    string
		arrange func(f *lpFixture)
		want    string
	}{
		{
			name:    "compute fails",
			arrange: func(f *lpFixture) { f.compute.errs["00"] = errors.New("grid mismatch") },
			want:    "grid mismatch",
		},
		{
			name: "publishing the result fails",
			arrange: func(f *lpFixture) {
				// Fail only node 00's output: if every stage-0 publish failed,
				// whichever sibling failed first would cancel node 00 and its
				// recorded error would be "context canceled" instead.
				f.pub.err = errors.New("minio down")
				f.pub.errFilename = landscape.OutZIntegrated + ".tif"
			},
			want: "minio down",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			f := newLPFixture(t)
			f.seedAllSlots(t)
			tc.arrange(f)

			run := f.runOnce(t, RunInput{})

			if run.Status != domain.LPRunFailed {
				t.Fatalf("status = %s, want failed", run.Status)
			}
			if run.Error == nil || !strings.Contains(*run.Error, tc.want) {
				t.Fatalf("run error = %v, want it to mention %q", run.Error, tc.want)
			}
			n00 := nodeByCode(t, run, "00")
			if n00.Status != domain.LPNodeFailed {
				t.Fatalf("node 00 status = %s, want failed", n00.Status)
			}
			if n00.Error == nil || !strings.Contains(*n00.Error, tc.want) {
				t.Fatalf("node 00 error = %v, want it to mention %q", n00.Error, tc.want)
			}
			// Temp files are always cleaned up, even on failure.
			for _, call := range f.compute.calls {
				for _, path := range call.inputs {
					if _, err := os.Stat(filepath.Dir(path)); !os.IsNotExist(err) {
						t.Fatalf("temp dir %q was not removed (stat err %v)", filepath.Dir(path), err)
					}
				}
			}
		})
	}
}

func TestLandscapeService_Execute_OverridePinsInputOverFresherDataset(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t)

	// Two ready rasters in the same slot group: the pinned one is the older of
	// the two, so resolving by freshness alone would pick the other.
	pinned := f.seedDataset(t, landscape.SlotZLCR, domain.DatasetStatusReady, domain.FileTypeRaster, "2023-01-31")
	fresher := f.seedDataset(t, landscape.SlotZLCR, domain.DatasetStatusReady, domain.FileTypeRaster, "2026-01-31")

	run := f.runOnce(t, RunInput{Overrides: map[string]uuid.UUID{landscape.SlotZLCR: pinned.ID}})
	if run.Status != domain.LPRunSucceeded {
		t.Fatalf("status = %s (%v), want succeeded", run.Status, run.Error)
	}

	// Both Z nodes consume the slot, and both must record the pinned dataset.
	for _, code := range []string{"00", "02"} {
		got := refsByGroup(nodeByCode(t, run, code).Inputs)[landscape.SlotZLCR]
		if got != pinned.ID {
			if got == fresher.ID {
				t.Fatalf("node %s resolved the fresher dataset %v, want the pinned %v", code, got, pinned.ID)
			}
			t.Fatalf("node %s input for %s = %v, want %v", code, landscape.SlotZLCR, got, pinned.ID)
		}
	}

	// The compute stub was handed the pinned object's bytes, not the fresher one's.
	call, ok := f.compute.callFor("00")
	if !ok {
		t.Fatal("compute was not called for node 00")
	}
	path := call.inputs[landscape.SlotZLCR]
	if path == "" {
		t.Fatalf("node 00 got no path for %s (%v)", landscape.SlotZLCR, call.inputs)
	}
	if got := call.bodies[landscape.SlotZLCR]; got != pinned.StorageKey {
		t.Fatalf("node 00 downloaded %q, want the pinned dataset's object %q", got, pinned.StorageKey)
	}

	// A group with no override still resolves the freshest ready dataset.
	newest := f.seedDataset(t, landscape.SlotUVeg, domain.DatasetStatusReady, domain.FileTypeRaster, "2026-02-28")
	second := f.runOnce(t, RunInput{Overrides: map[string]uuid.UUID{landscape.SlotZLCR: pinned.ID}})
	if got := refsByGroup(nodeByCode(t, second, "01").Inputs)[landscape.SlotUVeg]; got != newest.ID {
		t.Fatalf("node 01 input for %s = %v, want the freshest %v", landscape.SlotUVeg, got, newest.ID)
	}
}

// F2: a redelivered message for a finished run must be acked without rerunning.
func TestLandscapeService_Execute_IgnoresTerminalRun(t *testing.T) {
	f := newLPFixture(t)
	ctx := context.Background()
	f.seedAllSlots(t)

	run, err := f.svc.CreateRun(ctx, RunInput{})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if err := f.runs.SetRunStatus(ctx, run.ID, domain.LPRunSucceeded, nil); err != nil {
		t.Fatal(err)
	}
	f.runs.mu.Lock()
	f.runs.statusWrites = nil
	f.runs.mu.Unlock()

	if err := f.svc.Execute(ctx, run.ID); err != nil {
		t.Fatalf("Execute on a terminal run must ack, got %v", err)
	}
	if n := f.compute.count(); n != 0 {
		t.Fatalf("compute called %d times for a terminal run", n)
	}
	if got := f.runs.statusesWritten(); len(got) != 0 {
		t.Fatalf("no status write expected, got %v", got)
	}
	got, err := f.svc.GetRun(ctx, run.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != domain.LPRunSucceeded {
		t.Fatalf("run status changed to %s", got.Status)
	}
}

// F2: a "running" run is still executed, so a worker that crashed mid-run
// recovers on redelivery.
func TestLandscapeService_Execute_RerunsRunningRun(t *testing.T) {
	f := newLPFixture(t)
	ctx := context.Background()
	f.seedAllSlots(t)

	run, err := f.svc.CreateRun(ctx, RunInput{})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if err := f.runs.SetRunStatus(ctx, run.ID, domain.LPRunRunning, nil); err != nil {
		t.Fatal(err)
	}
	if err := f.svc.Execute(ctx, run.ID); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if f.compute.count() == 0 {
		t.Fatal("a running run must be executed on redelivery")
	}
}

// F4: terminal status writes must land even when the caller's context is dead.
func TestLandscapeService_Execute_TerminalWritesSurviveCancelledContext(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t)

	run, err := f.svc.CreateRun(context.Background(), RunInput{})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := f.svc.Execute(ctx, run.ID); err == nil {
		t.Fatal("expected Execute to fail on a cancelled context")
	}

	got, err := f.svc.GetRun(context.Background(), run.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != domain.LPRunFailed {
		t.Fatalf("run status = %s, want failed", got.Status)
	}
	w, ok := f.runs.statusWrite(domain.LPRunFailed)
	if !ok {
		t.Fatal("no failed status write recorded")
	}
	if w.ctxErr != nil {
		t.Fatalf("terminal run status write got a cancelled context: %v", w.ctxErr)
	}
	nw, ok := f.runs.nodeWrite(domain.LPNodeFailed)
	if !ok {
		t.Fatal("no failed node write recorded")
	}
	if nw.ctxErr != nil {
		t.Fatalf("terminal node write got a cancelled context: %v", nw.ctxErr)
	}
}

// F4: MarkRunFailed is the consumer's last resort and must not need a live ctx.
func TestLandscapeService_MarkRunFailed_IgnoresCancelledContext(t *testing.T) {
	f := newLPFixture(t)
	run, err := f.svc.CreateRun(context.Background(), RunInput{})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := f.svc.MarkRunFailed(ctx, run.ID, "boom"); err != nil {
		t.Fatalf("MarkRunFailed: %v", err)
	}
	w, ok := f.runs.statusWrite(domain.LPRunFailed)
	if !ok {
		t.Fatal("no failed status write recorded")
	}
	if w.ctxErr != nil {
		t.Fatalf("MarkRunFailed wrote on a cancelled context: %v", w.ctxErr)
	}
}

// F5: the per-node worker cap handed to Compute is the run's budget split over
// the concurrently running nodes.
func TestLandscapeService_Execute_SplitsWorkerBudgetOverNodes(t *testing.T) {
	cases := []struct {
		name string
		opts LandscapeOptions
		want int
	}{
		{"defaults", LandscapeOptions{}, DefaultMaxWorkers / DefaultMaxConcurrentNodes},
		{"explicit", LandscapeOptions{MaxWorkers: 12, MaxConcurrentNodes: 3}, 4},
		{"single node keeps the whole budget", LandscapeOptions{MaxWorkers: 6, MaxConcurrentNodes: 1}, 6},
		{"never below one", LandscapeOptions{MaxWorkers: 1, MaxConcurrentNodes: 4}, 1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newLPFixtureWith(t, c.opts)
			f.seedAllSlots(t)
			got := f.runOnce(t, RunInput{})
			if got.Status != domain.LPRunSucceeded {
				t.Fatalf("run status = %s: %v", got.Status, got.Error)
			}
			f.compute.mu.Lock()
			defer f.compute.mu.Unlock()
			if len(f.compute.calls) == 0 {
				t.Fatal("compute was never called")
			}
			for _, call := range f.compute.calls {
				if call.workers != c.want {
					t.Fatalf("node %s got workers=%d, want %d", call.node, call.workers, c.want)
				}
			}
		})
	}
}

// F6: a failing node cancels its siblings instead of letting them run on.
func TestLandscapeService_Execute_FailingNodeCancelsSiblings(t *testing.T) {
	// Four stage-0 nodes, all in flight at once; "00" fails immediately while
	// "01" blocks waiting for its context.
	f := newLPFixtureWith(t, LandscapeOptions{MaxWorkers: 8, MaxConcurrentNodes: 4})
	f.seedAllSlots(t)
	f.compute.errs["00"] = errors.New("boom")
	f.compute.waitForCancel["01"] = true

	got := f.runOnce(t, RunInput{})
	if got.Status != domain.LPRunFailed {
		t.Fatalf("run status = %s, want failed", got.Status)
	}
	if !f.compute.observedCancel("01") {
		t.Fatal("sibling node 01 did not observe the cancellation of its context")
	}
	for _, n := range got.Nodes {
		if n.NodeCode == "01" && n.Status == domain.LPNodeSucceeded {
			t.Fatal("cancelled sibling must not be recorded as succeeded")
		}
	}
}

// F5: a legacy ComputeFunc still wires up through NewLandscapeService.
func TestNewLandscapeService_WrapsLegacyComputeFunc(t *testing.T) {
	f := newLPFixture(t)
	f.seedAllSlots(t)
	// Rebuild the service the production-adjacent way: a legacy ComputeFunc,
	// no direct field substitution.
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	svc := NewLandscapeService(f.runs, f.datasets, f.groups, f.cats, f.pub, f.store, f.jobs, nil, f.compute.fn, log, LandscapeOptions{})

	run, err := svc.CreateRun(context.Background(), RunInput{})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if err := svc.Execute(context.Background(), run.ID); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	call, ok := f.compute.callFor("00")
	if !ok {
		t.Fatal("legacy ComputeFunc was never called")
	}
	if call.workers != -1 {
		t.Fatalf("legacy ComputeFunc should not see a worker cap, got %d", call.workers)
	}
}
