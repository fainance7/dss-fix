package domain

import (
	"time"

	"github.com/google/uuid"
)

// DatasetGroup groups "одноимённые" datasets (the same indicator delivered for
// different periods). The freshest dataset of a group is the one with the
// greatest end_date. A group may carry a shared SLD style used as a fallback by
// datasets that have none of their own.
type DatasetGroup struct {
	ID             uuid.UUID `json:"id"`
	Code           string    `json:"code"`
	Name           string    `json:"name"`
	Description    string    `json:"description"`
	SldStorageKey  *string   `json:"sld_storage_key"`
	SldFilename    *string   `json:"sld_filename"`
	SldContentType *string   `json:"sld_content_type"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}
