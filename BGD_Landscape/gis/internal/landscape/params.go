package landscape

import (
	"errors"
	"fmt"
)

// Params are the run-time knobs of the graph (TZ §6): window size, tie
// priority for functional zoning, and the V1/V2 suitability thresholds.
type Params struct {
	WindowSize  int   `json:"window_size"`
	TiePriority []int `json:"tie_priority"`
	V1MinSE12   int   `json:"v1_min_s_e12"`
	V1MinSE3    int   `json:"v1_min_s_e3"`
	V2MinSE3    int   `json:"v2_min_s_e3"`
	V2MinSE4    int   `json:"v2_min_s_e4"`
}

// DefaultParams returns the TZ defaults.
func DefaultParams() Params {
	return Params{WindowSize: 2048, TiePriority: []int{1, 2, 3, 4, 5}, V1MinSE12: 3, V1MinSE3: 4, V2MinSE3: 3, V2MinSE4: 5}
}

// WithDefaults fills zero-valued fields from DefaultParams.
func (p Params) WithDefaults() Params {
	d := DefaultParams()
	if p.WindowSize == 0 {
		p.WindowSize = d.WindowSize
	}
	if len(p.TiePriority) == 0 {
		p.TiePriority = d.TiePriority
	}
	if p.V1MinSE12 == 0 {
		p.V1MinSE12 = d.V1MinSE12
	}
	if p.V1MinSE3 == 0 {
		p.V1MinSE3 = d.V1MinSE3
	}
	if p.V2MinSE3 == 0 {
		p.V2MinSE3 = d.V2MinSE3
	}
	if p.V2MinSE4 == 0 {
		p.V2MinSE4 = d.V2MinSE4
	}
	return p
}

// Validate checks ranges: window 256..8192, thresholds 1..5, tie_priority a
// permutation of 1..5.
func (p Params) Validate() error {
	var errs []error
	if p.WindowSize < 256 || p.WindowSize > 8192 {
		errs = append(errs, fmt.Errorf("window_size must be in 256..8192"))
	}
	for name, v := range map[string]int{"v1_min_s_e12": p.V1MinSE12, "v1_min_s_e3": p.V1MinSE3, "v2_min_s_e3": p.V2MinSE3, "v2_min_s_e4": p.V2MinSE4} {
		if v < 1 || v > 5 {
			errs = append(errs, fmt.Errorf("%s must be in 1..5", name))
		}
	}
	seen := map[int]bool{}
	for _, k := range p.TiePriority {
		if k < 1 || k > 5 || seen[k] {
			errs = append(errs, fmt.Errorf("tie_priority must be a permutation of 1..5"))
			break
		}
		seen[k] = true
	}
	if len(p.TiePriority) != 5 && len(seen) == len(p.TiePriority) {
		errs = append(errs, fmt.Errorf("tie_priority must list all of 1..5"))
	}
	return errors.Join(errs...)
}

// ForNode keeps only the fields that influence the node's result (window size
// never does), so fingerprints do not change when irrelevant knobs change.
func (p Params) ForNode(code string) Params {
	if code == "30" {
		return Params{TiePriority: p.TiePriority, V1MinSE12: p.V1MinSE12, V1MinSE3: p.V1MinSE3, V2MinSE3: p.V2MinSE3, V2MinSE4: p.V2MinSE4}
	}
	return Params{}
}
