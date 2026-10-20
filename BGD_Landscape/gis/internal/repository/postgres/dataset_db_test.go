package postgres

import (
	"context"
	"errors"
	"testing"
	"time"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// TestDatasetRepository_GroupsPeriodsAndLatest exercises the Postgres-only
// parts of the dataset/group repositories added with dataset periods: the
// ::date casts on *string parameters, the date filter clause, and the
// "freshest in group" ordering (end_date DESC NULLS LAST, created_at DESC).
func TestDatasetRepository_GroupsPeriodsAndLatest(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	groups := NewDatasetGroupRepository(pool)
	datasets := NewDatasetRepository(pool)
	categories := NewCategoryRepository(pool)

	suffix := uuid.NewString()[:8]
	cat, err := categories.Create(ctx, domain.Category{Code: "dbtest-" + suffix, Name: "db test"})
	if err != nil {
		t.Fatalf("create category: %v", err)
	}
	t.Cleanup(func() { _, _ = pool.Exec(ctx, `DELETE FROM categories WHERE id = $1`, cat.ID) })

	group, err := groups.Create(ctx, domain.DatasetGroup{Code: "dbtest-group-" + suffix, Name: "db test group"})
	if err != nil {
		t.Fatalf("create group: %v", err)
	}
	t.Cleanup(func() { _, _ = pool.Exec(ctx, `DELETE FROM dataset_groups WHERE id = $1`, group.ID) })

	s := func(v string) *string { return &v }
	mk := func(name string, start, end *string) domain.Dataset {
		d, err := datasets.Create(ctx, domain.Dataset{
			CategoryID: cat.ID, DatasetGroupID: &group.ID, Name: name, Filename: name + ".tif",
			StorageKey: "dbtest/" + suffix + "/" + name, FileType: domain.FileTypeRaster,
			ContentType: "image/tiff", Status: domain.DatasetStatusReady,
			StartDate: start, EndDate: end,
		})
		if err != nil {
			t.Fatalf("create dataset %s: %v", name, err)
		}
		t.Cleanup(func() { _, _ = pool.Exec(ctx, `DELETE FROM datasets WHERE id = $1`, d.ID) })
		time.Sleep(5 * time.Millisecond) // distinct created_at for the tie-break
		return d
	}

	old := mk("old", s("2019-01-01"), s("2019-12-31"))
	fresh := mk("fresh", s("2020-01-01"), s("2020-12-31"))
	undated := mk("undated", nil, nil)

	if old.StartDate == nil || *old.StartDate != "2019-01-01" || *old.EndDate != "2019-12-31" || old.DatasetGroupID == nil {
		t.Fatalf("dates/group not round-tripped: %+v", old)
	}

	latest, err := datasets.LatestInGroup(ctx, group.ID, nil, nil)
	if err != nil {
		t.Fatalf("LatestInGroup: %v", err)
	}
	if latest.ID != fresh.ID {
		t.Fatalf("latest = %s, want fresh %s (undated %s must sort last)", latest.Name, fresh.ID, undated.ID)
	}
	ft := domain.FileTypeVector
	if _, err := datasets.LatestInGroup(ctx, group.ID, nil, &ft); !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("no vector in group: err = %v, want ErrNotFound", err)
	}

	page, err := datasets.ListSummaries(ctx, domain.DatasetFilter{
		DatasetGroupID: &group.ID, EndDateFrom: s("2020-01-01"), EndDateTo: s("2020-12-31"),
	}, 10, 0)
	if err != nil {
		t.Fatalf("ListSummaries with date bounds: %v", err)
	}
	if len(page) != 1 || page[0].ID != fresh.ID {
		t.Fatalf("date filter returned %+v, want only fresh", page)
	}
	n, err := datasets.Count(ctx, domain.DatasetFilter{DatasetGroupID: &group.ID, StartDateFrom: s("2019-06-01")})
	if err != nil || n != 1 {
		t.Fatalf("Count(start_date_from) = %d, %v; want 1", n, err)
	}

	updated, err := datasets.UpdateFields(ctx, undated.ID, domain.DatasetFields{
		CategoryID: cat.ID, Name: "undated-now-dated", DatasetGroupID: &group.ID,
		StartDate: s("2021-01-01"), EndDate: s("2021-12-31"),
	})
	if err != nil {
		t.Fatalf("UpdateFields: %v", err)
	}
	if updated.EndDate == nil || *updated.EndDate != "2021-12-31" {
		t.Fatalf("UpdateFields did not store the period: %+v", updated)
	}
	if _, err := datasets.UpdateFields(ctx, undated.ID, domain.DatasetFields{
		CategoryID: cat.ID, Name: "bad", StartDate: s("2021-12-31"), EndDate: s("2021-01-01"),
	}); !errors.Is(err, domain.ErrValidation) {
		t.Fatalf("reversed period must hit the CHECK constraint: err = %v", err)
	}

	if err := groups.Delete(ctx, group.ID); !errors.Is(err, domain.ErrConflict) {
		t.Fatalf("deleting a referenced group: err = %v, want ErrConflict", err)
	}
	g2, err := groups.SetSLD(ctx, group.ID, "k", "style.sld", "application/xml")
	if err != nil || g2.SldStorageKey == nil || *g2.SldStorageKey != "k" {
		t.Fatalf("SetSLD: %+v, %v", g2, err)
	}
	byCode, err := groups.GetByCode(ctx, group.Code)
	if err != nil || byCode.ID != group.ID {
		t.Fatalf("GetByCode: %+v, %v", byCode, err)
	}
}
