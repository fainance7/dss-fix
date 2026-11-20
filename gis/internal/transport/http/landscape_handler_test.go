package http

import (
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/go-playground/validator/v10"
)

// newLandscapeTestHandler builds a handler with a nil service: every case below
// is rejected while parsing or validating the request, before the service is
// reached.
func newLandscapeTestHandler() *LandscapeHandler {
	return NewLandscapeHandler(nil, validator.New(validator.WithRequiredStructEnabled()))
}

func postRun(t *testing.T, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/landscape/runs", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	newLandscapeTestHandler().createRun(rec, req)
	return rec
}

func TestCreateRunInvalidJSON(t *testing.T) {
	rec := postRun(t, `{"from_stage":`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusBadRequest, rec.Body.String())
	}
}

func TestCreateRunUnknownField(t *testing.T) {
	// DecodeJSON rejects unknown fields, so a typo is a 400 rather than a
	// silently ignored option.
	rec := postRun(t, `{"from_stages":1}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusBadRequest, rec.Body.String())
	}
}

func TestCreateRunFromStageOutOfRange(t *testing.T) {
	rec := postRun(t, `{"from_stage":7}`)
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusUnprocessableEntity, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "FromStage") {
		t.Fatalf("body %s does not name the offending field", rec.Body.String())
	}
}

func TestCreateRunOverrideNotUUID(t *testing.T) {
	rec := postRun(t, `{"overrides":{"lp-z-lcr":"not-a-uuid"}}`)
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusUnprocessableEntity, rec.Body.String())
	}
}

func TestUploadSlotRequiresMultipart(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/landscape/slots/lp-z-lcr", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	newLandscapeTestHandler().uploadSlot(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusBadRequest, rec.Body.String())
	}
}

func TestListRunsInvalidPage(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/landscape/runs?page=0", nil)
	rec := httptest.NewRecorder()
	newLandscapeTestHandler().listRuns(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusBadRequest, rec.Body.String())
	}
}

func TestGetRunInvalidID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/landscape/runs/nope", nil)
	rec := httptest.NewRecorder()
	newLandscapeTestHandler().getRun(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusBadRequest, rec.Body.String())
	}
}

// TestDecodeRunRequestEmptyBody pins the "all defaults" call: POST
// /landscape/runs with no body at all is a valid request, not a 400. Tested at
// the helper level because the 202 path needs a live service.
func TestDecodeRunRequestEmptyBody(t *testing.T) {
	for _, body := range []string{"", "{}"} {
		req := httptest.NewRequest(http.MethodPost, "/landscape/runs", strings.NewReader(body))
		rec := httptest.NewRecorder()

		got, ok := decodeRunRequest(rec, req)
		if !ok {
			t.Fatalf("body %q: decodeRunRequest reported failure with status %d", body, rec.Code)
		}
		if got.FromStage != nil || len(got.Overrides) != 0 || got.Params != nil {
			t.Fatalf("body %q: got %+v, want the zero request", body, got)
		}
		if rec.Code != http.StatusOK || rec.Body.Len() != 0 {
			t.Fatalf("body %q: helper wrote a response (status %d, body %s)", body, rec.Code, rec.Body.String())
		}
		// The zero request must also survive validation, or an empty body
		// would still be rejected one step later.
		if err := validator.New(validator.WithRequiredStructEnabled()).Struct(got); err != nil {
			t.Fatalf("body %q: zero request fails validation: %v", body, err)
		}
	}
}

func TestDecodeRunRequestMalformedBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/landscape/runs", strings.NewReader(`{`))
	rec := httptest.NewRecorder()

	if _, ok := decodeRunRequest(rec, req); ok {
		t.Fatal("decodeRunRequest accepted a truncated body")
	}
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusBadRequest)
	}
}

// TestRegisterMountsRoutes walks the route table of a router with the handler
// registered, so every one of the six routes is checked — including GET /slots
// and GET /results, which cannot be exercised without a live service.
func TestRegisterMountsRoutes(t *testing.T) {
	r := chi.NewRouter()
	r.Route("/landscape", newLandscapeTestHandler().Register)

	got := map[string]bool{}
	err := chi.Walk(r, func(method, route string, _ http.Handler, _ ...func(http.Handler) http.Handler) error {
		got[method+" "+strings.TrimSuffix(route, "/")] = true
		return nil
	})
	if err != nil {
		t.Fatalf("walk routes: %v", err)
	}

	want := []string{
		"GET /landscape/slots",
		"POST /landscape/slots/{code}",
		"GET /landscape/runs",
		"POST /landscape/runs",
		"GET /landscape/runs/{id}",
		"GET /landscape/results",
	}
	for _, w := range want {
		if !got[w] {
			t.Errorf("route %q is not mounted; mounted routes: %v", w, keys(got))
		}
	}
	if len(got) != len(want) {
		t.Errorf("mounted %d routes, want %d: %v", len(got), len(want), keys(got))
	}
}

// TestRoutedRequestReachesHandler sends a real request through the router so the
// URL parameter wiring (chi.URLParam) is exercised, not just the pattern.
func TestRoutedRequestReachesHandler(t *testing.T) {
	r := chi.NewRouter()
	r.Route("/landscape", newLandscapeTestHandler().Register)

	req := httptest.NewRequest(http.MethodGet, "/landscape/runs/nope", nil)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want %d (body %s)", rec.Code, http.StatusBadRequest, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "invalid id") {
		t.Fatalf("body %s does not report the bad id", rec.Body.String())
	}
}

func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
