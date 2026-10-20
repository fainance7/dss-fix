package landscape

import (
	"reflect"
	"testing"
)

func TestDefaultParamsValidate(t *testing.T) {
	if err := DefaultParams().Validate(); err != nil {
		t.Fatalf("DefaultParams().Validate() = %v, want nil", err)
	}
}

func TestWithDefaultsFillsZeroFields(t *testing.T) {
	got := Params{}.WithDefaults()
	want := DefaultParams()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Params{}.WithDefaults() = %+v, want %+v", got, want)
	}
}

func TestValidate_TiePriorityDuplicateInvalid(t *testing.T) {
	p := DefaultParams()
	p.TiePriority = []int{1, 1, 2, 3, 4}
	if err := p.Validate(); err == nil {
		t.Fatal("expected error for tie_priority with duplicate values")
	}
}

func TestValidate_WindowSizeOutOfRangeInvalid(t *testing.T) {
	p := DefaultParams()
	p.WindowSize = 100
	if err := p.Validate(); err == nil {
		t.Fatal("expected error for window_size below 256")
	}
}

func TestForNode_NonFuncZoningNodeIsZero(t *testing.T) {
	p := DefaultParams()
	got := p.ForNode("00")
	if !reflect.DeepEqual(got, Params{}) {
		t.Fatalf("ForNode(00) = %+v, want zero value", got)
	}
}

func TestForNode_FuncZoningHasNoWindowSize(t *testing.T) {
	p := DefaultParams()
	got := p.ForNode("30")
	if got.WindowSize != 0 {
		t.Fatalf("ForNode(30).WindowSize = %d, want 0", got.WindowSize)
	}
	if !reflect.DeepEqual(got.TiePriority, p.TiePriority) {
		t.Fatalf("ForNode(30).TiePriority = %v, want %v", got.TiePriority, p.TiePriority)
	}
	if got.V1MinSE12 != p.V1MinSE12 || got.V1MinSE3 != p.V1MinSE3 || got.V2MinSE3 != p.V2MinSE3 || got.V2MinSE4 != p.V2MinSE4 {
		t.Fatalf("ForNode(30) thresholds = %+v, want matching %+v", got, p)
	}
}
