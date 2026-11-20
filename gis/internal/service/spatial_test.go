package service

import (
	"context"
	"encoding/json"
	"testing"
)

func TestGeometryBBox(t *testing.T) {
	b, ok := geometryBBox(json.RawMessage(`{"type":"Polygon","coordinates":[[[69,54],[70,54],[70,55],[69,55],[69,54]]]}`))
	if !ok || b != [4]float64{69, 54, 70, 55} {
		t.Fatalf("bbox %v %v", b, ok)
	}
	b, ok = geometryBBox(json.RawMessage(`{"type":"GeometryCollection","geometries":[{"type":"Point","coordinates":[1,2]},{"type":"Point","coordinates":[3,-4]}]}`))
	if !ok || b != [4]float64{1, -4, 3, 2} {
		t.Fatalf("collection bbox %v %v", b, ok)
	}
}

// ТЗ п. 4.4: objects outside the AOI extent widened by 2° are rejected.
func TestCheckWithinAOI(t *testing.T) {
	t.Setenv("GIS_AOI_BBOX", "68.0,52.5,73.5,55.5")
	s := &DatasetService{}
	in := []json.RawMessage{json.RawMessage(`{"type":"Point","coordinates":[69.15,54.87]}`),
		json.RawMessage(`{"type":"Point","coordinates":[75.4,57.4]}`)} // within the 2° buffer
	if err := s.checkWithinAOI(context.Background(), in); err != nil {
		t.Fatalf("inside AOI rejected: %v", err)
	}
	out := []json.RawMessage{json.RawMessage(`{"type":"Point","coordinates":[20,-10]}`)}
	if err := s.checkWithinAOI(context.Background(), out); err == nil {
		t.Fatal("object outside AOI accepted")
	}
}

func TestAOINotConfigured(t *testing.T) {
	t.Setenv("GIS_AOI_BBOX", "")
	s := &DatasetService{}
	if err := s.checkWithinAOI(context.Background(), []json.RawMessage{json.RawMessage(`{"type":"Point","coordinates":[69,54]}`)}); err == nil {
		t.Fatal("expected AOI-not-configured error")
	}
}
