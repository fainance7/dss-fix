// Package raster converts rasters to Cloud-Optimized GeoTIFFs and reads their
// footprints using the GDAL command-line tools (gdal_translate, gdalinfo),
// which must be installed in the worker environment.
package raster

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
)

// GDALConverter shells out to GDAL.
type GDALConverter struct {
	compression string
}

// NewGDALConverter returns a converter using DEFLATE compression.
func NewGDALConverter() *GDALConverter {
	return &GDALConverter{compression: "DEFLATE"}
}

// ToCOG converts the source raster to a Cloud-Optimized GeoTIFF at dst. The COG
// driver builds internal tiling and overviews.
func (c *GDALConverter) ToCOG(ctx context.Context, src, dst string) error {
	cmd := exec.CommandContext(ctx, "gdal_translate",
		"-of", "COG",
		"-co", "COMPRESS="+c.compression,
		src, dst,
	)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("gdal_translate: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return nil
}

// Footprint returns the raster's footprint as a GeoJSON polygon in EPSG:4326, or
// nil if the raster has no spatial reference.
func (c *GDALConverter) Footprint(ctx context.Context, src string) ([]byte, error) {
	out, err := exec.CommandContext(ctx, "gdalinfo", "-json", src).Output()
	if err != nil {
		return nil, fmt.Errorf("gdalinfo: %w", err)
	}

	var info struct {
		Wgs84Extent json.RawMessage `json:"wgs84Extent"`
	}
	if err := json.Unmarshal(out, &info); err != nil {
		return nil, fmt.Errorf("parse gdalinfo: %w", err)
	}
	if len(info.Wgs84Extent) == 0 || string(info.Wgs84Extent) == "null" {
		return nil, nil
	}
	return info.Wgs84Extent, nil
}

// VectorFeatures reads every feature of a vector file and returns their
// geometries, reprojected to EPSG:4326, in file order (nil if the file has no
// features). A feature with no geometry yields a nil entry at its index, so
// the result stays aligned with attribute rows read from the same file (the
// shapefile format itself has no other way to pair a .shp geometry record with
// its .dbf attribute row than matching by position). Zipped ESRI shapefiles
// are read in place via GDAL's /vsizip/ virtual filesystem; GeoJSON and
// GeoPackage are read directly.
func (c *GDALConverter) VectorFeatures(ctx context.Context, src string) ([]json.RawMessage, error) {
	input := src
	if strings.EqualFold(filepath.Ext(src), ".zip") {
		input = "/vsizip/" + src
	}

	cmd := exec.CommandContext(ctx, "ogr2ogr",
		"-f", "GeoJSON",
		"-t_srs", "EPSG:4326",
		"/vsistdout/", input,
	)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ogr2ogr: %w: %s", err, strings.TrimSpace(stderr.String()))
	}

	var fc struct {
		Features []struct {
			Geometry json.RawMessage `json:"geometry"`
		} `json:"features"`
	}
	if err := json.Unmarshal(out, &fc); err != nil {
		return nil, fmt.Errorf("parse ogr2ogr output: %w", err)
	}
	if len(fc.Features) == 0 {
		return nil, nil
	}

	geoms := make([]json.RawMessage, len(fc.Features))
	for i, f := range fc.Features {
		if len(f.Geometry) == 0 || string(f.Geometry) == "null" {
			continue
		}
		geoms[i] = f.Geometry
	}
	return geoms, nil
}
