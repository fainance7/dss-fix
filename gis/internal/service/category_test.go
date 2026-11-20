package service

import (
	"context"
	"errors"
	"testing"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// stubCategoryRepo is an in-memory CategoryRepository for tests.
type stubCategoryRepo struct {
	store map[uuid.UUID]domain.Category
}

func newStubCategoryRepo() *stubCategoryRepo {
	return &stubCategoryRepo{store: map[uuid.UUID]domain.Category{}}
}

func (r *stubCategoryRepo) Create(_ context.Context, c domain.Category) (domain.Category, error) {
	if c.ID == uuid.Nil {
		c.ID = uuid.New()
	}
	r.store[c.ID] = c
	return c, nil
}

func (r *stubCategoryRepo) GetByID(_ context.Context, id uuid.UUID) (domain.Category, error) {
	c, ok := r.store[id]
	if !ok {
		return domain.Category{}, domain.ErrNotFound
	}
	return c, nil
}

func (r *stubCategoryRepo) List(_ context.Context, _ *uuid.UUID) ([]domain.Category, error) {
	return nil, nil
}

func (r *stubCategoryRepo) Update(_ context.Context, c domain.Category) (domain.Category, error) {
	r.store[c.ID] = c
	return c, nil
}

func (r *stubCategoryRepo) Delete(_ context.Context, id uuid.UUID) error {
	delete(r.store, id)
	return nil
}

func TestCategoryService_Create(t *testing.T) {
	ctx := context.Background()

	t.Run("root category succeeds", func(t *testing.T) {
		svc := NewCategoryService(newStubCategoryRepo())
		got, err := svc.Create(ctx, CategoryInput{Code: "root", Name: "root"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got.Name != "root" || got.ParentID != nil {
			t.Fatalf("unexpected category: %+v", got)
		}
	})

	t.Run("invalid code is a validation error", func(t *testing.T) {
		svc := NewCategoryService(newStubCategoryRepo())
		for _, code := range []string{"", "Root", "with space", "-leading", "double--dash"} {
			if _, err := svc.Create(ctx, CategoryInput{Code: code, Name: "x"}); !errors.Is(err, domain.ErrValidation) {
				t.Fatalf("code %q: want ErrValidation, got %v", code, err)
			}
		}
	})

	t.Run("missing parent is a validation error", func(t *testing.T) {
		svc := NewCategoryService(newStubCategoryRepo())
		missing := uuid.New()
		_, err := svc.Create(ctx, CategoryInput{Code: "child", Name: "child", ParentID: &missing})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("existing parent succeeds", func(t *testing.T) {
		repo := newStubCategoryRepo()
		svc := NewCategoryService(repo)
		root, _ := svc.Create(ctx, CategoryInput{Code: "root", Name: "root"})

		child, err := svc.Create(ctx, CategoryInput{Code: "child", Name: "child", ParentID: &root.ID})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if child.ParentID == nil || *child.ParentID != root.ID {
			t.Fatalf("child not linked to parent: %+v", child)
		}
	})
}

func TestCategoryService_Update_PreventsCycles(t *testing.T) {
	ctx := context.Background()
	repo := newStubCategoryRepo()
	svc := NewCategoryService(repo)

	root, _ := svc.Create(ctx, CategoryInput{Code: "root", Name: "root"})
	child, _ := svc.Create(ctx, CategoryInput{Code: "child", Name: "child", ParentID: &root.ID})

	t.Run("category cannot be its own parent", func(t *testing.T) {
		_, err := svc.Update(ctx, root.ID, CategoryInput{Code: "root", Name: "root", ParentID: &root.ID})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("category cannot descend from its own child", func(t *testing.T) {
		_, err := svc.Update(ctx, root.ID, CategoryInput{Code: "root", Name: "root", ParentID: &child.ID})
		if !errors.Is(err, domain.ErrValidation) {
			t.Fatalf("want ErrValidation, got %v", err)
		}
	})

	t.Run("valid reparent succeeds", func(t *testing.T) {
		other, _ := svc.Create(ctx, CategoryInput{Code: "other", Name: "other"})
		updated, err := svc.Update(ctx, child.ID, CategoryInput{Code: "child", Name: "child", ParentID: &other.ID})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if updated.ParentID == nil || *updated.ParentID != other.ID {
			t.Fatalf("reparent failed: %+v", updated)
		}
	})
}

func TestCategoryService_Update_MissingCategory(t *testing.T) {
	svc := NewCategoryService(newStubCategoryRepo())
	_, err := svc.Update(context.Background(), uuid.New(), CategoryInput{Code: "x", Name: "x"})
	if !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}
