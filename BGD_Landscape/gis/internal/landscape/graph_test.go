package landscape

import (
	"regexp"
	"testing"
)

var slugRe = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

// isSlot reports whether code names one of the leaf input slots.
func isSlot(code string) bool {
	_, ok := SlotByCode(code)
	return ok
}

func TestNodeInputsComeFromSlotsOrLowerStage(t *testing.T) {
	for _, n := range Nodes {
		for _, in := range n.Inputs {
			if isSlot(in) {
				continue
			}
			producer, ok := NodeForOutput(in)
			if !ok {
				t.Errorf("node %s: input %q is neither a slot nor a known node output", n.Code, in)
				continue
			}
			if producer.Stage >= n.Stage {
				t.Errorf("node %s (stage %d): input %q is produced by node %s at stage %d, want stage < %d",
					n.Code, n.Stage, in, producer.Code, producer.Stage, n.Stage)
			}
		}
	}
}

func TestEveryOutputHasExactlyOneProducer(t *testing.T) {
	count := map[string]int{}
	for _, n := range Nodes {
		for _, o := range n.Outputs {
			count[o]++
		}
	}
	for _, o := range Outputs {
		if c := count[o.Code]; c != 1 {
			t.Errorf("output %q has %d producers, want exactly 1", o.Code, c)
		}
	}
	// No node should produce something that isn't in the Outputs list.
	for code, c := range count {
		if _, ok := OutputByCode(code); !ok {
			t.Errorf("node output %q (%d producers) is not declared in Outputs", code, c)
		}
	}
}

func TestStagesGroupingAndOrder(t *testing.T) {
	stages := Stages()
	if len(stages) != 4 {
		t.Fatalf("got %d stages, want 4", len(stages))
	}
	want := [][]string{
		{"00", "01", "02", "03"},
		{"10"},
		{"20", "21"},
		{"30", "31"},
	}
	for i, group := range stages {
		var codes []string
		for _, n := range group {
			codes = append(codes, n.Code)
		}
		if len(codes) != len(want[i]) {
			t.Fatalf("stage group %d: got codes %v, want %v", i, codes, want[i])
		}
		for j, c := range codes {
			if c != want[i][j] {
				t.Errorf("stage group %d: got codes %v, want %v", i, codes, want[i])
				break
			}
		}
	}
}

func TestAllGroupCodesAreValidSlugs(t *testing.T) {
	for _, s := range Slots {
		if !slugRe.MatchString(s.Code) {
			t.Errorf("slot code %q is not a valid slug", s.Code)
		}
	}
	for _, o := range Outputs {
		if !slugRe.MatchString(o.Code) {
			t.Errorf("output code %q is not a valid slug", o.Code)
		}
	}
}

func TestNodeByCode(t *testing.T) {
	n, ok := NodeByCode("00")
	if !ok || n.Name != "z_integrated" {
		t.Fatalf("NodeByCode(00) = %+v, %v", n, ok)
	}
	if _, ok := NodeByCode("99"); ok {
		t.Fatal("NodeByCode(99) should not be found")
	}
}

func TestSlotByCode(t *testing.T) {
	s, ok := SlotByCode(SlotZBiodiversity)
	if !ok || s.Title != "Z Biodiversity" {
		t.Fatalf("SlotByCode(%s) = %+v, %v", SlotZBiodiversity, s, ok)
	}
	if _, ok := SlotByCode("nope"); ok {
		t.Fatal("SlotByCode(nope) should not be found")
	}
}

func TestOutputByCode(t *testing.T) {
	o, ok := OutputByCode(OutE)
	if !ok || o.Title != "E Ecological Constraint" {
		t.Fatalf("OutputByCode(%s) = %+v, %v", OutE, o, ok)
	}
	if _, ok := OutputByCode("nope"); ok {
		t.Fatal("OutputByCode(nope) should not be found")
	}
}

func TestNodeForOutput(t *testing.T) {
	n, ok := NodeForOutput(OutV1)
	if !ok || n.Code != "30" {
		t.Fatalf("NodeForOutput(%s) = %+v, %v", OutV1, n, ok)
	}
	if _, ok := NodeForOutput("nope"); ok {
		t.Fatal("NodeForOutput(nope) should not be found")
	}
}

// Functional zoning ranks the ORIGINAL suitability rasters P1..P5 against E
// (reference notebook cells "Three scenarios" and zoning_core.py); the
// conflict layers C_k are diagnostics and must not replace P_k here.
func TestFuncZoningTakesEAndOriginalP(t *testing.T) {
	n, ok := NodeByCode("30")
	if !ok {
		t.Fatal("node 30 missing")
	}
	want := []string{OutE, SlotPIrrigation, SlotPSnow, SlotPCrop, SlotPPasture, SlotPTourism}
	if len(n.Inputs) != len(want) {
		t.Fatalf("node 30 inputs = %v, want %v", n.Inputs, want)
	}
	for i := range want {
		if n.Inputs[i] != want[i] {
			t.Fatalf("node 30 input[%d] = %s, want %s (order is E, then sectors 1..5)", i, n.Inputs[i], want[i])
		}
	}
}
