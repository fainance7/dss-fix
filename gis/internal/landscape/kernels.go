package landscape

// valid reports whether a class value is in the analysis domain (1..5). Any
// other value, including nodata and NaN, is invalid.
func valid(v float64) bool { return v >= 1 && v <= 5 }

// classify maps a float32 score onto classes 1..5 with the fixed intervals
// used by the reference notebook (comparisons in float32, like numpy on a
// float32 array). Scores outside [1, 5] yield NoData.
func classify(x float32) int16 {
	switch {
	case x < 1.0:
		return NoData
	case x <= 1.8:
		return 1
	case x <= 2.6:
		return 2
	case x <= 3.4:
		return 3
	case x <= 4.2:
		return 4
	case x <= 5.0:
		return 5
	default:
		return NoData
	}
}

// kernelZIntegrated: all four Z valid -> classify(0.25*Z1 + 0.25*Z2 + 0.25*Z3 + 0.25*Z4).
func kernelZIntegrated(in [][]float64, out [][]int16, _ Params) {
	for i := range out[0] {
		z1, z2, z3, z4 := in[0][i], in[1][i], in[2][i], in[3][i]
		if !(valid(z1) && valid(z2) && valid(z3) && valid(z4)) {
			out[0][i] = NoData
			continue
		}
		// explicit conversions forbid FMA fusion so results match numpy's per-op rounding
		score := float32(float32(0.25)*float32(z1)) + float32(float32(0.25)*float32(z2)) +
			float32(float32(0.25)*float32(z3)) + float32(float32(0.25)*float32(z4))
		out[0][i] = classify(score)
	}
}

// kernelUIntegrated: water and soil required, at least one of crop/veg;
// U = (0.25*water + 0.25*soil + [0.25*crop] + [0.25*veg]) / (0.5 + [0.25] + [0.25]).
func kernelUIntegrated(in [][]float64, out [][]int16, _ Params) {
	for i := range out[0] {
		water, soil, crop, veg := in[0][i], in[1][i], in[2][i], in[3][i]
		cropOK, vegOK := valid(crop), valid(veg)
		if !(valid(water) && valid(soil) && (cropOK || vegOK)) {
			out[0][i] = NoData
			continue
		}
		// explicit conversions forbid FMA fusion so results match numpy's per-op rounding
		num := float32(float32(0.25)*float32(water)) + float32(float32(0.25)*float32(soil))
		den := float32(0.5)
		if cropOK {
			num += float32(float32(0.25) * float32(crop))
			den += 0.25
		}
		if vegOK {
			num += float32(float32(0.25) * float32(veg))
			den += 0.25
		}
		out[0][i] = classify(num / den)
	}
}

// kernelZMax: all four valid -> max.
func kernelZMax(in [][]float64, out [][]int16, _ Params) {
	for i := range out[0] {
		m, ok := int16(0), true
		for _, layer := range in {
			v := layer[i]
			if !valid(v) {
				ok = false
				break
			}
			if int16(v) > m {
				m = int16(v)
			}
		}
		if !ok {
			m = NoData
		}
		out[0][i] = m
	}
}

// kernelUMax: water and soil required plus one of crop/veg; max over valid layers.
func kernelUMax(in [][]float64, out [][]int16, _ Params) {
	for i := range out[0] {
		water, soil, crop, veg := in[0][i], in[1][i], in[2][i], in[3][i]
		cropOK, vegOK := valid(crop), valid(veg)
		if !(valid(water) && valid(soil) && (cropOK || vegOK)) {
			out[0][i] = NoData
			continue
		}
		m := max(int16(water), int16(soil))
		if cropOK {
			m = max(m, int16(crop))
		}
		if vegOK {
			m = max(m, int16(veg))
		}
		out[0][i] = m
	}
}

// kernelE: Z and U valid -> max(Z, U).
func kernelE(in [][]float64, out [][]int16, _ Params) {
	for i := range out[0] {
		z, u := in[0][i], in[1][i]
		if !(valid(z) && valid(u)) {
			out[0][i] = NoData
			continue
		}
		out[0][i] = max(int16(z), int16(u))
	}
}

// kernelCConflicts: inputs P1..P5, E; output j = min(Pj, E) where both valid.
func kernelCConflicts(in [][]float64, out [][]int16, _ Params) {
	e := in[5]
	for j := 0; j < 5; j++ {
		p := in[j]
		for i := range out[j] {
			if !(valid(p[i]) && valid(e[i])) {
				out[j][i] = NoData
				continue
			}
			out[j][i] = min(int16(p[i]), int16(e[i]))
		}
	}
}

// kernelMaxAnyValid: max over the valid inputs; NoData when none is valid.
// Used for P_max and C_max.
func kernelMaxAnyValid(in [][]float64, out [][]int16, _ Params) {
	for i := range out[0] {
		m, any := int16(0), false
		for _, layer := range in {
			if v := layer[i]; valid(v) {
				any = true
				m = max(m, int16(v))
			}
		}
		if !any {
			m = NoData
		}
		out[0][i] = m
	}
}

// kernelFuncZoning implements TZ_functional_zoning_go.md §4. Inputs: E, then
// S1..S5 (irrigation, snow, crop, pasture, tourism). Outputs: V1, V2, V3.
func kernelFuncZoning(in [][]float64, out [][]int16, p Params) {
	e := in[0]
	rules := [3]func(e, s int16) bool{
		func(e, s int16) bool {
			return (e <= 2 && s >= int16(p.V1MinSE12)) || (e == 3 && s >= int16(p.V1MinSE3))
		},
		func(e, s int16) bool {
			return e <= 2 || (e == 3 && s >= int16(p.V2MinSE3)) || (e == 4 && s >= int16(p.V2MinSE4))
		},
		func(e, s int16) bool { return e <= 4 },
	}
	var s [5]int16
	var base [5]bool
	for i := range out[0] {
		if !valid(e[i]) {
			out[0][i], out[1][i], out[2][i] = NoData, NoData, NoData
			continue
		}
		ev := int16(e[i])
		anyBase := false
		for k := 0; k < 5; k++ {
			base[k] = valid(in[k+1][i])
			if base[k] {
				s[k] = int16(in[k+1][i])
				anyBase = true
			}
		}
		if !anyBase {
			out[0][i], out[1][i], out[2][i] = NoData, NoData, NoData
			continue
		}
		for v := 0; v < 3; v++ {
			var cand [5]bool
			maxS, anyCand := int16(0), false
			for k := 0; k < 5; k++ {
				cand[k] = base[k] && rules[v](ev, s[k])
				if cand[k] {
					anyCand = true
					if s[k] > maxS {
						maxS = s[k]
					}
				}
			}
			if !anyCand {
				out[v][i] = 1 // regulated
				continue
			}
			sector := int16(0)
			for _, code := range p.TiePriority {
				k := code - 1
				if cand[k] && s[k] == maxS {
					sector = int16(code)
					break
				}
			}
			out[v][i] = sector + 1
		}
	}
}
