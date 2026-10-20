// Package domain holds the core entities, enums, and sentinel errors shared by
// every layer. It has no dependencies on other internal packages.
package domain

import "errors"

var (
	// ErrNotFound is returned when a requested entity does not exist.
	ErrNotFound = errors.New("not found")
	// ErrConflict is returned when an operation violates a constraint, e.g. a
	// foreign-key reference or a uniqueness rule.
	ErrConflict = errors.New("conflict")
	// ErrValidation is returned when input fails a business rule (as opposed to
	// request-shape validation, which the transport layer handles).
	ErrValidation = errors.New("validation failed")
)
