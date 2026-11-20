package raster

import (
	"reflect"
	"testing"
)

func TestWindows(t *testing.T) {
	tests := []struct {
		name       string
		w, h, size int
		want       []Window
	}{
		{
			name: "10x10 size4 tiles into 9 windows",
			w:    10, h: 10, size: 4,
			want: []Window{
				{X: 0, Y: 0, Width: 4, Height: 4},
				{X: 4, Y: 0, Width: 4, Height: 4},
				{X: 8, Y: 0, Width: 2, Height: 4},
				{X: 0, Y: 4, Width: 4, Height: 4},
				{X: 4, Y: 4, Width: 4, Height: 4},
				{X: 8, Y: 4, Width: 2, Height: 4},
				{X: 0, Y: 8, Width: 4, Height: 2},
				{X: 4, Y: 8, Width: 4, Height: 2},
				{X: 8, Y: 8, Width: 2, Height: 2},
			},
		},
		{
			name: "5x3 size8 fits in one window",
			w:    5, h: 3, size: 8,
			want: []Window{
				{X: 0, Y: 0, Width: 5, Height: 3},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Windows(tt.w, tt.h, tt.size)
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("Windows(%d, %d, %d) = %+v, want %+v", tt.w, tt.h, tt.size, got, tt.want)
			}
			if len(got) > 0 {
				last := got[len(got)-1]
				wantLast := tt.want[len(tt.want)-1]
				if last != wantLast {
					t.Fatalf("last window = %+v, want %+v", last, wantLast)
				}
			}
		})
	}
}

func TestDefaultWriterOptions(t *testing.T) {
	got := DefaultWriterOptions()
	want := WriterOptions{NoData: -9999, TileSize: 512, Compression: "DEFLATE"}
	if got != want {
		t.Fatalf("DefaultWriterOptions() = %+v, want %+v", got, want)
	}
}
