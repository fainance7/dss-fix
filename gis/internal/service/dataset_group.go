package service

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"path"
	"path/filepath"
	"strings"

	"gis/internal/domain"

	"github.com/google/uuid"
)

// DatasetGroupRepository is the persistence behaviour DatasetGroupService needs.
type DatasetGroupRepository interface {
	Create(ctx context.Context, g domain.DatasetGroup) (domain.DatasetGroup, error)
	GetByID(ctx context.Context, id uuid.UUID) (domain.DatasetGroup, error)
	GetByCode(ctx context.Context, code string) (domain.DatasetGroup, error)
	List(ctx context.Context, code *string, limit, offset int) ([]domain.DatasetGroup, error)
	Count(ctx context.Context, code *string) (int, error)
	Update(ctx context.Context, g domain.DatasetGroup) (domain.DatasetGroup, error)
	Delete(ctx context.Context, id uuid.UUID) error
	SetSLD(ctx context.Context, id uuid.UUID, key, filename, contentType string) (domain.DatasetGroup, error)
}

// DatasetGroupInput carries the mutable fields of a group.
type DatasetGroupInput struct {
	Code        string
	Name        string
	Description string
}

// DatasetGroupPage is a page of groups.
type DatasetGroupPage struct {
	Items    []domain.DatasetGroup
	Page     int
	PageSize int
	Total    int
}

// DatasetGroupService implements dataset-group business rules.
type DatasetGroupService struct {
	repo     DatasetGroupRepository
	datasets DatasetRepository
	store    ObjectStore
}

// NewDatasetGroupService wires the group repository, the dataset repository
// (for the "latest" lookup) and the object store (for the group SLD).
func NewDatasetGroupService(repo DatasetGroupRepository, datasets DatasetRepository, store ObjectStore) *DatasetGroupService {
	return &DatasetGroupService{repo: repo, datasets: datasets, store: store}
}

// errInvalidGroupCode is the validation error returned for a non-slug code.
func errInvalidGroupCode() error {
	return fmt.Errorf("%w: code must be a slug (lowercase latin letters, digits, and dashes)", domain.ErrValidation)
}

// Create persists a new group after validating its code.
func (s *DatasetGroupService) Create(ctx context.Context, in DatasetGroupInput) (domain.DatasetGroup, error) {
	if !domain.ValidSlug(in.Code) {
		return domain.DatasetGroup{}, errInvalidGroupCode()
	}
	return s.repo.Create(ctx, domain.DatasetGroup{Code: in.Code, Name: in.Name, Description: in.Description})
}

// Get returns one group by id.
func (s *DatasetGroupService) Get(ctx context.Context, id uuid.UUID) (domain.DatasetGroup, error) {
	return s.repo.GetByID(ctx, id)
}

// List returns a page of groups; page/pageSize are clamped like datasets.
func (s *DatasetGroupService) List(ctx context.Context, code *string, page, pageSize int) (DatasetGroupPage, error) {
	if page < 1 {
		page = 1
	}
	if pageSize < 1 {
		pageSize = DefaultPageSize
	}
	if pageSize > MaxPageSize {
		pageSize = MaxPageSize
	}
	items, err := s.repo.List(ctx, code, pageSize, (page-1)*pageSize)
	if err != nil {
		return DatasetGroupPage{}, err
	}
	total, err := s.repo.Count(ctx, code)
	if err != nil {
		return DatasetGroupPage{}, err
	}
	return DatasetGroupPage{Items: items, Page: page, PageSize: pageSize, Total: total}, nil
}

// Update replaces a group's editable fields.
func (s *DatasetGroupService) Update(ctx context.Context, id uuid.UUID, in DatasetGroupInput) (domain.DatasetGroup, error) {
	if !domain.ValidSlug(in.Code) {
		return domain.DatasetGroup{}, errInvalidGroupCode()
	}
	return s.repo.Update(ctx, domain.DatasetGroup{ID: id, Code: in.Code, Name: in.Name, Description: in.Description})
}

// Delete removes a group (409 via ErrConflict while datasets reference it) and
// then best-effort removes its SLD object.
func (s *DatasetGroupService) Delete(ctx context.Context, id uuid.UUID) error {
	g, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return err
	}
	if err := s.repo.Delete(ctx, id); err != nil {
		return err
	}
	if g.SldStorageKey != nil {
		_ = s.store.Remove(ctx, *g.SldStorageKey) // best-effort: the row is gone
	}
	return nil
}

// Latest returns the freshest dataset of the group (greatest end_date, NULLs
// last, then newest), optionally restricted by status and file type.
func (s *DatasetGroupService) Latest(ctx context.Context, id uuid.UUID, status *string, fileType *domain.FileType) (domain.Dataset, error) {
	if _, err := s.repo.GetByID(ctx, id); err != nil {
		return domain.Dataset{}, err
	}
	return s.datasets.LatestInGroup(ctx, id, status, fileType)
}

// UploadSLD validates and stores the group's shared SLD, replacing any previous one.
func (s *DatasetGroupService) UploadSLD(ctx context.Context, id uuid.UUID, in UploadSLDInput) (domain.DatasetGroup, error) {
	g, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.DatasetGroup{}, err
	}

	ext := strings.ToLower(filepath.Ext(in.Filename))
	if !domain.ExtensionAllowedForSLD(ext) {
		return domain.DatasetGroup{}, fmt.Errorf("%w: sld file must have one of these extensions: %s",
			domain.ErrValidation, strings.Join(domain.AllowedSLDExtensions, ", "))
	}

	head := make([]byte, 512)
	n, err := io.ReadFull(in.Reader, head)
	if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
		return domain.DatasetGroup{}, fmt.Errorf("read sld upload: %w", err)
	}
	head = head[:n]
	if err := domain.ValidateFileContent(ext, head); err != nil {
		return domain.DatasetGroup{}, fmt.Errorf("%w: %s", domain.ErrValidation, err)
	}
	content := io.MultiReader(bytes.NewReader(head), in.Reader)

	name, err := safeObjectName(in.Filename)
	if err != nil {
		return domain.DatasetGroup{}, err
	}
	key := path.Join("groups", g.ID.String(), "sld", name)
	if err := s.store.Put(ctx, key, content, in.Size, in.ContentType); err != nil {
		return domain.DatasetGroup{}, err
	}
	updated, err := s.repo.SetSLD(ctx, id, key, in.Filename, in.ContentType)
	if err != nil {
		// Compensate: the row was not updated, so the object would be orphaned.
		_ = s.store.Remove(ctx, key)
		return domain.DatasetGroup{}, err
	}
	if g.SldStorageKey != nil && *g.SldStorageKey != key {
		_ = s.store.Remove(ctx, *g.SldStorageKey) // best-effort: drop the replaced file
	}
	return updated, nil
}

// DownloadSLD returns the group and a reader for its SLD; ErrSLDNotFound when
// none has been uploaded. The caller must close the reader.
func (s *DatasetGroupService) DownloadSLD(ctx context.Context, id uuid.UUID) (domain.DatasetGroup, io.ReadCloser, error) {
	g, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return domain.DatasetGroup{}, nil, err
	}
	if g.SldStorageKey == nil {
		return domain.DatasetGroup{}, nil, ErrSLDNotFound
	}
	obj, err := s.store.Get(ctx, *g.SldStorageKey)
	if err != nil {
		return domain.DatasetGroup{}, nil, err
	}
	return g, obj, nil
}
