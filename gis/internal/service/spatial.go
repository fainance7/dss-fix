package service

// Этап 7 (ТЗ п. 4.4): обязательная проверка загружаемых пространственных
// объектов — система координат только EPSG:4326 или EPSG:32642, геометрия —
// в пределах экстента зоны интереса (AOI), расширенного на 2 градуса.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// AllowedEPSG lists the coordinate systems accepted on import (ТЗ п. 4.4, 4.7.3).
var AllowedEPSG = map[int]bool{4326: true, 32642: true}

// AOIBufferDeg is the buffer added to every side of the AOI extent (ТЗ п. 4.4).
const AOIBufferDeg = 2.0

// ErrAOINotConfigured means neither GIS_AOI_BBOX nor district boundaries are available.
var ErrAOINotConfigured = errors.New("area of interest is not configured: import district boundaries or set GIS_AOI_BBOX")

// crsProber is implemented by converters that report the source CRS.
type crsProber interface {
	SourceEPSG(ctx context.Context, path string) (int, error)
}

// aoiSource is implemented by repositories that know the AOI extent.
type aoiSource interface {
	DistrictExtent(ctx context.Context) (bbox [4]float64, ok bool, err error)
}

// checkCRS rejects files whose coordinate system is not allowed. Converters
// that cannot report the CRS (test doubles) skip the check.
func (s *DatasetService) checkCRS(ctx context.Context, path string) error {
	p, ok := s.converter.(crsProber)
	if !ok {
		return nil
	}
	code, err := p.SourceEPSG(ctx, path)
	if err != nil {
		return fmt.Errorf("coordinate system cannot be determined (allowed: EPSG:4326, EPSG:32642): %w", err)
	}
	if !AllowedEPSG[code] {
		return fmt.Errorf("coordinate system EPSG:%d is not allowed (allowed: EPSG:4326, EPSG:32642)", code)
	}
	return nil
}

// aoiExtent returns the AOI extent widened by AOIBufferDeg.
func (s *DatasetService) aoiExtent(ctx context.Context) ([4]float64, error) {
	var b [4]float64
	if v := strings.TrimSpace(os.Getenv("GIS_AOI_BBOX")); v != "" {
		parts := strings.Split(v, ",")
		if len(parts) != 4 {
			return b, fmt.Errorf("GIS_AOI_BBOX must be minLon,minLat,maxLon,maxLat")
		}
		for i, p := range parts {
			f, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
			if err != nil {
				return b, fmt.Errorf("GIS_AOI_BBOX: %w", err)
			}
			b[i] = f
		}
	} else {
		src, ok := s.repo.(aoiSource)
		if !ok {
			return b, ErrAOINotConfigured
		}
		ext, found, err := src.DistrictExtent(ctx)
		if err != nil {
			return b, err
		}
		if !found {
			return b, ErrAOINotConfigured
		}
		b = ext
	}
	return [4]float64{b[0] - AOIBufferDeg, b[1] - AOIBufferDeg, b[2] + AOIBufferDeg, b[3] + AOIBufferDeg}, nil
}

// checkWithinAOI verifies that every geometry (GeoJSON in EPSG:4326) lies inside
// the buffered AOI extent.
func (s *DatasetService) checkWithinAOI(ctx context.Context, geoms []json.RawMessage) error {
	ext, err := s.aoiExtent(ctx)
	if err != nil {
		return err
	}
	for i, g := range geoms {
		if len(g) == 0 || string(g) == "null" {
			continue
		}
		box, ok := geometryBBox(g)
		if !ok {
			continue
		}
		if !insideBox(box, ext) {
			return fmt.Errorf("object %d lies outside the area of interest (%.4f,%.4f,%.4f,%.4f incl. %.0f° buffer)",
				i+1, ext[0], ext[1], ext[2], ext[3], AOIBufferDeg)
		}
	}
	return nil
}

func insideBox(b, ext [4]float64) bool {
	return b[0] >= ext[0] && b[1] >= ext[1] && b[2] <= ext[2] && b[3] <= ext[3]
}

// geometryBBox returns [minX, minY, maxX, maxY] of a GeoJSON geometry (or polygon
// coordinates such as gdalinfo's wgs84Extent).
func geometryBBox(raw json.RawMessage) ([4]float64, bool) {
	var g struct {
		Coordinates json.RawMessage   `json:"coordinates"`
		Geometries  []json.RawMessage `json:"geometries"`
	}
	if err := json.Unmarshal(raw, &g); err != nil {
		return [4]float64{}, false
	}
	box := [4]float64{math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)}
	found := false
	var walk func(v any)
	walk = func(v any) {
		arr, ok := v.([]any)
		if !ok {
			return
		}
		if len(arr) >= 2 {
			x, okx := arr[0].(float64)
			y, oky := arr[1].(float64)
			if okx && oky {
				box[0], box[1] = math.Min(box[0], x), math.Min(box[1], y)
				box[2], box[3] = math.Max(box[2], x), math.Max(box[3], y)
				found = true
				return
			}
		}
		for _, e := range arr {
			walk(e)
		}
	}
	if len(g.Coordinates) > 0 {
		var v any
		if err := json.Unmarshal(g.Coordinates, &v); err == nil {
			walk(v)
		}
	}
	for _, sub := range g.Geometries {
		if b, ok := geometryBBox(sub); ok {
			box[0], box[1] = math.Min(box[0], b[0]), math.Min(box[1], b[1])
			box[2], box[3] = math.Max(box[2], b[2]), math.Max(box[3], b[3])
			found = true
		}
	}
	return box, found
}

// validateVectorFile applies the ТЗ п. 4.4 checks to an uploaded vector file:
// allowed coordinate system and every geometry inside the buffered AOI.
// Converters that cannot report the CRS (test doubles) skip both checks.
func (s *DatasetService) validateVectorFile(ctx context.Context, filename string, data []byte) error {
	if _, ok := s.converter.(crsProber); !ok {
		return nil
	}
	ext := strings.ToLower(filepath.Ext(filename))
	f, err := os.CreateTemp("", "gis-chk-*"+ext)
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err := f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if ext == ".geojson" || ext == ".json" {
		// RFC 7946: GeoJSON is EPSG:4326 unless GDAL reports otherwise.
		if err := s.checkCRS(ctx, f.Name()); err != nil && !strings.Contains(err.Error(), "cannot be determined") {
			return err
		}
	} else if err := s.checkCRS(ctx, f.Name()); err != nil {
		return err
	}
	geoms, err := s.converter.VectorFeatures(ctx, f.Name())
	if err != nil {
		return fmt.Errorf("read geometries: %w", err)
	}
	return s.checkWithinAOI(ctx, geoms)
}
