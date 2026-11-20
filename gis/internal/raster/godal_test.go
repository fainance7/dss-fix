package raster

import (
	"bytes"
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"

	"gis/internal/domain"
)

func TestGodalIO_RoundTrip(t *testing.T) {
	rio := NewGodalIO()
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "out.tif")
	grid := Grid{Width: 10, Height: 7, GeoTransform: [6]float64{100, 30, 0, 200, 0, -30}, ProjectionWKT: "EPSG:32642"}

	w, err := rio.Create(ctx, path, grid, DefaultWriterOptions())
	if err != nil {
		t.Fatal(err)
	}
	full := make([]int16, 10*7)
	for i := range full {
		full[i] = int16(i%6) - 1 // -1..4 (-1 stands in for garbage, not nodata)
	}
	full[0] = -9999
	if err := w.WriteInt16(Window{0, 0, 10, 7}, full); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}

	r, err := rio.Open(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	g := r.Grid()
	if g.Width != 10 || g.Height != 7 || g.GeoTransform != grid.GeoTransform || g.ProjectionWKT == "" {
		t.Fatalf("grid mismatch: %+v", g)
	}
	if nd, ok := r.NoData(); !ok || nd != -9999 {
		t.Fatalf("nodata = %v %v", nd, ok)
	}
	dst := make([]float64, 3*2)
	if err := r.ReadFloat64(Window{X: 8, Y: 5, Width: 2, Height: 2}, dst[:4]); err != nil {
		t.Fatal(err)
	}
	// pixel (8,5) has index 58 -> 58%6-1 = 3 ; (9,5)=59 -> 4 ; (8,6)=68 -> 1 ; (9,6)=69 -> 2
	want := []float64{3, 4, 1, 2}
	for i, v := range want {
		if dst[i] != v {
			t.Fatalf("pixel %d = %v want %v", i, dst[i], v)
		}
	}
	if !rio.SameGrid(g, grid) {
		t.Fatal("SameGrid should be true for identical grids")
	}
	other := grid
	other.GeoTransform[0] += 1
	if rio.SameGrid(g, other) {
		t.Fatal("SameGrid should be false for shifted origin")
	}
}

// TestGodalIO_BigTIFFPassesContentValidation guards the upload magic-byte
// check against the pipeline's own output: GDAL writes a BigTIFF header
// (version 43) whenever BIGTIFF is YES or IF_SAFER kicks in for a large
// raster, and domain.ValidateFileContent must accept it.
func TestGodalIO_BigTIFFPassesContentValidation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "big.tif")
	grid := Grid{Width: 8, Height: 8, GeoTransform: [6]float64{100, 30, 0, 200, 0, -30}, ProjectionWKT: "EPSG:32642"}

	w, err := createGTiff(path, grid, DefaultWriterOptions(), "YES")
	if err != nil {
		t.Fatal(err)
	}
	if err := w.WriteInt16(Window{0, 0, 8, 8}, make([]int16, 8*8)); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}

	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	head := make([]byte, 4)
	if _, err := io.ReadFull(f, head); err != nil {
		t.Fatal(err)
	}
	if !bytes.HasPrefix(head, []byte("II+\x00")) && !bytes.HasPrefix(head, []byte("MM\x00+")) {
		t.Fatalf("BIGTIFF=YES did not produce a BigTIFF header: % x", head)
	}
	if err := domain.ValidateFileContent(".tif", head); err != nil {
		t.Fatalf("ValidateFileContent rejected our own BigTIFF output: %v", err)
	}
}
