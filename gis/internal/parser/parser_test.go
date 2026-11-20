package parser

import (
	"archive/zip"
	"bytes"
	"testing"
)

func TestColumns_GeoJSON(t *testing.T) {
	data := []byte(`{
		"type": "FeatureCollection",
		"features": [
			{"type":"Feature","properties":{"ObjectID":1,"F_2023":100,"D_2025":200,"като":"751010000"},"geometry":null},
			{"type":"Feature","properties":{"ObjectID":2,"F_2023":150,"D_2025":250,"като":"751020000"},"geometry":null}
		]
	}`)

	cols, err := Columns("regions.geojson", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Order must follow the first feature's properties.
	wantNames := []string{"ObjectID", "F_2023", "D_2025", "като"}
	if len(cols) != len(wantNames) {
		t.Fatalf("want %d columns, got %d (%+v)", len(wantNames), len(cols), cols)
	}
	for i, want := range wantNames {
		if cols[i].Name != want {
			t.Errorf("column %d = %q, want %q", i, cols[i].Name, want)
		}
	}

	// KATO column should carry sample values from both features.
	kato := cols[3]
	if len(kato.Samples) != 2 || kato.Samples[0] != "751010000" {
		t.Errorf("unexpected kato samples: %v", kato.Samples)
	}
}

func TestColumns_UnsupportedFormat(t *testing.T) {
	if _, err := Columns("data.txt", []byte("x")); err == nil {
		t.Fatal("expected error for unsupported format")
	}
}

func TestColumns_GeoJSONNoFeatures(t *testing.T) {
	_, err := Columns("empty.geojson", []byte(`{"type":"FeatureCollection","features":[]}`))
	if err == nil {
		t.Fatal("expected error for empty feature collection")
	}
}

// Этап 7 (ПТБ-17): a zipped shapefile whose .dbf inflates past MaxDBFBytes is
// rejected without reading it into memory.
func TestReadDBFRejectsZipBomb(t *testing.T) {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	f, err := zw.Create("x.dbf")
	if err != nil {
		t.Fatal(err)
	}
	chunk := make([]byte, 1<<20)
	for i := 0; i < int(MaxDBFBytes>>20)+2; i++ {
		if _, err := f.Write(chunk); err != nil {
			t.Fatal(err)
		}
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := readDBF(buf.Bytes()); err == nil {
		t.Fatal("expected oversized .dbf to be rejected")
	}
}
