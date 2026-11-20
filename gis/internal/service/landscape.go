package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"gis/internal/domain"
	"gis/internal/landscape"
	"gis/internal/raster"

	"github.com/google/uuid"
	"golang.org/x/sync/errgroup"
)

// ComputeFunc runs one graph node over its input rasters, writing one raster
// per output group into outDir and returning the produced paths by group. It
// mirrors landscape.Compute so tests can substitute a stub.
type ComputeFunc func(ctx context.Context, rio raster.IO, node landscape.Node, inputs map[string]string, outDir string, p landscape.Params) (map[string]string, error)

// ComputeWithWorkersFunc is ComputeFunc plus an explicit per-node worker cap.
// It mirrors landscape.ComputeWithWorkers.
type ComputeWithWorkersFunc func(ctx context.Context, rio raster.IO, node landscape.Node, inputs map[string]string, outDir string, p landscape.Params, workers int) (map[string]string, error)

// Default bounds on a run's parallelism, mirroring config.LandscapeConfig.
const (
	// DefaultMaxWorkers is the total window-worker budget of one run.
	DefaultMaxWorkers = 8
	// DefaultMaxConcurrentNodes is how many nodes of a stage run at a time.
	DefaultMaxConcurrentNodes = 2
)

// terminalWriteTimeout bounds a terminal status write made on a context that
// has already been cancelled (see terminalCtx).
const terminalWriteTimeout = 10 * time.Second

// LandscapeOptions bounds a run's parallelism, and with it the worker's peak
// memory: MaxConcurrentNodes nodes run at a time, and they share the
// MaxWorkers window-worker budget. Zero or negative values fall back to the
// defaults.
type LandscapeOptions struct {
	MaxWorkers         int
	MaxConcurrentNodes int
}

func (o LandscapeOptions) withDefaults() LandscapeOptions {
	if o.MaxWorkers < 1 {
		o.MaxWorkers = DefaultMaxWorkers
	}
	if o.MaxConcurrentNodes < 1 {
		o.MaxConcurrentNodes = DefaultMaxConcurrentNodes
	}
	return o
}

// workersPerNode splits the total worker budget over the concurrently running
// nodes, so nodes x workers stays at MaxWorkers. Always at least 1.
func (o LandscapeOptions) workersPerNode() int {
	return max(1, o.MaxWorkers/o.MaxConcurrentNodes)
}

// LandscapeRepository is the persistence behaviour LandscapeService needs.
type LandscapeRepository interface {
	CreateRun(ctx context.Context, run domain.LPRun) (domain.LPRun, error)
	GetRun(ctx context.Context, id uuid.UUID) (domain.LPRun, error)
	ListRuns(ctx context.Context, limit, offset int) ([]domain.LPRun, error)
	CountRuns(ctx context.Context) (int, error)
	SetRunStatus(ctx context.Context, id uuid.UUID, status domain.LPRunStatus, errMsg *string) error
	SetNodeStarted(ctx context.Context, runID uuid.UUID, code string, fingerprint string, inputs []domain.LPDatasetRef) error
	SetNodeFinished(ctx context.Context, runID uuid.UUID, code string, status domain.LPNodeStatus, outputs []domain.LPDatasetRef, errMsg *string) error
	FindReusableOutputs(ctx context.Context, code, fingerprint string, excludeRun uuid.UUID) ([]domain.LPDatasetRef, error)
}

// LandscapeJobEnqueuer schedules asynchronous Landscape Program runs.
type LandscapeJobEnqueuer interface {
	EnqueueRun(ctx context.Context, runID uuid.UUID) error
}

// datasetPublisher is the slice of DatasetService the Landscape Program needs:
// uploading a slot input and publishing a computed result.
type datasetPublisher interface {
	Upload(ctx context.Context, in UploadInput) (domain.Dataset, error)
	CreateRasterFromFile(ctx context.Context, in CreateRasterInput) (domain.Dataset, error)
}

// DatasetService is what the landscape service publishes through in production.
var _ datasetPublisher = (*DatasetService)(nil)

// SlotInfo describes one leaf input slot and the dataset currently filling it.
type SlotInfo struct {
	Code    string                 `json:"code"`
	Title   string                 `json:"title"`
	GroupID uuid.UUID              `json:"group_id"`
	Latest  *domain.DatasetSummary `json:"latest"`
}

// ResultInfo describes one computed output group and its freshest ready dataset.
type ResultInfo struct {
	Group   string                 `json:"group"`
	Title   string                 `json:"title"`
	Node    string                 `json:"node"`
	GroupID uuid.UUID              `json:"group_id"`
	Latest  *domain.DatasetSummary `json:"latest"`
	CogURL  *string                `json:"cog_url"`
	SldURL  *string                `json:"sld_url"`
}

// SlotUploadInput carries a raster upload destined for one input slot.
type SlotUploadInput struct {
	Filename    string
	ContentType string
	Size        int64
	Reader      io.Reader
	Name        string
	StartDate   *string
	EndDate     *string
}

// RunInput carries the options of a new Landscape Program run.
type RunInput struct {
	// FromStage forces recomputation from this stage on (0..3); nil recomputes
	// only what changed.
	FromStage *int
	// Overrides pins a specific dataset for a group code instead of resolving
	// the freshest ready one.
	Overrides map[string]uuid.UUID
	Params    landscape.Params
}

// LPRunPage is a page of runs with pagination metadata.
type LPRunPage struct {
	Items    []domain.LPRun
	Page     int
	PageSize int
	Total    int
}

// LandscapeService plans and executes Landscape Program runs.
type LandscapeService struct {
	runs       LandscapeRepository
	datasets   DatasetRepository
	groups     DatasetGroupRepository
	categories categoryReader
	publisher  datasetPublisher
	store      ObjectStore
	jobs       LandscapeJobEnqueuer
	rio        raster.IO
	// compute is the legacy worker-count-agnostic entry point, kept so an
	// existing ComputeFunc still wires up; runNode always calls
	// computeWithWorkers, which wraps compute when one was provided.
	compute            ComputeFunc
	computeWithWorkers ComputeWithWorkersFunc
	opts               LandscapeOptions
	log                *slog.Logger
}

// NewLandscapeService wires the run repository, the dataset and group
// repositories (input resolution), the category reader (the "landscape"
// category every result is filed under), the dataset publisher (result
// datasets and slot uploads), the object store (input downloads), the job
// enqueuer, and the raster IO / compute function the graph runs on. Pass a nil
// compute (the production wiring) to run landscape.ComputeWithWorkers with the
// per-node share of opts.MaxWorkers; a non-nil ComputeFunc is honoured for
// backward compatibility but never sees the worker cap. opts bounds the run's
// parallelism; zero fields fall back to the package defaults.
func NewLandscapeService(
	runs LandscapeRepository,
	datasets DatasetRepository,
	groups DatasetGroupRepository,
	categories categoryReader,
	publisher datasetPublisher,
	store ObjectStore,
	jobs LandscapeJobEnqueuer,
	rio raster.IO,
	compute ComputeFunc,
	log *slog.Logger,
	opts LandscapeOptions,
) *LandscapeService {
	// Default to the workers-aware entry point; wrap a supplied legacy
	// ComputeFunc (which has no worker parameter) when one is given.
	withWorkers := ComputeWithWorkersFunc(landscape.ComputeWithWorkers)
	if compute != nil {
		withWorkers = func(ctx context.Context, rio raster.IO, node landscape.Node, inputs map[string]string, outDir string, p landscape.Params, _ int) (map[string]string, error) {
			return compute(ctx, rio, node, inputs, outDir, p)
		}
	}
	return &LandscapeService{
		runs:               runs,
		datasets:           datasets,
		groups:             groups,
		categories:         categories,
		publisher:          publisher,
		store:              store,
		jobs:               jobs,
		rio:                rio,
		compute:            compute,
		computeWithWorkers: withWorkers,
		opts:               opts.withDefaults(),
		log:                log,
	}
}

// Slots lists every leaf input slot with its group and latest dataset (any
// status), so the UI can show what has been uploaded.
func (s *LandscapeService) Slots(ctx context.Context) ([]SlotInfo, error) {
	out := make([]SlotInfo, 0, len(landscape.Slots))
	for _, slot := range landscape.Slots {
		g, err := s.groups.GetByCode(ctx, slot.Code)
		if err != nil {
			return nil, fmt.Errorf("dataset group %q: %w", slot.Code, err)
		}
		info := SlotInfo{Code: slot.Code, Title: slot.Title, GroupID: g.ID}
		latest, err := s.datasets.LatestInGroup(ctx, g.ID, nil, nil)
		switch {
		case err == nil:
			summary := summaryOf(latest)
			info.Latest = &summary
		case errors.Is(err, domain.ErrNotFound):
			// An empty slot is expected before the first upload.
		default:
			return nil, err
		}
		out = append(out, info)
	}
	return out, nil
}

// UploadToSlot stores a raster under the slot's dataset group in the landscape
// category, through the regular upload path (COG conversion is enqueued).
// Returns domain.ErrNotFound when the code is not a known slot.
func (s *LandscapeService) UploadToSlot(ctx context.Context, code string, in SlotUploadInput) (domain.Dataset, error) {
	slot, ok := landscape.SlotByCode(code)
	if !ok {
		return domain.Dataset{}, fmt.Errorf("%w: unknown landscape slot %q", domain.ErrNotFound, code)
	}
	g, err := s.groups.GetByCode(ctx, slot.Code)
	if err != nil {
		return domain.Dataset{}, fmt.Errorf("dataset group %q: %w", slot.Code, err)
	}
	cat, err := s.categories.GetByCode(ctx, landscape.CategoryCode)
	if err != nil {
		return domain.Dataset{}, fmt.Errorf("category %q: %w", landscape.CategoryCode, err)
	}
	name := in.Name
	if name == "" {
		name = in.Filename
	}
	return s.publisher.Upload(ctx, UploadInput{
		CategoryID:     cat.ID,
		DatasetGroupID: &g.ID,
		Name:           name,
		Filename:       in.Filename,
		FileType:       domain.FileTypeRaster,
		ContentType:    in.ContentType,
		Size:           in.Size,
		Reader:         in.Reader,
		StartDate:      in.StartDate,
		EndDate:        in.EndDate,
	})
}

// CreateRun validates the params and overrides, persists a queued run with one
// pending node per graph node, and publishes the worker job.
func (s *LandscapeService) CreateRun(ctx context.Context, in RunInput) (domain.LPRun, error) {
	params := in.Params.WithDefaults()
	if err := params.Validate(); err != nil {
		return domain.LPRun{}, fmt.Errorf("%w: %s", domain.ErrValidation, err)
	}
	if in.FromStage != nil {
		if *in.FromStage < 0 || *in.FromStage > maxStage() {
			return domain.LPRun{}, fmt.Errorf("%w: from_stage must be in 0..%d", domain.ErrValidation, maxStage())
		}
	}

	overrides := map[string]uuid.UUID{}
	for group, id := range in.Overrides {
		if !knownGroup(group) {
			return domain.LPRun{}, fmt.Errorf("%w: unknown landscape group %q in overrides", domain.ErrValidation, group)
		}
		d, err := s.datasets.GetByID(ctx, id)
		if err != nil {
			if errors.Is(err, domain.ErrNotFound) {
				return domain.LPRun{}, fmt.Errorf("%w: override for %q: dataset %s does not exist", domain.ErrValidation, group, id)
			}
			return domain.LPRun{}, err
		}
		if d.FileType != domain.FileTypeRaster {
			return domain.LPRun{}, fmt.Errorf("%w: override for %q: dataset %s is not a raster", domain.ErrValidation, group, id)
		}
		if d.Status != domain.DatasetStatusReady {
			return domain.LPRun{}, fmt.Errorf("%w: override for %q: dataset %s is not ready", domain.ErrValidation, group, id)
		}
		overrides[group] = id
	}

	// lp_runs.params is NOT NULL, and the kernels misbehave on a partially
	// filled Params, so the defaulted-and-validated value is what we persist.
	paramsJSON, err := json.Marshal(params)
	if err != nil {
		return domain.LPRun{}, err
	}

	nodes := make([]domain.LPRunNode, 0, len(landscape.Nodes))
	for _, n := range landscape.Nodes {
		nodes = append(nodes, domain.LPRunNode{NodeCode: n.Code, Stage: n.Stage, Status: domain.LPNodePending})
	}

	run, err := s.runs.CreateRun(ctx, domain.LPRun{
		Status:    domain.LPRunQueued,
		FromStage: in.FromStage,
		Params:    paramsJSON,
		Overrides: overrides,
		Nodes:     nodes,
	})
	if err != nil {
		return domain.LPRun{}, err
	}

	if err := s.jobs.EnqueueRun(ctx, run.ID); err != nil {
		msg := fmt.Sprintf("failed to enqueue: %s", err)
		if setErr := s.runs.SetRunStatus(ctx, run.ID, domain.LPRunFailed, &msg); setErr != nil {
			s.log.Error("mark landscape run failed", "run_id", run.ID, "error", setErr)
		}
		return domain.LPRun{}, fmt.Errorf("enqueue landscape run: %w", err)
	}
	return run, nil
}

// GetRun returns one run with its nodes.
func (s *LandscapeService) GetRun(ctx context.Context, id uuid.UUID) (domain.LPRun, error) {
	return s.runs.GetRun(ctx, id)
}

// ListRuns returns a page of runs, newest first.
func (s *LandscapeService) ListRuns(ctx context.Context, page, pageSize int) (LPRunPage, error) {
	if page < 1 {
		page = 1
	}
	if pageSize < 1 {
		pageSize = DefaultPageSize
	}
	if pageSize > MaxPageSize {
		pageSize = MaxPageSize
	}
	items, err := s.runs.ListRuns(ctx, pageSize, (page-1)*pageSize)
	if err != nil {
		return LPRunPage{}, err
	}
	total, err := s.runs.CountRuns(ctx)
	if err != nil {
		return LPRunPage{}, err
	}
	return LPRunPage{Items: items, Page: page, PageSize: pageSize, Total: total}, nil
}

// Results lists every output group with its freshest ready dataset and the URLs
// to render it.
func (s *LandscapeService) Results(ctx context.Context) ([]ResultInfo, error) {
	ready := domain.DatasetStatusReady
	fileType := domain.FileTypeRaster

	out := make([]ResultInfo, 0, len(landscape.Outputs))
	for _, o := range landscape.Outputs {
		g, err := s.groups.GetByCode(ctx, o.Code)
		if err != nil {
			return nil, fmt.Errorf("dataset group %q: %w", o.Code, err)
		}
		info := ResultInfo{Group: o.Code, Title: o.Title, GroupID: g.ID}
		if node, ok := landscape.NodeForOutput(o.Code); ok {
			info.Node = node.Code
		}

		latest, err := s.datasets.LatestInGroup(ctx, g.ID, &ready, &fileType)
		switch {
		case err == nil:
			summary := summaryOf(latest)
			info.Latest = &summary
			cog := "/datasets/" + latest.ID.String() + ".cog"
			info.CogURL = &cog
			if g.SldStorageKey != nil {
				sld := "/dataset-groups/" + g.ID.String() + "/sld"
				info.SldURL = &sld
			}
		case errors.Is(err, domain.ErrNotFound):
			// Nothing computed for this group yet.
		default:
			return nil, err
		}
		out = append(out, info)
	}
	return out, nil
}

// MarkRunFailed records a terminal failure on a run so it does not stay stuck
// in an in-progress status.
func (s *LandscapeService) MarkRunFailed(ctx context.Context, runID uuid.UUID, reason string) error {
	wctx, cancel := terminalCtx(ctx)
	defer cancel()
	return s.runs.SetRunStatus(wctx, runID, domain.LPRunFailed, &reason)
}

// Execute runs the whole graph for one run: stages in order, nodes within a
// stage concurrently. The first failing node fails the run and later stages are
// not started. It is the worker entry point.
func (s *LandscapeService) Execute(ctx context.Context, runID uuid.UUID) error {
	run, err := s.runs.GetRun(ctx, runID)
	if err != nil {
		return err
	}
	// A redelivered or stale message must not rerun a finished run. "running"
	// is allowed through so a run whose worker crashed mid-flight still
	// recovers on redelivery.
	if run.Status != domain.LPRunQueued && run.Status != domain.LPRunRunning {
		s.log.Info("landscape run already terminal, ignoring redelivery", "run_id", runID, "status", run.Status)
		return nil
	}

	var params landscape.Params
	if len(run.Params) > 0 {
		if err := json.Unmarshal(run.Params, &params); err != nil {
			return s.failRun(ctx, runID, fmt.Errorf("decode run params: %w", err))
		}
	}
	params = params.WithDefaults()

	cat, err := s.categories.GetByCode(ctx, landscape.CategoryCode)
	if err != nil {
		return s.failRun(ctx, runID, fmt.Errorf("category %q: %w", landscape.CategoryCode, err))
	}

	if err := s.runs.SetRunStatus(ctx, runID, domain.LPRunRunning, nil); err != nil {
		return err
	}
	// Re-read so started_at is available for result dataset names; on failure
	// keep the pre-transition copy and fall back to time.Now() for the names.
	if started, err := s.runs.GetRun(ctx, runID); err == nil {
		run = started
	} else {
		s.log.Warn("re-read landscape run after marking it running", "run_id", runID, "error", err)
	}

	state := &runState{produced: map[string]uuid.UUID{}}
	for _, stage := range landscape.Stages() {
		// WithContext so the first failing node cancels its siblings: Compute
		// honours cancellation, so a doomed stage stops reading rasters
		// instead of running to completion. Terminal status writes use
		// terminalCtx, so they still land on the cancelled context.
		g, gctx := errgroup.WithContext(ctx)
		// Each node's Compute spins its own worker pool (each worker holding a
		// window buffer per input band), so peak memory is the product of the
		// two limits. Bound the node fan-out here and split the worker budget
		// over it so that product stays at opts.MaxWorkers.
		g.SetLimit(s.opts.MaxConcurrentNodes)
		for _, node := range stage {
			g.Go(func() error { return s.runNode(gctx, run, node, params, cat, state) })
		}
		// Wait for every node of the stage: each one records its own outcome,
		// and a sibling's failure must not leave a node half-recorded.
		if err := g.Wait(); err != nil {
			return s.failRun(ctx, runID, err)
		}
	}

	return s.runs.SetRunStatus(ctx, runID, domain.LPRunSucceeded, nil)
}

// terminalCtx derives a bounded context that outlives the cancellation of ctx.
// Terminal status writes must happen even when the run failed *because* the
// context was cancelled (shutdown, a sibling node failing the stage); on the
// original context they would be refused and leave the run stuck in "running".
func terminalCtx(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.WithoutCancel(ctx), terminalWriteTimeout)
}

// failRun marks the run failed with cause's message and returns cause.
func (s *LandscapeService) failRun(ctx context.Context, runID uuid.UUID, cause error) error {
	msg := cause.Error()
	wctx, cancel := terminalCtx(ctx)
	defer cancel()
	if err := s.runs.SetRunStatus(wctx, runID, domain.LPRunFailed, &msg); err != nil {
		s.log.Error("mark landscape run failed", "run_id", runID, "error", err)
	}
	return cause
}

// runState carries what the run has produced so far, shared by the concurrent
// nodes of a stage.
type runState struct {
	mu       sync.Mutex
	produced map[string]uuid.UUID
}

func (r *runState) get(group string) (uuid.UUID, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	id, ok := r.produced[group]
	return id, ok
}

func (r *runState) record(refs []domain.LPDatasetRef) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, ref := range refs {
		r.produced[ref.Group] = ref.DatasetID
	}
}

// runNode resolves a node's inputs, reuses a previous result when the
// fingerprint matches, and otherwise computes and publishes its outputs.
func (s *LandscapeService) runNode(ctx context.Context, run domain.LPRun, node landscape.Node, params landscape.Params, cat domain.Category, state *runState) error {
	refs, inputs, err := s.resolveInputs(ctx, run, node, state)
	if err != nil {
		return s.finishNodeFailed(ctx, run.ID, node, err)
	}

	fp := fingerprint(node, refs, params)
	if err := s.runs.SetNodeStarted(ctx, run.ID, node.Code, fp, refs); err != nil {
		// Best-effort, so the node is not left pending while the run is failed.
		return s.finishNodeFailed(ctx, run.ID, node, err)
	}

	if run.FromStage == nil || node.Stage < *run.FromStage {
		reused, ok, err := s.reusableOutputs(ctx, run.ID, node, fp)
		if err != nil {
			return s.finishNodeFailed(ctx, run.ID, node, err)
		}
		if ok {
			state.record(reused)
			return s.runs.SetNodeFinished(ctx, run.ID, node.Code, domain.LPNodeSkipped, reused, nil)
		}
	}

	outputs, err := s.computeNode(ctx, run, node, params, cat, refs, inputs)
	if err != nil {
		return s.finishNodeFailed(ctx, run.ID, node, err)
	}
	state.record(outputs)
	if err := s.runs.SetNodeFinished(ctx, run.ID, node.Code, domain.LPNodeSucceeded, outputs, nil); err != nil {
		// Best-effort, so the node is not left running while the run is failed.
		return s.finishNodeFailed(ctx, run.ID, node, err)
	}
	return nil
}

// finishNodeFailed records the failure on the node and returns the cause.
func (s *LandscapeService) finishNodeFailed(ctx context.Context, runID uuid.UUID, node landscape.Node, cause error) error {
	msg := cause.Error()
	wctx, cancel := terminalCtx(ctx)
	defer cancel()
	if err := s.runs.SetNodeFinished(wctx, runID, node.Code, domain.LPNodeFailed, nil, &msg); err != nil {
		s.log.Error("mark landscape node failed", "run_id", runID, "node", node.Code, "error", err)
	}
	return cause
}

// resolveInputs picks one dataset per input group: an explicit override first,
// then whatever this run already produced for the group, then the freshest
// ready raster of the group.
func (s *LandscapeService) resolveInputs(ctx context.Context, run domain.LPRun, node landscape.Node, state *runState) ([]domain.LPDatasetRef, []domain.Dataset, error) {
	ready := domain.DatasetStatusReady
	fileType := domain.FileTypeRaster

	refs := make([]domain.LPDatasetRef, 0, len(node.Inputs))
	datasets := make([]domain.Dataset, 0, len(node.Inputs))

	for _, group := range node.Inputs {
		var (
			d   domain.Dataset
			err error
		)
		switch id, ok := s.pinnedInput(run, group, state); {
		case ok:
			d, err = s.datasets.GetByID(ctx, id)
		default:
			g, gerr := s.groups.GetByCode(ctx, group)
			if gerr != nil {
				return nil, nil, fmt.Errorf("node %s: dataset group %q: %w", node.Code, group, gerr)
			}
			d, err = s.datasets.LatestInGroup(ctx, g.ID, &ready, &fileType)
		}
		if err != nil {
			if errors.Is(err, domain.ErrNotFound) {
				return nil, nil, fmt.Errorf("node %s: no ready raster dataset in group %q", node.Code, group)
			}
			return nil, nil, err
		}
		refs = append(refs, domain.LPDatasetRef{Group: group, DatasetID: d.ID})
		datasets = append(datasets, d)
	}
	return refs, datasets, nil
}

// pinnedInput returns the dataset already decided for a group: the run's
// override, or what an earlier node of this run produced.
func (s *LandscapeService) pinnedInput(run domain.LPRun, group string, state *runState) (uuid.UUID, bool) {
	if id, ok := run.Overrides[group]; ok {
		return id, true
	}
	return state.get(group)
}

// reusableOutputs reports whether a previous succeeded node with the same
// fingerprint left outputs that all still exist and are ready.
func (s *LandscapeService) reusableOutputs(ctx context.Context, runID uuid.UUID, node landscape.Node, fp string) ([]domain.LPDatasetRef, bool, error) {
	refs, err := s.runs.FindReusableOutputs(ctx, node.Code, fp, runID)
	if err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return nil, false, nil
		}
		return nil, false, err
	}
	if len(refs) != len(node.Outputs) {
		return nil, false, nil
	}
	for _, ref := range refs {
		d, err := s.datasets.GetByID(ctx, ref.DatasetID)
		if err != nil {
			if errors.Is(err, domain.ErrNotFound) {
				return nil, false, nil
			}
			return nil, false, err
		}
		if d.Status != domain.DatasetStatusReady {
			return nil, false, nil
		}
	}
	return refs, true, nil
}

// computeNode downloads the node's inputs into a temp dir, computes it, and
// publishes one raster dataset per output group. Temp files are always removed.
func (s *LandscapeService) computeNode(
	ctx context.Context,
	run domain.LPRun,
	node landscape.Node,
	params landscape.Params,
	cat domain.Category,
	refs []domain.LPDatasetRef,
	inputs []domain.Dataset,
) ([]domain.LPDatasetRef, error) {
	// landscape.Compute does not create outDir, so we own the whole directory.
	dir, err := os.MkdirTemp("", "lp-"+node.Code+"-*")
	if err != nil {
		return nil, fmt.Errorf("node %s: temp dir: %w", node.Code, err)
	}
	defer os.RemoveAll(dir)

	paths := make(map[string]string, len(inputs))
	for i, d := range inputs {
		group := refs[i].Group
		// Downstream nodes read the original object, never the COG, so they
		// never wait for the conversion job.
		path := filepath.Join(dir, "in-"+group+".tif")
		if err := s.download(ctx, d.StorageKey, path); err != nil {
			return nil, fmt.Errorf("node %s: download %q: %w", node.Code, group, err)
		}
		paths[group] = path
	}

	outPaths, err := s.computeWithWorkers(ctx, s.rio, node, paths, dir, params, s.opts.workersPerNode())
	if err != nil {
		return nil, err
	}

	start, end := periodOf(inputs)
	date := time.Now()
	if run.StartedAt != nil {
		date = *run.StartedAt
	}

	meta, err := json.Marshal(map[string]any{
		"lp_run_id": run.ID,
		"lp_node":   node.Code,
		"inputs":    refs,
	})
	if err != nil {
		return nil, err
	}

	outputs := make([]domain.LPDatasetRef, 0, len(node.Outputs))
	for _, group := range node.Outputs {
		path, ok := outPaths[group]
		if !ok || path == "" {
			return nil, fmt.Errorf("node %s: compute produced no output for group %q", node.Code, group)
		}
		g, err := s.groups.GetByCode(ctx, group)
		if err != nil {
			return nil, fmt.Errorf("node %s: dataset group %q: %w", node.Code, group, err)
		}
		title := group
		if o, ok := landscape.OutputByCode(group); ok {
			title = o.Title
		}
		d, err := s.publisher.CreateRasterFromFile(ctx, CreateRasterInput{
			CategoryID:     cat.ID,
			DatasetGroupID: &g.ID,
			Name:           title + " " + date.Format("2006-01-02"),
			Filename:       group + ".tif",
			Path:           path,
			StartDate:      start,
			EndDate:        end,
			Meta:           meta,
			Automated:      true,
		})
		if err != nil {
			return nil, fmt.Errorf("node %s: publish %q: %w", node.Code, group, err)
		}
		outputs = append(outputs, domain.LPDatasetRef{Group: group, DatasetID: d.ID})
	}
	return outputs, nil
}

// download copies an object from the store to a local path.
func (s *LandscapeService) download(ctx context.Context, key, path string) error {
	obj, err := s.store.Get(ctx, key)
	if err != nil {
		return err
	}
	defer obj.Close()

	f, err := os.Create(path)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, obj); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

// fingerprint is a stable hash of the node, its resolved inputs and the
// node-relevant params.
func fingerprint(node landscape.Node, inputs []domain.LPDatasetRef, p landscape.Params) string {
	sorted := append([]domain.LPDatasetRef(nil), inputs...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Group < sorted[j].Group })
	payload, _ := json.Marshal(struct {
		Node   string                `json:"node"`
		Inputs []domain.LPDatasetRef `json:"inputs"`
		Params landscape.Params      `json:"params"`
	}{node.Code, sorted, p.ForNode(node.Code)})
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

// periodOf returns the min start_date and max end_date over the inputs, or nil
// when every input leaves the respective bound unset.
func periodOf(inputs []domain.Dataset) (*string, *string) {
	var start, end *string
	for _, d := range inputs {
		if d.StartDate != nil && (start == nil || *d.StartDate < *start) {
			start = d.StartDate
		}
		if d.EndDate != nil && (end == nil || *d.EndDate > *end) {
			end = d.EndDate
		}
	}
	return start, end
}

// knownGroup reports whether a group code names an input slot or a node output.
func knownGroup(code string) bool {
	if _, ok := landscape.SlotByCode(code); ok {
		return true
	}
	_, ok := landscape.OutputByCode(code)
	return ok
}

// maxStage returns the highest stage number in the graph.
func maxStage() int {
	highest := 0
	for _, n := range landscape.Nodes {
		if n.Stage > highest {
			highest = n.Stage
		}
	}
	return highest
}

// summaryOf projects a dataset onto the summary shape used in list responses.
func summaryOf(d domain.Dataset) domain.DatasetSummary {
	return domain.DatasetSummary{
		ID:             d.ID,
		CategoryID:     d.CategoryID,
		DatasetGroupID: d.DatasetGroupID,
		StartDate:      d.StartDate,
		EndDate:        d.EndDate,
		Name:           d.Name,
		Description:    d.Description,
		Unit:           d.Unit,
		FileType:       d.FileType,
		SizeBytes:      d.SizeBytes,
		Status:         d.Status,
		CreatedAt:      d.CreatedAt,
		UpdatedAt:      d.UpdatedAt,
	}
}
