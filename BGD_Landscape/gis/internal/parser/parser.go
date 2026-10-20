// Package parser extracts attribute-table columns (with a few sample values)
// from vector geo files: zipped ESRI shapefiles (.dbf), GeoJSON, and GeoPackage
// (.gpkg). It is used to let a user pick the KATO column and map year columns.
package parser

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"gis/internal/domain"
)

// sampleRows is the maximum number of sample values collected per column.
const sampleRows = 5

// ErrNoColumns indicates the file had no detectable attribute columns.
var ErrNoColumns = errors.New("no attribute columns detected")

// Columns detects the attribute columns of a vector file, dispatching on the
// filename extension.
func Columns(filename string, data []byte) ([]domain.AttributeColumn, error) {
	switch ext := strings.ToLower(filepath.Ext(filename)); ext {
	case ".zip":
		return shapefileColumns(data)
	case ".geojson", ".json":
		return geojsonColumns(data)
	case ".gpkg":
		return gpkgColumns(data)
	default:
		return nil, fmt.Errorf("unsupported format %q", ext)
	}
}

// Rows reads every feature's attribute values as name->value maps, dispatching
// on the filename extension. Used to unpivot the attribute table.
func Rows(filename string, data []byte) ([]map[string]string, error) {
	switch ext := strings.ToLower(filepath.Ext(filename)); ext {
	case ".zip":
		return shapefileRows(data)
	case ".geojson", ".json":
		return geojsonRows(data)
	case ".gpkg":
		return gpkgRows(data)
	default:
		return nil, fmt.Errorf("unsupported format %q", ext)
	}
}

func geojsonRows(data []byte) ([]map[string]string, error) {
	var fc struct {
		Features []struct {
			Properties map[string]json.RawMessage `json:"properties"`
		} `json:"features"`
	}
	if err := json.Unmarshal(data, &fc); err != nil {
		return nil, fmt.Errorf("decode geojson: %w", err)
	}

	rows := make([]map[string]string, 0, len(fc.Features))
	for _, f := range fc.Features {
		row := make(map[string]string, len(f.Properties))
		for k, raw := range f.Properties {
			row[k] = rawToSample(raw)
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func geojsonColumns(data []byte) ([]domain.AttributeColumn, error) {
	var fc struct {
		Features []struct {
			Properties json.RawMessage `json:"properties"`
		} `json:"features"`
	}
	if err := json.Unmarshal(data, &fc); err != nil {
		return nil, fmt.Errorf("decode geojson: %w", err)
	}
	if len(fc.Features) == 0 {
		return nil, ErrNoColumns
	}

	// Column order is taken from the first feature's properties.
	keys, err := orderedKeys(fc.Features[0].Properties)
	if err != nil {
		return nil, err
	}
	if len(keys) == 0 {
		return nil, ErrNoColumns
	}

	samples := make(map[string][]string, len(keys))
	for i, f := range fc.Features {
		if i >= sampleRows {
			break
		}
		var props map[string]json.RawMessage
		if err := json.Unmarshal(f.Properties, &props); err != nil {
			continue
		}
		for _, k := range keys {
			if raw, ok := props[k]; ok {
				samples[k] = append(samples[k], rawToSample(raw))
			}
		}
	}

	cols := make([]domain.AttributeColumn, 0, len(keys))
	for _, k := range keys {
		cols = append(cols, domain.AttributeColumn{Name: k, Samples: samples[k]})
	}
	return cols, nil
}

// orderedKeys returns the keys of a JSON object in document order.
func orderedKeys(obj json.RawMessage) ([]string, error) {
	dec := json.NewDecoder(bytes.NewReader(obj))
	t, err := dec.Token()
	if err != nil {
		return nil, err
	}
	if d, ok := t.(json.Delim); !ok || d != '{' {
		return nil, fmt.Errorf("properties is not an object")
	}

	var keys []string
	for dec.More() {
		kt, err := dec.Token()
		if err != nil {
			return nil, err
		}
		key, ok := kt.(string)
		if !ok {
			return nil, fmt.Errorf("unexpected object key")
		}
		keys = append(keys, key)
		if err := skipValue(dec); err != nil {
			return nil, err
		}
	}
	return keys, nil
}

// skipValue consumes the next JSON value (scalar, object, or array).
func skipValue(dec *json.Decoder) error {
	t, err := dec.Token()
	if err != nil {
		return err
	}
	d, ok := t.(json.Delim)
	if !ok || (d != '{' && d != '[') {
		return nil
	}
	depth := 1
	for depth > 0 {
		t, err := dec.Token()
		if err != nil {
			return err
		}
		if d, ok := t.(json.Delim); ok {
			if d == '{' || d == '[' {
				depth++
			} else {
				depth--
			}
		}
	}
	return nil
}

// rawToSample renders a JSON value as a short sample string.
func rawToSample(raw json.RawMessage) string {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return strings.TrimSpace(string(raw))
	}
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return t
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(t)
	default:
		return strings.TrimSpace(string(raw))
	}
}
