// Package landscape defines the Landscape Program (ЛП) computation graph — the
// per-pixel formulas that turn ecological (Z), vulnerability (U) and
// suitability (P) class rasters into constraints (E), conflicts (C) and
// functional zoning (V1–V3) — together with a window-based executor.
package landscape

import "sort"

const (
	// NoData is the output nodata value of every LP raster.
	NoData int16 = -9999
	// CategoryCode is the dataset category holding every LP dataset.
	CategoryCode = "landscape"
)

// Group codes. Slots are leaf inputs users upload; the rest are node outputs.
const (
	SlotZBiodiversity   = "lp-z-biodiversity"
	SlotZLCR            = "lp-z-lcr"
	SlotZSoilProtection = "lp-z-soil-protection"
	SlotZWR             = "lp-z-wr"
	SlotUSurfWater      = "lp-u-surf-water"
	SlotUSoil           = "lp-u-soil"
	SlotUCrop           = "lp-u-crop"
	SlotUVeg            = "lp-u-veg"
	SlotPIrrigation     = "lp-p-irrigation"
	SlotPSnow           = "lp-p-snow"
	SlotPCrop           = "lp-p-crop"
	SlotPPasture        = "lp-p-pasture"
	SlotPTourism        = "lp-p-tourism"

	OutZIntegrated = "lp-z-integrated"
	OutUIntegrated = "lp-u-integrated"
	OutZMax        = "lp-z-max"
	OutUMax        = "lp-u-max"
	OutE           = "lp-e"
	OutCIrrigation = "lp-c-irrigation"
	OutCSnow       = "lp-c-snow"
	OutCCrop       = "lp-c-crop"
	OutCPasture    = "lp-c-pasture"
	OutCTourism    = "lp-c-tourism"
	OutPMax        = "lp-p-max"
	OutV1          = "lp-v1-conservative"
	OutV2          = "lp-v2-balanced"
	OutV3          = "lp-v3-development"
	OutCMax        = "lp-c-max"
)

type Slot struct{ Code, Title string }
type Output struct{ Code, Title string }

// Kernel computes one window. in[i] follows Node.Inputs order and out[j]
// follows Node.Outputs order; every slice has exactly window-pixel length.
type Kernel func(in [][]float64, out [][]int16, p Params)

// Node is one function of the graph. Stage = first digit of Code.
type Node struct {
	Code, Name string
	Stage      int
	Inputs     []string
	Outputs    []string
	Kernel     Kernel
}

var Slots = []Slot{
	{SlotZBiodiversity, "Z Biodiversity"}, {SlotZLCR, "Z LCR"}, {SlotZSoilProtection, "Z Soil Protection"}, {SlotZWR, "Z Water Resources"},
	{SlotUSurfWater, "U Surface Water"}, {SlotUSoil, "U Soil"}, {SlotUCrop, "U Crop"}, {SlotUVeg, "U Vegetation"},
	{SlotPIrrigation, "P Irrigation"}, {SlotPSnow, "P Snow"}, {SlotPCrop, "P Crop"}, {SlotPPasture, "P Pasture"}, {SlotPTourism, "P Tourism"},
}

var Outputs = []Output{
	{OutZIntegrated, "Z Integrated"}, {OutUIntegrated, "U Integrated"}, {OutZMax, "Z Max"}, {OutUMax, "U Max"},
	{OutE, "E Ecological Constraint"},
	{OutCIrrigation, "C Irrigation"}, {OutCSnow, "C Snow"}, {OutCCrop, "C Crop"}, {OutCPasture, "C Pasture"}, {OutCTourism, "C Tourism"},
	{OutPMax, "P Max"},
	{OutV1, "V1 Conservative"}, {OutV2, "V2 Balanced"}, {OutV3, "V3 Development"}, {OutCMax, "C Max"},
}

var zInputs = []string{SlotZBiodiversity, SlotZLCR, SlotZSoilProtection, SlotZWR}
var uInputs = []string{SlotUSurfWater, SlotUSoil, SlotUCrop, SlotUVeg}                    // order matters: water, soil, crop, veg
var pInputs = []string{SlotPIrrigation, SlotPSnow, SlotPCrop, SlotPPasture, SlotPTourism} // sector order 1..5
var cOutputs = []string{OutCIrrigation, OutCSnow, OutCCrop, OutCPasture, OutCTourism}

var Nodes = []Node{
	{Code: "00", Name: "z_integrated", Stage: 0, Inputs: zInputs, Outputs: []string{OutZIntegrated}, Kernel: kernelZIntegrated},
	{Code: "01", Name: "u_integrated", Stage: 0, Inputs: uInputs, Outputs: []string{OutUIntegrated}, Kernel: kernelUIntegrated},
	{Code: "02", Name: "z_max", Stage: 0, Inputs: zInputs, Outputs: []string{OutZMax}, Kernel: kernelZMax},
	{Code: "03", Name: "u_max", Stage: 0, Inputs: uInputs, Outputs: []string{OutUMax}, Kernel: kernelUMax},
	{Code: "10", Name: "e_ecological_constraint", Stage: 1, Inputs: []string{OutZIntegrated, OutUIntegrated}, Outputs: []string{OutE}, Kernel: kernelE},
	{Code: "20", Name: "c_conflicts", Stage: 2, Inputs: append(append([]string{}, pInputs...), OutE), Outputs: cOutputs, Kernel: kernelCConflicts},
	{Code: "21", Name: "p_max", Stage: 2, Inputs: pInputs, Outputs: []string{OutPMax}, Kernel: kernelMaxAnyValid},
	// func_zoning ranks the original suitability rasters P1..P5 against E (the
	// reference notebook and zoning_core.py); the conflict layers C_k are
	// diagnostics and are not zoning inputs. It stays in stage 3 to keep the
	// diagram's folder numbering (30x) even though it only depends on stage 1.
	{Code: "30", Name: "func_zoning", Stage: 3, Inputs: append([]string{OutE}, pInputs...), Outputs: []string{OutV1, OutV2, OutV3}, Kernel: kernelFuncZoning},
	{Code: "31", Name: "c_max", Stage: 3, Inputs: cOutputs, Outputs: []string{OutCMax}, Kernel: kernelMaxAnyValid},
}

func NodeByCode(code string) (Node, bool) {
	for _, n := range Nodes {
		if n.Code == code {
			return n, true
		}
	}
	return Node{}, false
}

func SlotByCode(code string) (Slot, bool) {
	for _, s := range Slots {
		if s.Code == code {
			return s, true
		}
	}
	return Slot{}, false
}

func OutputByCode(code string) (Output, bool) {
	for _, o := range Outputs {
		if o.Code == code {
			return o, true
		}
	}
	return Output{}, false
}

// NodeForOutput returns the node producing the given output group.
func NodeForOutput(group string) (Node, bool) {
	for _, n := range Nodes {
		for _, o := range n.Outputs {
			if o == group {
				return n, true
			}
		}
	}
	return Node{}, false
}

// Stages groups nodes by stage, ascending; within a stage nodes are sorted by code.
func Stages() [][]Node {
	byStage := map[int][]Node{}
	for _, n := range Nodes {
		byStage[n.Stage] = append(byStage[n.Stage], n)
	}
	keys := make([]int, 0, len(byStage))
	for k := range byStage {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	out := make([][]Node, 0, len(keys))
	for _, k := range keys {
		ns := byStage[k]
		sort.Slice(ns, func(i, j int) bool { return ns[i].Code < ns[j].Code })
		out = append(out, ns)
	}
	return out
}
