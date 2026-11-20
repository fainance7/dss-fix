package domain

import "encoding/json"

// District is an administrative district boundary keyed by KATO code. Geometry
// is the boundary serialized as a GeoJSON geometry object in EPSG:4326.
type District struct {
	Kato     string          `json:"kato"`
	Name     string          `json:"name"`
	Geometry json.RawMessage `json:"geometry"`
}

// GeoJSON object type tags (RFC 7946).
const (
	GeoJSONFeatureCollection = "FeatureCollection"
	GeoJSONFeature           = "Feature"
)

// FeatureCollection is a GeoJSON FeatureCollection (RFC 7946).
type FeatureCollection struct {
	Type     string    `json:"type"`
	Features []Feature `json:"features"`
}

// Feature is a GeoJSON Feature (RFC 7946). Geometry is a raw GeoJSON geometry
// object (or JSON null when absent); Properties is an arbitrary key/value map.
type Feature struct {
	Type       string          `json:"type"`
	Geometry   json.RawMessage `json:"geometry"`
	Properties map[string]any  `json:"properties"`
}
