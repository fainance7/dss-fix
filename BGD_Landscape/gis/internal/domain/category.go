package domain

import (
	"regexp"
	"time"

	"github.com/google/uuid"
)

// Category is a hierarchical grouping for datasets. A category may have a parent
// category (nil for a root) and many child categories.
type Category struct {
	ID          uuid.UUID  `json:"id"`
	ParentID    *uuid.UUID `json:"parent_id"`
	Code        string     `json:"code"`
	Name        string     `json:"name"`
	Description string     `json:"description"`
	CreatedAt   time.Time  `json:"created_at"`
	UpdatedAt   time.Time  `json:"updated_at"`
}

// slugPattern matches a slug: lowercase latin letters and digits in
// dash-separated groups, e.g. "population", "land-use-2024".
var slugPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

// ValidSlug reports whether s is a valid slug (lowercase latin letters, digits,
// and single dashes between groups; no leading, trailing, or repeated dashes).
func ValidSlug(s string) bool {
	return slugPattern.MatchString(s)
}
