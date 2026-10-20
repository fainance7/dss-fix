package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// stubGroupRepo is an in-memory DatasetGroupRepository for tests. It also
// satisfies the dataset service's groupReader dependency.
type stubGroupRepo struct {
	store map[uuid.UUID]domain.DatasetGroup
}

func newStubGroupRepo() *stubGroupRepo {
	return &stubGroupRepo{store: map[uuid.UUID]domain.DatasetGroup{}}
}

func (r *stubGroupRepo) Create(_ context.Context, g domain.DatasetGroup) (domain.DatasetGroup, error) {
	for _, e := range r.store {
		if e.Code == g.Code {
			return domain.DatasetGroup{}, domain.ErrConflict
		}
	}
	g.ID = uuid.New()
	r.store[g.ID] = g
	return g, nil
}

func (r *stubGroupRepo) GetByID(_ context.Context, id uuid.UUID) (domain.DatasetGroup, error) {
	g, ok := r.store[id]
	if !ok {
		return domain.DatasetGroup{}, domain.ErrNotFound
	}
	return g, nil
}

func (r *stubGroupRepo) GetByCode(_ context.Context, code string) (domain.DatasetGroup, error) {
	for _, g := range r.store {
		if g.Code == code {
			return g, nil
		}
	}
	return domain.DatasetGroup{}, domain.ErrNotFound
}

func (r *stubGroupRepo) List(_ context.Context, code *string, _, _ int) ([]domain.DatasetGroup, error) {
	out := []domain.DatasetGroup{}
	for _, g := range r.store {
		if code == nil || g.Code == *code {
			out = append(out, g)
		}
	}
	return out, nil
}

func (r *stubGroupRepo) Count(_ context.Context, code *string) (int, error) {
	l, _ := r.List(context.Background(), code, 0, 0)
	return len(l), nil
}

func (r *stubGroupRepo) Update(_ context.Context, g domain.DatasetGroup) (domain.DatasetGroup, error) {
	if _, ok := r.store[g.ID]; !ok {
		return domain.DatasetGroup{}, domain.ErrNotFound
	}
	r.store[g.ID] = g
	return g, nil
}

func (r *stubGroupRepo) Delete(_ context.Context, id uuid.UUID) error {
	if _, ok := r.store[id]; !ok {
		return domain.ErrNotFound
	}
	delete(r.store, id)
	return nil
}

func (r *stubGroupRepo) SetSLD(_ context.Context, id uuid.UUID, key, filename, contentType string) (domain.DatasetGroup, error) {
	g, ok := r.store[id]
	if !ok {
		return domain.DatasetGroup{}, domain.ErrNotFound
	}
	g.SldStorageKey, g.SldFilename, g.SldContentType = &key, &filename, &contentType
	r.store[id] = g
	return g, nil
}

func TestDatasetGroupService_Create_RequiresSlug(t *testing.T) {
	svc := NewDatasetGroupService(newStubGroupRepo(), newStubDatasetRepo(), newStubStore())
	_, err := svc.Create(context.Background(), DatasetGroupInput{Code: "Bad Code", Name: "x"})
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetGroupService_Latest_PrefersGreatestEndDateThenCreatedAt(t *testing.T) {
	groups := newStubGroupRepo()
	datasets := newStubDatasetRepo()
	svc := NewDatasetGroupService(groups, datasets, newStubStore())
	g, err := svc.Create(context.Background(), DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}
	s := func(v string) *string { return &v }
	base := time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)
	old := domain.Dataset{ID: uuid.New(), DatasetGroupID: &g.ID, EndDate: s("2019-12-31"), Status: domain.DatasetStatusReady, CreatedAt: base}
	fresh := domain.Dataset{ID: uuid.New(), DatasetGroupID: &g.ID, EndDate: s("2020-12-31"), Status: domain.DatasetStatusReady, CreatedAt: base.Add(time.Hour)}
	undated := domain.Dataset{ID: uuid.New(), DatasetGroupID: &g.ID, Status: domain.DatasetStatusReady, CreatedAt: base.Add(2 * time.Hour)}
	for _, d := range []domain.Dataset{old, fresh, undated} {
		datasets.store[d.ID] = d
	}
	got, err := svc.Latest(context.Background(), g.ID, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got.ID != fresh.ID {
		t.Fatalf("want %s, got %s", fresh.ID, got.ID)
	}
}

func TestDatasetGroupService_Latest_NotFoundWhenEmpty(t *testing.T) {
	groups := newStubGroupRepo()
	svc := NewDatasetGroupService(groups, newStubDatasetRepo(), newStubStore())
	g, err := svc.Create(context.Background(), DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Latest(context.Background(), g.ID, nil, nil); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestDatasetGroupService_Latest_UnknownGroupIsNotFound(t *testing.T) {
	svc := NewDatasetGroupService(newStubGroupRepo(), newStubDatasetRepo(), newStubStore())
	if _, err := svc.Latest(context.Background(), uuid.New(), nil, nil); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestDatasetGroupService_UploadSLD_RejectsBadExtension(t *testing.T) {
	groups := newStubGroupRepo()
	store := newStubStore()
	svc := NewDatasetGroupService(groups, newStubDatasetRepo(), store)
	g, err := svc.Create(context.Background(), DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = svc.UploadSLD(context.Background(), g.ID, UploadSLDInput{Filename: "style.txt", ContentType: "text/plain", Size: 3, Reader: strings.NewReader("<x>")})
	if !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
	if len(store.put) != 0 {
		t.Fatalf("nothing should be stored on validation failure, got %v", store.put)
	}
}

func TestDatasetGroupService_UploadSLD_StoresAndReplaces(t *testing.T) {
	ctx := context.Background()
	groups := newStubGroupRepo()
	store := newStubStore()
	svc := NewDatasetGroupService(groups, newStubDatasetRepo(), store)
	g, err := svc.Create(ctx, DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}

	updated, err := svc.UploadSLD(ctx, g.ID, UploadSLDInput{
		Filename: "style.sld", ContentType: "application/xml", Size: 3, Reader: strings.NewReader("<x>"),
	})
	if err != nil {
		t.Fatal(err)
	}
	wantKey := "groups/" + g.ID.String() + "/sld/style.sld"
	if updated.SldStorageKey == nil || *updated.SldStorageKey != wantKey {
		t.Fatalf("want key %s, got %v", wantKey, updated.SldStorageKey)
	}

	// A second upload under a different name replaces the previous object.
	if _, err := svc.UploadSLD(ctx, g.ID, UploadSLDInput{
		Filename: "other.sld", ContentType: "application/xml", Size: 3, Reader: strings.NewReader("<y>"),
	}); err != nil {
		t.Fatal(err)
	}
	if len(store.removed) != 1 || store.removed[0] != wantKey {
		t.Fatalf("want previous sld removed, got %v", store.removed)
	}
}

func TestDatasetGroupService_DownloadSLD_NotFoundWhenAbsent(t *testing.T) {
	svc := NewDatasetGroupService(newStubGroupRepo(), newStubDatasetRepo(), newStubStore())
	g, err := svc.Create(context.Background(), DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := svc.DownloadSLD(context.Background(), g.ID); !errors.Is(err, ErrSLDNotFound) {
		t.Fatalf("want ErrSLDNotFound, got %v", err)
	}
}

func TestDatasetGroupService_Delete_RemovesSLDObject(t *testing.T) {
	ctx := context.Background()
	store := newStubStore()
	svc := NewDatasetGroupService(newStubGroupRepo(), newStubDatasetRepo(), store)
	g, err := svc.Create(ctx, DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.UploadSLD(ctx, g.ID, UploadSLDInput{
		Filename: "style.sld", ContentType: "application/xml", Size: 3, Reader: strings.NewReader("<x>"),
	}); err != nil {
		t.Fatal(err)
	}
	if err := svc.Delete(ctx, g.ID); err != nil {
		t.Fatal(err)
	}
	wantKey := "groups/" + g.ID.String() + "/sld/style.sld"
	if len(store.removed) != 1 || store.removed[0] != wantKey {
		t.Fatalf("want sld object removed, got %v", store.removed)
	}
	if _, err := svc.Get(ctx, g.ID); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("want ErrNotFound after delete, got %v", err)
	}
}

func TestDatasetGroupService_List_ClampsPageSize(t *testing.T) {
	svc := NewDatasetGroupService(newStubGroupRepo(), newStubDatasetRepo(), newStubStore())
	if _, err := svc.Create(context.Background(), DatasetGroupInput{Code: "pop", Name: "Population"}); err != nil {
		t.Fatal(err)
	}
	page, err := svc.List(context.Background(), nil, 0, 10_000)
	if err != nil {
		t.Fatal(err)
	}
	if page.Page != 1 || page.PageSize != MaxPageSize {
		t.Fatalf("want page 1 and page size %d, got %d/%d", MaxPageSize, page.Page, page.PageSize)
	}
	if page.Total != 1 {
		t.Fatalf("want total 1, got %d", page.Total)
	}
}

func TestDatasetGroupService_Update_RequiresSlug(t *testing.T) {
	svc := NewDatasetGroupService(newStubGroupRepo(), newStubDatasetRepo(), newStubStore())
	g, err := svc.Create(context.Background(), DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Update(context.Background(), g.ID, DatasetGroupInput{Code: "Bad Code", Name: "x"}); !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("want ErrValidation, got %v", err)
	}
}

func TestDatasetGroupService_UploadSLD_RejectsPathTraversal(t *testing.T) {
	ctx := context.Background()
	groups := newStubGroupRepo()
	store := newStubStore()
	svc := NewDatasetGroupService(groups, newStubDatasetRepo(), store)
	g, err := svc.Create(ctx, DatasetGroupInput{Code: "pop", Name: "Population"})
	if err != nil {
		t.Fatal(err)
	}

	updated, err := svc.UploadSLD(ctx, g.ID, UploadSLDInput{
		Filename: "../../other/sld/evil.sld", ContentType: "application/xml", Size: 3, Reader: strings.NewReader("<x>"),
	})
	if err != nil {
		t.Fatal(err)
	}
	wantPrefix := "groups/" + g.ID.String() + "/sld/"
	if updated.SldStorageKey == nil || !strings.HasPrefix(*updated.SldStorageKey, wantPrefix) ||
		!strings.HasSuffix(*updated.SldStorageKey, "evil.sld") {
		t.Fatalf("sld key escaped the group prefix: %v", updated.SldStorageKey)
	}
	if len(store.put) != 1 || !strings.HasPrefix(store.put[0], wantPrefix) {
		t.Fatalf("object stored outside the group sld prefix: %v", store.put)
	}
}
