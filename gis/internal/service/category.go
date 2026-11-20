// Package service holds the application's business logic. Services depend on
// repository and storage interfaces (declared here) rather than concrete types,
// and they translate between transport input and domain entities.
package service

import (
	"context"
	"errors"
	"fmt"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// CategoryRepository is the persistence behaviour CategoryService needs.
type CategoryRepository interface {
	Create(ctx context.Context, c domain.Category) (domain.Category, error)
	GetByID(ctx context.Context, id uuid.UUID) (domain.Category, error)
	List(ctx context.Context, parentID *uuid.UUID) ([]domain.Category, error)
	Update(ctx context.Context, c domain.Category) (domain.Category, error)
	Delete(ctx context.Context, id uuid.UUID) error
}

// CategoryInput carries the mutable fields of a category.
type CategoryInput struct {
	ParentID    *uuid.UUID
	Code        string
	Name        string
	Description string
}

// CategoryService implements category business rules.
type CategoryService struct {
	repo CategoryRepository
}

// NewCategoryService returns a CategoryService backed by repo.
func NewCategoryService(repo CategoryRepository) *CategoryService {
	return &CategoryService{repo: repo}
}

// Create validates the code and parent (if any) and stores a new category.
func (s *CategoryService) Create(ctx context.Context, in CategoryInput) (domain.Category, error) {
	if !domain.ValidSlug(in.Code) {
		return domain.Category{}, fmt.Errorf("%w: code must be a slug (lowercase latin letters, digits, and dashes)", domain.ErrValidation)
	}
	if err := s.ensureParentExists(ctx, in.ParentID); err != nil {
		return domain.Category{}, err
	}
	return s.repo.Create(ctx, domain.Category{
		ParentID:    in.ParentID,
		Code:        in.Code,
		Name:        in.Name,
		Description: in.Description,
	})
}

// Get returns a category by id.
func (s *CategoryService) Get(ctx context.Context, id uuid.UUID) (domain.Category, error) {
	return s.repo.GetByID(ctx, id)
}

// List returns categories, optionally filtered to a parent's direct children.
func (s *CategoryService) List(ctx context.Context, parentID *uuid.UUID) ([]domain.Category, error) {
	return s.repo.List(ctx, parentID)
}

// Update validates the code and parent change (existence + no cycles) and stores it.
func (s *CategoryService) Update(ctx context.Context, id uuid.UUID, in CategoryInput) (domain.Category, error) {
	if !domain.ValidSlug(in.Code) {
		return domain.Category{}, fmt.Errorf("%w: code must be a slug (lowercase latin letters, digits, and dashes)", domain.ErrValidation)
	}
	if _, err := s.repo.GetByID(ctx, id); err != nil {
		return domain.Category{}, err
	}
	if err := s.ensureParentExists(ctx, in.ParentID); err != nil {
		return domain.Category{}, err
	}
	if err := s.ensureNoCycle(ctx, id, in.ParentID); err != nil {
		return domain.Category{}, err
	}
	return s.repo.Update(ctx, domain.Category{
		ID:          id,
		ParentID:    in.ParentID,
		Code:        in.Code,
		Name:        in.Name,
		Description: in.Description,
	})
}

// Delete removes a category.
func (s *CategoryService) Delete(ctx context.Context, id uuid.UUID) error {
	return s.repo.Delete(ctx, id)
}

func (s *CategoryService) ensureParentExists(ctx context.Context, parentID *uuid.UUID) error {
	if parentID == nil {
		return nil
	}
	if _, err := s.repo.GetByID(ctx, *parentID); err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return fmt.Errorf("%w: parent category does not exist", domain.ErrValidation)
		}
		return err
	}
	return nil
}

// ensureNoCycle walks up the proposed parent's ancestry; if it reaches id, the
// move would create a cycle.
func (s *CategoryService) ensureNoCycle(ctx context.Context, id uuid.UUID, parentID *uuid.UUID) error {
	cursor := parentID
	for cursor != nil {
		if *cursor == id {
			return fmt.Errorf("%w: category cannot be its own ancestor", domain.ErrValidation)
		}
		parent, err := s.repo.GetByID(ctx, *cursor)
		if err != nil {
			return err
		}
		cursor = parent.ParentID
	}
	return nil
}
