package landscape

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"

	"gis/internal/raster"

	"golang.org/x/sync/errgroup"
)

// MaxWorkersPerNode is the per-node worker cap Compute uses. Peak memory is
// roughly nodes x workers x window^2 x (inputs+outputs), so callers that run
// several nodes at once should divide their total worker budget between them
// and call ComputeWithWorkers instead.
const MaxWorkersPerNode = 8

// Compute runs one node over its input rasters with the default per-node
// worker cap (MaxWorkersPerNode). See ComputeWithWorkers.
func Compute(ctx context.Context, rio raster.IO, node Node, inputs map[string]string, outDir string, p Params) (map[string]string, error) {
	return ComputeWithWorkers(ctx, rio, node, inputs, outDir, p, MaxWorkersPerNode)
}

// ComputeWithWorkers runs one node over its input rasters with an explicit
// worker cap. See the package doc for the concurrency model: windows are
// processed by a worker pool of at most workers goroutines; each worker owns
// its own Reader handles (GDAL datasets are not goroutine-safe) and writes are
// serialised per output. Window order does not affect the result. On any
// error, no output files are left behind: ComputeWithWorkers best-effort
// removes every output it created before returning. A workers value below 1 is
// treated as 1.
func ComputeWithWorkers(ctx context.Context, rio raster.IO, node Node, inputs map[string]string, outDir string, p Params, workers int) (map[string]string, error) {
	p = p.WithDefaults()
	if p.WindowSize <= 0 {
		// WithDefaults only fills in a zero value; guard against a negative
		// one too, since raster.Windows would otherwise loop forever.
		p.WindowSize = DefaultParams().WindowSize
	}
	paths := make([]string, len(node.Inputs))
	for i, g := range node.Inputs {
		path, ok := inputs[g]
		if !ok || path == "" {
			return nil, fmt.Errorf("node %s (%s): missing input %q", node.Code, node.Name, g)
		}
		paths[i] = path
	}

	// Validate grids with one set of handles before creating anything.
	grid, err := checkGrids(ctx, rio, node, paths)
	if err != nil {
		return nil, err
	}

	outPaths := make(map[string]string, len(node.Outputs))
	writers := make([]raster.Writer, len(node.Outputs))
	createdPaths := make([]string, 0, len(node.Outputs))
	for j, g := range node.Outputs {
		path := filepath.Join(outDir, g+".tif")
		w, err := rio.Create(ctx, path, grid, raster.DefaultWriterOptions())
		if err != nil {
			closeWriters(writers[:j])
			removeOutputs(createdPaths)
			return nil, fmt.Errorf("node %s: create output %s: %w", node.Code, g, err)
		}
		writers[j] = w
		outPaths[g] = path
		createdPaths = append(createdPaths, path)
	}

	windows := raster.Windows(grid.Width, grid.Height, p.WindowSize)
	// The first window raster.Windows produces is always the largest: it
	// starts at (0,0) so both its width and height are the un-clipped
	// min(WindowSize, dimension). Size per-worker buffers off it instead of
	// WindowSize*WindowSize, which would over-allocate whenever the raster is
	// smaller than one window (the common case for small/test rasters, and
	// for the last row/column of any raster).
	var size int
	if len(windows) > 0 {
		size = windows[0].Width * windows[0].Height
	}
	jobs := make(chan raster.Window)
	var writeMu sync.Mutex
	eg, gctx := errgroup.WithContext(ctx)
	// Cap workers well below GOMAXPROCS too: each worker holds a full set of
	// per-window input/output buffers, and nodes can have many inputs and
	// outputs (e.g. node 20: 6 inputs, 5 outputs), so an unbounded worker
	// count multiplies memory with core count and with concurrently running
	// nodes. The caller's budget is the third bound.
	workers = min(runtime.GOMAXPROCS(0), len(windows), max(1, workers))
	for w := 0; w < workers; w++ {
		eg.Go(func() error {
			readers, err := openAll(gctx, rio, node, paths)
			if err != nil {
				return err
			}
			defer closeReaders(readers)
			in := make([][]float64, len(paths))
			for i := range in {
				in[i] = make([]float64, size)
			}
			out := make([][]int16, len(writers))
			for j := range out {
				out[j] = make([]int16, size)
			}
			for win := range jobs {
				n := win.Width * win.Height
				inView := make([][]float64, len(in))
				for i, r := range readers {
					if err := r.ReadFloat64(win, in[i][:n]); err != nil {
						return fmt.Errorf("node %s: read %s window %+v: %w", node.Code, node.Inputs[i], win, err)
					}
					inView[i] = in[i][:n]
				}
				outView := make([][]int16, len(out))
				for j := range out {
					outView[j] = out[j][:n]
				}
				node.Kernel(inView, outView, p)
				writeMu.Lock()
				for j, wr := range writers {
					if err := wr.WriteInt16(win, outView[j]); err != nil {
						writeMu.Unlock()
						return fmt.Errorf("node %s: write %s window %+v: %w", node.Code, node.Outputs[j], win, err)
					}
				}
				writeMu.Unlock()
			}
			return nil
		})
	}
	eg.Go(func() error {
		defer close(jobs)
		for _, win := range windows {
			select {
			case jobs <- win:
			case <-gctx.Done():
				return gctx.Err()
			}
		}
		return nil
	})
	runErr := eg.Wait()
	if err := closeWriters(writers); err != nil && runErr == nil {
		runErr = err
	}
	if runErr != nil {
		removeOutputs(createdPaths)
		return nil, runErr
	}
	return outPaths, nil
}

// checkGrids opens every input once, verifies all grids match the first, and
// returns that grid.
func checkGrids(ctx context.Context, rio raster.IO, node Node, paths []string) (raster.Grid, error) {
	readers, err := openAll(ctx, rio, node, paths)
	if err != nil {
		return raster.Grid{}, err
	}
	defer closeReaders(readers)
	if len(readers) == 0 {
		return raster.Grid{}, fmt.Errorf("node %s (%s): has no inputs", node.Code, node.Name)
	}
	ref := readers[0].Grid()
	for i := 1; i < len(readers); i++ {
		if g := readers[i].Grid(); !rio.SameGrid(ref, g) {
			return raster.Grid{}, fmt.Errorf("node %s: input %s grid (%dx%d, %v) differs from %s (%dx%d, %v); inputs must be aligned",
				node.Code, node.Inputs[i], g.Width, g.Height, g.GeoTransform, node.Inputs[0], ref.Width, ref.Height, ref.GeoTransform)
		}
	}
	return ref, nil
}

// openAll opens paths in order, wrapping any failure with the node code and
// the input group name (paths is always node.Inputs resolved to files, in
// the same order) for context.
func openAll(ctx context.Context, rio raster.IO, node Node, paths []string) ([]raster.Reader, error) {
	readers := make([]raster.Reader, 0, len(paths))
	for i, p := range paths {
		r, err := rio.Open(ctx, p)
		if err != nil {
			closeReaders(readers)
			name := p
			if i < len(node.Inputs) {
				name = node.Inputs[i]
			}
			return nil, fmt.Errorf("node %s: input %s: %w", node.Code, name, err)
		}
		readers = append(readers, r)
	}
	return readers, nil
}

func closeReaders(rs []raster.Reader) {
	for _, r := range rs {
		if r != nil {
			_ = r.Close()
		}
	}
}

func closeWriters(ws []raster.Writer) error {
	var first error
	for _, w := range ws {
		if w == nil {
			continue
		}
		if err := w.Close(); err != nil && first == nil {
			first = err
		}
	}
	return first
}

// removeOutputs best-effort deletes every path in paths, ignoring errors. It
// is used to leave no partially-written output behind after a failed run.
func removeOutputs(paths []string) {
	for _, p := range paths {
		_ = os.Remove(p)
	}
}
