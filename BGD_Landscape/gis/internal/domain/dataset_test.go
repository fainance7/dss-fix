package domain

import (
	"errors"
	"testing"
)

func TestFileTypeValid(t *testing.T) {
	valid := []FileType{FileTypeVectorWithKato, FileTypeVector, FileTypeRaster}
	for _, ft := range valid {
		if !ft.Valid() {
			t.Errorf("%q should be valid", ft)
		}
	}

	invalid := []FileType{"", "vector_with_table", "image", "bogus"}
	for _, ft := range invalid {
		if ft.Valid() {
			t.Errorf("%q should be invalid", ft)
		}
	}
}

func TestValidateFileContent(t *testing.T) {
	cases := []struct {
		name string
		ext  string
		head []byte
		ok   bool
	}{
		{"valid tiff LE", ".tif", []byte("II*\x00rest"), true},
		{"valid tiff BE", ".tiff", []byte("MM\x00*rest"), true},
		{"valid bigtiff LE", ".tif", []byte("II+\x00rest"), true},
		{"valid bigtiff BE", ".tiff", []byte("MM\x00+rest"), true},
		{"bad tiff", ".tif", []byte("not a tiff"), false},
		{"bad tiff near-miss prefix", ".tif", []byte("II-\x00rest"), false},
		{"valid zip", ".zip", []byte("PK\x03\x04rest"), true},
		{"bad zip", ".zip", []byte("RARrest"), false},
		{"valid gpkg", ".gpkg", []byte("SQLite format 3\x00rest"), true},
		{"bad gpkg", ".gpkg", []byte("notsqlite"), false},
		{"valid geojson", ".geojson", []byte("  \n{\"type\":\"FeatureCollection\"}"), true},
		{"geojson with BOM", ".geojson", append([]byte{0xEF, 0xBB, 0xBF}, []byte("{}")...), true},
		{"bad geojson", ".geojson", []byte("<html>"), false},
		{"empty geojson", ".geojson", []byte(""), false},
		{"unknown ext is permissive", ".dat", []byte("anything"), true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := ValidateFileContent(c.ext, c.head)
			if c.ok && err != nil {
				t.Errorf("want ok, got %v", err)
			}
			if !c.ok && err == nil {
				t.Errorf("want error, got nil")
			}
		})
	}
}

func TestExtensionAllowedFor(t *testing.T) {
	cases := []struct {
		ft   FileType
		ext  string
		want bool
	}{
		{FileTypeVectorWithKato, ".zip", true},
		{FileTypeVectorWithKato, ".geojson", true},
		{FileTypeVectorWithKato, ".gpkg", true},
		{FileTypeVectorWithKato, ".tif", false},
		{FileTypeRaster, ".tif", true},
		{FileTypeRaster, ".geojson", false},
		{FileTypeVector, ".geojson", true},
		{"bogus", ".zip", false},
	}
	for _, c := range cases {
		if got := ExtensionAllowedFor(c.ft, c.ext); got != c.want {
			t.Errorf("ExtensionAllowedFor(%q, %q) = %v, want %v", c.ft, c.ext, got, c.want)
		}
	}
}

func TestValidateDatePeriod(t *testing.T) {
	s := func(v string) *string { return &v }
	cases := []struct {
		name       string
		start, end *string
		wantErr    bool
	}{
		{"both nil", nil, nil, false},
		{"start only", s("2020-01-01"), nil, false},
		{"ordered", s("2020-01-01"), s("2020-12-31"), false},
		{"same day", s("2020-01-01"), s("2020-01-01"), false},
		{"reversed", s("2020-12-31"), s("2020-01-01"), true},
		{"bad start", s("01.01.2020"), nil, true},
		{"bad end", nil, s("2020-13-01"), true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := ValidateDatePeriod(tc.start, tc.end)
			if (err != nil) != tc.wantErr {
				t.Fatalf("err=%v wantErr=%v", err, tc.wantErr)
			}
			if err != nil && !errors.Is(err, ErrValidation) {
				t.Fatalf("expected ErrValidation, got %v", err)
			}
		})
	}
}
