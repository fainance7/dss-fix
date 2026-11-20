package raster

import "context"

// Grid describes a raster's pixel grid: its size, geotransform, and
// projection. Two grids describe the same pixels when IO.SameGrid reports
// true for them.
type Grid struct {
	Width, Height int
	GeoTransform  [6]float64
	ProjectionWKT string
}

// Window is a rectangular block of pixels within a raster, in pixel/line
// coordinates with (0,0) at the top-left corner.
type Window struct {
	X, Y, Width, Height int
}

// Reader reads pixel data and metadata from an existing raster.
type Reader interface {
	// Grid returns the raster's pixel grid.
	Grid() Grid
	// NoData returns the raster's nodata value and whether one is set.
	NoData() (float64, bool)
	// ReadFloat64 reads the pixels in w into dst, row-major, converting the
	// underlying pixel type to float64. len(dst) must be at least
	// w.Width*w.Height.
	ReadFloat64(w Window, dst []float64) error
	// Close releases the underlying dataset handle.
	Close() error
}

// WriterOptions configures a raster created by IO.Create.
type WriterOptions struct {
	// NoData is the nodata value written to the output band.
	NoData int16
	// TileSize is the internal tile size (both block dimensions) in pixels.
	TileSize int
	// Compression is the GDAL COMPRESS creation option value.
	Compression string
}

// Writer writes pixel data to a raster created by IO.Create.
type Writer interface {
	// WriteInt16 writes src, row-major, into the pixels described by w.
	// len(src) must be at least w.Width*w.Height.
	WriteInt16(w Window, src []int16) error
	// Close flushes and releases the underlying dataset handle.
	Close() error
}

// IO is an engine-agnostic block I/O abstraction over raster storage. It lets
// callers open and create rasters without depending on a particular GDAL
// binding.
type IO interface {
	// Open opens the raster at path for reading.
	Open(ctx context.Context, path string) (Reader, error)
	// Create creates a new single-band int16 raster at path with the given
	// grid and writer options, ready for WriteInt16 calls.
	Create(ctx context.Context, path string, g Grid, o WriterOptions) (Writer, error)
	// SameGrid reports whether a and b describe the same pixel grid: equal
	// size, geotransform, and coordinate reference system.
	SameGrid(a, b Grid) bool
}

// Windows tiles a w×h raster into row-major windows of at most size×size.
func Windows(w, h, size int) []Window {
	var out []Window
	for y := 0; y < h; y += size {
		wh := min(size, h-y)
		for x := 0; x < w; x += size {
			out = append(out, Window{X: x, Y: y, Width: min(size, w-x), Height: wh})
		}
	}
	return out
}
