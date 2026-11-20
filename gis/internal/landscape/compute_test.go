package landscape

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"gis/internal/raster"
)

func grid(w, h int) raster.Grid {
	return raster.Grid{Width: w, Height: h, GeoTransform: [6]float64{0, 30, 0, 0, 0, -30}, ProjectionWKT: "EPSG:32642"}
}

func constRaster(w, h int, v float64) []float64 {
	d := make([]float64, w*h)
	for i := range d {
		d[i] = v
	}
	return d
}

func TestCompute_E_TilesWindowsAndWritesEveryPixel(t *testing.T) {
	fio := newFakeIO()
	g := grid(10, 7)
	z := constRaster(10, 7, 2)
	u := constRaster(10, 7, 4)
	z[0] = -9999 // pixel 0 invalid
	fio.put("z.tif", g, z)
	fio.put("u.tif", g, u)
	node, _ := NodeByCode("10")
	p := DefaultParams()
	p.WindowSize = 4 // forces partial windows (10x7 with 4x4 tiles)
	out, err := Compute(context.Background(), fio, node, map[string]string{OutZIntegrated: "z.tif", OutUIntegrated: "u.tif"}, "/out", p)
	if err != nil {
		t.Fatal(err)
	}
	path := out[OutE]
	if path != filepath.Join("/out", OutE+".tif") {
		t.Fatalf("unexpected output path %q", path)
	}
	res := fio.files[path]
	if res.grid != g {
		t.Fatal("output grid must copy the first input's grid")
	}
	if res.opts != raster.DefaultWriterOptions() {
		t.Fatalf("output created with %+v, want raster.DefaultWriterOptions() = %+v", res.opts, raster.DefaultWriterOptions())
	}
	for i, v := range res.data {
		want := 4.0
		if i == 0 {
			want = -9999
		}
		if v != want {
			t.Fatalf("pixel %d = %v want %v", i, v, want)
		}
	}
}

func TestCompute_FailsOnGridMismatch(t *testing.T) {
	fio := newFakeIO()
	fio.put("z.tif", grid(10, 7), constRaster(10, 7, 2))
	fio.put("u.tif", grid(9, 7), constRaster(9, 7, 4))
	node, _ := NodeByCode("10")
	_, err := Compute(context.Background(), fio, node, map[string]string{OutZIntegrated: "z.tif", OutUIntegrated: "u.tif"}, "/out", DefaultParams())
	if err == nil {
		t.Fatal("expected grid mismatch error")
	}
	if _, created := fio.files[filepath.Join("/out", OutE+".tif")]; created {
		t.Fatal("no output must be created on mismatch")
	}
}

func TestCompute_FailsOnMissingInput(t *testing.T) {
	fio := newFakeIO()
	fio.put("z.tif", grid(2, 2), constRaster(2, 2, 2))
	node, _ := NodeByCode("10")
	if _, err := Compute(context.Background(), fio, node, map[string]string{OutZIntegrated: "z.tif"}, "/out", DefaultParams()); err == nil {
		t.Fatal("expected error for missing input group")
	}
}

func TestCompute_MultiOutputNode(t *testing.T) {
	fio := newFakeIO()
	g := grid(3, 1)
	paths := map[string]string{}
	for i, s := range []string{SlotPIrrigation, SlotPSnow, SlotPCrop, SlotPPasture, SlotPTourism} {
		paths[s] = s + ".tif"
		fio.put(paths[s], g, constRaster(3, 1, float64(i+1)))
	}
	paths[OutE] = "e.tif"
	fio.put("e.tif", g, constRaster(3, 1, 3))
	node, _ := NodeByCode("20")
	out, err := Compute(context.Background(), fio, node, paths, "/o", DefaultParams())
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 5 {
		t.Fatalf("want 5 outputs, got %d", len(out))
	}
	if fio.files[out[OutCTourism]].data[0] != 3 || fio.files[out[OutCIrrigation]].data[0] != 1 {
		t.Fatal("min(P, E) not applied per output")
	}
}

// TestCompute_MultiOutputNode_TiledWindows runs the 6-input/5-output node 20
// with a window size much smaller than the raster, so per-worker output
// buffers are reused across windows of differing pixel counts (partial
// windows at the raster's right/bottom edges).
func TestCompute_MultiOutputNode_TiledWindows(t *testing.T) {
	fio := newFakeIO()
	w, h := 5, 3
	g := grid(w, h)
	paths := map[string]string{}
	for i, s := range []string{SlotPIrrigation, SlotPSnow, SlotPCrop, SlotPPasture, SlotPTourism} {
		paths[s] = s + ".tif"
		fio.put(paths[s], g, constRaster(w, h, float64(i+1)))
	}
	paths[OutE] = "e.tif"
	fio.put("e.tif", g, constRaster(w, h, 3))
	node, _ := NodeByCode("20")
	p := DefaultParams()
	p.WindowSize = 2 // 5x3 with 2x2 tiles: several differently-sized windows
	out, err := Compute(context.Background(), fio, node, paths, "/o2", p)
	if err != nil {
		t.Fatal(err)
	}
	codes := []string{OutCIrrigation, OutCSnow, OutCCrop, OutCPasture, OutCTourism}
	for i, code := range codes {
		want := min(i+1, 3) // min(P_sector, E=3)
		res := fio.files[out[code]]
		for px, v := range res.data {
			if v != float64(want) {
				t.Fatalf("%s pixel %d = %v want %v", code, px, v, want)
			}
		}
	}
}

// TestCompute_ZeroParamsUsesDefaults exercises Compute with a completely
// zero-valued Params, relying on WithDefaults to fill in WindowSize and the
// other node-independent knobs.
func TestCompute_ZeroParamsUsesDefaults(t *testing.T) {
	fio := newFakeIO()
	g := grid(4, 4)
	fio.put("z.tif", g, constRaster(4, 4, 2))
	fio.put("u.tif", g, constRaster(4, 4, 3))
	node, _ := NodeByCode("10")
	out, err := Compute(context.Background(), fio, node, map[string]string{OutZIntegrated: "z.tif", OutUIntegrated: "u.tif"}, "/z", Params{})
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range fio.files[out[OutE]].data {
		if v != 3 {
			t.Fatalf("pixel = %v want 3", v)
		}
	}
}

// TestCompute_NegativeWindowSizeClampedToDefault guards against
// raster.Windows looping forever on a non-positive window size: WithDefaults
// only fills in a zero value, so Compute must clamp negatives itself.
func TestCompute_NegativeWindowSizeClampedToDefault(t *testing.T) {
	fio := newFakeIO()
	g := grid(2, 2)
	fio.put("z.tif", g, constRaster(2, 2, 2))
	fio.put("u.tif", g, constRaster(2, 2, 3))
	node, _ := NodeByCode("10")
	p := DefaultParams()
	p.WindowSize = -5
	out, err := Compute(context.Background(), fio, node, map[string]string{OutZIntegrated: "z.tif", OutUIntegrated: "u.tif"}, "/neg", p)
	if err != nil {
		t.Fatal(err)
	}
	if fio.files[out[OutE]].data[0] != 3 {
		t.Fatal("unexpected result")
	}
}

// TestCompute_FailureMidRunReturnsError simulates a read failure partway
// through a run (the fake fails every ReadFloat64 against u.tif from the
// first call on) and checks Compute surfaces the error instead of returning
// a partial result.
func TestCompute_FailureMidRunReturnsError(t *testing.T) {
	fio := newFakeIO()
	g := grid(10, 7)
	fio.put("z.tif", g, constRaster(10, 7, 2))
	fio.put("u.tif", g, constRaster(10, 7, 4))
	fio.failReadAfterN("u.tif", 0) // fail starting from the very first read
	node, _ := NodeByCode("10")
	p := DefaultParams()
	p.WindowSize = 4
	if _, err := Compute(context.Background(), fio, node, map[string]string{OutZIntegrated: "z.tif", OutUIntegrated: "u.tif"}, "/out-fail", p); err == nil {
		t.Fatal("expected error from simulated read failure")
	}
}

// TestRemoveOutputs_DeletesFiles unit-tests the cleanup helper Compute uses
// after a failed run, against the real filesystem (the fake IO above has no
// on-disk files to assert against).
func TestRemoveOutputs_DeletesFiles(t *testing.T) {
	dir := t.TempDir()
	var paths []string
	for i := 0; i < 3; i++ {
		p := filepath.Join(dir, fmt.Sprintf("out%d.tif", i))
		if err := os.WriteFile(p, []byte("data"), 0o644); err != nil {
			t.Fatal(err)
		}
		paths = append(paths, p)
	}
	removeOutputs(paths)
	for _, p := range paths {
		if _, err := os.Stat(p); !os.IsNotExist(err) {
			t.Fatalf("expected %s to be removed, stat err = %v", p, err)
		}
	}
}

// TestComputeWithWorkers_RespectsCap checks the caller's worker budget bounds
// the pool: each pool worker opens one reader per input and holds a buffer per
// input and output, so the open count bounds peak memory. checkGrids opens one
// extra set of handles before the pool starts, hence the (workers+1) bound.
func TestComputeWithWorkers_RespectsCap(t *testing.T) {
	node, _ := NodeByCode("10")
	inputs := len(node.Inputs)

	for _, workers := range []int{1, 2, 4, 0, -3} {
		t.Run(fmt.Sprintf("workers=%d", workers), func(t *testing.T) {
			fio := newFakeIO()
			g := grid(64, 64)
			fio.put("z.tif", g, constRaster(64, 64, 2))
			fio.put("u.tif", g, constRaster(64, 64, 4))
			p := DefaultParams()
			p.WindowSize = 8 // 64 windows, so the pool is never window-bound

			if _, err := ComputeWithWorkers(context.Background(), fio, node,
				map[string]string{OutZIntegrated: "z.tif", OutUIntegrated: "u.tif"}, "/out", p, workers); err != nil {
				t.Fatal(err)
			}

			effective := max(1, workers)
			if got, limit := fio.openCount(), (effective+1)*inputs; got > limit {
				t.Fatalf("opened %d readers with workers=%d, want at most %d", got, workers, limit)
			}
			if fio.openCount() < inputs {
				t.Fatalf("opened %d readers, want at least one set of %d", fio.openCount(), inputs)
			}
		})
	}
}
