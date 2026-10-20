package landscape

import (
	"context"
	"fmt"
	"sync"

	"gis/internal/raster"
)

// fakeIO keeps rasters in memory keyed by path.
type fakeIO struct {
	mu    sync.Mutex
	files map[string]*fakeRaster

	// failReadAfter, when set for a path, makes every ReadFloat64 call
	// against that path fail once the cumulative read count (across all
	// readers/workers) exceeds the configured threshold. Used to simulate a
	// mid-run I/O failure.
	failReadAfter map[string]int
	readCounts    map[string]int

	// opens counts every successful Open. Each pool worker opens one reader
	// per input, so the count bounds the number of concurrent handles.
	opens int
}

// openCount returns how many readers have been opened so far.
func (f *fakeIO) openCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.opens
}

type fakeRaster struct {
	grid raster.Grid
	data []float64 // row-major
	opts raster.WriterOptions
}

func newFakeIO() *fakeIO { return &fakeIO{files: map[string]*fakeRaster{}} }

func (f *fakeIO) put(path string, g raster.Grid, data []float64) {
	f.files[path] = &fakeRaster{grid: g, data: data}
}

// failReadAfterN makes ReadFloat64 against path fail starting from the
// (n+1)-th call to it.
func (f *fakeIO) failReadAfterN(path string, n int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failReadAfter == nil {
		f.failReadAfter = map[string]int{}
		f.readCounts = map[string]int{}
	}
	f.failReadAfter[path] = n
}

type fakeReader struct {
	f    *fakeIO
	path string
	r    *fakeRaster
}

func (r *fakeReader) Grid() raster.Grid       { return r.r.grid }
func (r *fakeReader) NoData() (float64, bool) { return -9999, true }
func (r *fakeReader) Close() error            { return nil }
func (r *fakeReader) ReadFloat64(w raster.Window, dst []float64) error {
	if r.f != nil {
		r.f.mu.Lock()
		if n, ok := r.f.failReadAfter[r.path]; ok {
			r.f.readCounts[r.path]++
			if r.f.readCounts[r.path] > n {
				r.f.mu.Unlock()
				return fmt.Errorf("fake: simulated read failure for %s", r.path)
			}
		}
		r.f.mu.Unlock()
	}
	for y := 0; y < w.Height; y++ {
		copy(dst[y*w.Width:(y+1)*w.Width], r.r.data[(w.Y+y)*r.r.grid.Width+w.X:(w.Y+y)*r.r.grid.Width+w.X+w.Width])
	}
	return nil
}

type fakeWriter struct {
	f *fakeIO
	r *fakeRaster
}

func (w *fakeWriter) WriteInt16(win raster.Window, src []int16) error {
	w.f.mu.Lock()
	defer w.f.mu.Unlock()
	for y := 0; y < win.Height; y++ {
		for x := 0; x < win.Width; x++ {
			w.r.data[(win.Y+y)*w.r.grid.Width+win.X+x] = float64(src[y*win.Width+x])
		}
	}
	return nil
}
func (w *fakeWriter) Close() error { return nil }

func (f *fakeIO) Open(_ context.Context, path string) (raster.Reader, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	r, ok := f.files[path]
	if !ok {
		return nil, fmt.Errorf("fake: %s not found", path)
	}
	f.opens++
	return &fakeReader{f: f, path: path, r: r}, nil
}

func (f *fakeIO) Create(_ context.Context, path string, g raster.Grid, o raster.WriterOptions) (raster.Writer, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	r := &fakeRaster{grid: g, data: make([]float64, g.Width*g.Height), opts: o}
	f.files[path] = r
	return &fakeWriter{f: f, r: r}, nil
}

func (f *fakeIO) SameGrid(a, b raster.Grid) bool { return a == b }
