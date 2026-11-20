package raster

import (
	"context"
	"fmt"
	"math"
	"sync"

	"github.com/airbusgeo/godal"
)

var registerOnce sync.Once

// GodalIO implements IO with the GDAL bindings (github.com/airbusgeo/godal).
// GDAL datasets are not goroutine-safe: callers must not share a Reader or
// Writer between goroutines without external synchronisation.
type GodalIO struct{}

// NewGodalIO registers GDAL drivers (once) and returns a GodalIO.
func NewGodalIO() *GodalIO {
	registerOnce.Do(godal.RegisterAll)
	return &GodalIO{}
}

// DefaultWriterOptions returns the Landscape Program output profile.
func DefaultWriterOptions() WriterOptions {
	return WriterOptions{NoData: -9999, TileSize: 512, Compression: "DEFLATE"}
}

type godalReader struct {
	ds   *godal.Dataset
	grid Grid
}

func (g *GodalIO) Open(_ context.Context, path string) (Reader, error) {
	ds, err := godal.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open raster %s: %w", path, err)
	}
	st := ds.Structure()
	if st.NBands < 1 {
		ds.Close()
		return nil, fmt.Errorf("open raster %s: no bands", path)
	}
	gt, err := ds.GeoTransform()
	if err != nil {
		ds.Close()
		return nil, fmt.Errorf("open raster %s: geotransform: %w", path, err)
	}
	return &godalReader{ds: ds, grid: Grid{Width: st.SizeX, Height: st.SizeY, GeoTransform: gt, ProjectionWKT: ds.Projection()}}, nil
}

func (r *godalReader) Grid() Grid { return r.grid }

func (r *godalReader) NoData() (float64, bool) { return r.ds.Bands()[0].NoData() }

func (r *godalReader) ReadFloat64(w Window, dst []float64) error {
	if len(dst) < w.Width*w.Height {
		return fmt.Errorf("read window: dst has %d elements, need %d", len(dst), w.Width*w.Height)
	}
	return r.ds.Bands()[0].Read(w.X, w.Y, dst[:w.Width*w.Height], w.Width, w.Height)
}

func (r *godalReader) Close() error { return r.ds.Close() }

type godalWriter struct{ ds *godal.Dataset }

func (g *GodalIO) Create(_ context.Context, path string, grid Grid, o WriterOptions) (Writer, error) {
	return createGTiff(path, grid, o, "IF_SAFER")
}

// createGTiff writes a tiled Int16 GeoTIFF. bigTIFF is the value of the
// BIGTIFF creation option ("IF_SAFER" in production); it is a parameter so
// tests can force a BigTIFF header.
func createGTiff(path string, grid Grid, o WriterOptions, bigTIFF string) (Writer, error) {
	tile := fmt.Sprint(o.TileSize)
	ds, err := godal.Create(godal.GTiff, path, 1, godal.Int16, grid.Width, grid.Height,
		godal.CreationOption("TILED=YES", "BLOCKXSIZE="+tile, "BLOCKYSIZE="+tile,
			"COMPRESS="+o.Compression, "PREDICTOR=2", "BIGTIFF="+bigTIFF))
	if err != nil {
		return nil, fmt.Errorf("create raster %s: %w", path, err)
	}
	if err := ds.SetGeoTransform(grid.GeoTransform); err != nil {
		ds.Close()
		return nil, fmt.Errorf("create raster %s: geotransform: %w", path, err)
	}
	if grid.ProjectionWKT != "" {
		if err := ds.SetProjection(grid.ProjectionWKT); err != nil {
			ds.Close()
			return nil, fmt.Errorf("create raster %s: projection: %w", path, err)
		}
	}
	if err := ds.Bands()[0].SetNoData(float64(o.NoData)); err != nil {
		ds.Close()
		return nil, fmt.Errorf("create raster %s: nodata: %w", path, err)
	}
	return &godalWriter{ds: ds}, nil
}

func (w *godalWriter) WriteInt16(win Window, src []int16) error {
	if len(src) < win.Width*win.Height {
		return fmt.Errorf("write window: src has %d elements, need %d", len(src), win.Width*win.Height)
	}
	return w.ds.Bands()[0].Write(win.X, win.Y, src[:win.Width*win.Height], win.Width, win.Height)
}

func (w *godalWriter) Close() error { return w.ds.Close() }

// SameGrid reports whether two grids describe the same pixels: equal size,
// geotransform within 1e-9 (absolute), and the same CRS (GDAL IsSame; an
// empty projection only matches an empty projection).
//
// ProjectionWKT is parsed with godal.NewSpatialRef rather than
// NewSpatialRefFromWKT: despite the field name, callers may pass any user
// projection string GDAL's SetProjection accepts (e.g. "EPSG:32642"), not
// only WKT, and NewSpatialRefFromWKT rejects non-WKT input.
func (g *GodalIO) SameGrid(a, b Grid) bool {
	if a.Width != b.Width || a.Height != b.Height {
		return false
	}
	for i := range a.GeoTransform {
		if math.Abs(a.GeoTransform[i]-b.GeoTransform[i]) > 1e-9 {
			return false
		}
	}
	if a.ProjectionWKT == "" || b.ProjectionWKT == "" {
		return a.ProjectionWKT == b.ProjectionWKT
	}
	sa, err := godal.NewSpatialRef(a.ProjectionWKT)
	if err != nil {
		return false
	}
	defer sa.Close()
	sb, err := godal.NewSpatialRef(b.ProjectionWKT)
	if err != nil {
		return false
	}
	defer sb.Close()
	return sa.IsSame(sb)
}
