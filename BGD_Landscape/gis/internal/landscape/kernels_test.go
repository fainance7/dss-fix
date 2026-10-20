package landscape

import (
	"reflect"
	"testing"
)

func run(k Kernel, nOut int, in ...[]float64) [][]int16 {
	out := make([][]int16, nOut)
	for j := range out {
		out[j] = make([]int16, len(in[0]))
	}
	k(in, out, DefaultParams())
	return out
}

func TestClassify(t *testing.T) {
	cases := map[float32]int16{1.0: 1, 1.8: 1, 1.81: 2, 2.6: 2, 2.75: 3, 3.4: 3, 3.5: 4, 4.2: 4, 4.25: 5, 5.0: 5}
	for x, want := range cases {
		if got := classify(x); got != want {
			t.Errorf("classify(%v)=%d want %d", x, got, want)
		}
	}
	if classify(0.9) != NoData || classify(5.1) != NoData {
		t.Error("out of range must be NoData")
	}
}

func TestKernelZIntegrated(t *testing.T) {
	// pixel0: all valid (1,2,3,4) -> 2.5 -> class 2 ; pixel1: one nodata -> NoData ; pixel2: all 5 -> 5 -> class 5
	got := run(kernelZIntegrated, 1,
		[]float64{1, -9999, 5}, []float64{2, 2, 5}, []float64{3, 3, 5}, []float64{4, 4, 5})
	if want := []int16{2, NoData, 5}; !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %v want %v", got[0], want)
	}
}

func TestKernelUIntegrated_Applicability(t *testing.T) {
	// order: water, soil, crop, veg
	// p0: all valid 1,1,5,5 -> (0.25+0.25+1.25+1.25)/1.0 = 3.0 -> class 3
	// p1: crop invalid: 1,1,-,5 -> (0.25+0.25+1.25)/0.75 = 2.333 -> class 2
	// p2: crop and veg invalid -> NoData
	// p3: water invalid -> NoData
	got := run(kernelUIntegrated, 1,
		[]float64{1, 1, 1, -9999}, []float64{1, 1, 1, 1}, []float64{5, 0, 0, 5}, []float64{5, 5, 0, 5})
	if want := []int16{3, 2, NoData, NoData}; !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %v want %v", got[0], want)
	}
}

func TestKernelZMax_RequiresAllValid(t *testing.T) {
	got := run(kernelZMax, 1, []float64{1, 1}, []float64{4, 4}, []float64{2, -9999}, []float64{3, 3})
	if want := []int16{4, NoData}; !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %v want %v", got[0], want)
	}
}

func TestKernelUMax_IgnoresInvalidCropVeg(t *testing.T) {
	// water, soil, crop, veg ; p0: 1,2,-,5 -> 5 ; p1: 3,2,0,0 -> NoData (needs crop or veg) ; p2: 3,2,1,0 -> 3
	got := run(kernelUMax, 1, []float64{1, 3, 3}, []float64{2, 2, 2}, []float64{0, 0, 1}, []float64{5, 0, 0})
	if want := []int16{5, NoData, 3}; !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %v want %v", got[0], want)
	}
}

func TestKernelE(t *testing.T) {
	got := run(kernelE, 1, []float64{2, 5, -9999}, []float64{4, 1, 3})
	if want := []int16{4, 5, NoData}; !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %v want %v", got[0], want)
	}
}

func TestKernelCConflicts(t *testing.T) {
	// 5 P then E; p0: E=3 -> min(P,3); p1: E invalid -> all NoData; p2: P2 invalid
	e := []float64{3, 0, 4}
	got := run(kernelCConflicts, 5,
		[]float64{5, 5, 5}, []float64{1, 1, 7}, []float64{3, 3, 3}, []float64{4, 4, 4}, []float64{2, 2, 2}, e)
	want := [][]int16{{3, NoData, 4}, {1, NoData, NoData}, {3, NoData, 3}, {3, NoData, 4}, {2, NoData, 2}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v want %v", got, want)
	}
}

func TestKernelMaxAnyValid(t *testing.T) {
	got := run(kernelMaxAnyValid, 1, []float64{1, 0, 0}, []float64{4, 0, 2}, []float64{-9999, 0, 0})
	if want := []int16{4, NoData, 2}; !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %v want %v", got[0], want)
	}
}

// TZ §7 edge cases. Inputs: E, then C1..C5 (irrigation, snow, crop, pasture, tourism).
func TestKernelFuncZoning_TZEdgeCases(t *testing.T) {
	e := []float64{0, 3, 5, 4, 2, 2, 3}
	s1 := []float64{3, 0, 3, 5, 3, 2, 4}
	s2 := []float64{3, 0, 3, 5, 3, 2, 4}
	s3 := []float64{3, 0, 3, 2, 3, 2, 5}
	s4 := []float64{3, 0, 3, 2, 3, 2, 1}
	s5 := []float64{3, 0, 3, 2, 3, 2, 1}
	got := run(kernelFuncZoning, 3, e, s1, s2, s3, s4, s5)
	// pixel: 0 E invalid -> NoData ; 1 all S invalid -> NoData ; 2 E=5 -> regulated(1) everywhere ;
	// 3 E=4, S=5 for sectors 1,2 -> V1 regulated, V2/V3 sector 1 -> class 2 ;
	// 4 all S equal (3), E=2 -> irrigation (2) in all ; 5 E=2, S=2 -> V1 regulated (S<3), V2 best=irrigation (2), V3 irrigation (2)
	// 6 E=3: S=[4,4,5,1,1] -> V1 (S>=4): winners 1,2,3 max 5 -> crop (4); V2 (S>=3) -> crop (4); V3 -> crop (4)
	want := [][]int16{
		{NoData, NoData, 1, 1, 2, 1, 4}, // V1
		{NoData, NoData, 1, 2, 2, 2, 4}, // V2
		{NoData, NoData, 1, 2, 2, 2, 4}, // V3
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v want %v", got, want)
	}
}

func TestKernelFuncZoning_TiePriorityIsConfigurable(t *testing.T) {
	in := [][]float64{{2}, {3}, {3}, {3}, {3}, {3}}
	out := [][]int16{{0}, {0}, {0}}
	p := DefaultParams()
	p.TiePriority = []int{5, 4, 3, 2, 1}
	kernelFuncZoning(in, out, p)
	if out[0][0] != 6 || out[1][0] != 6 || out[2][0] != 6 {
		t.Fatalf("expected tourism (6) everywhere, got %v", out)
	}
}
