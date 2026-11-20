// Package httputil provides small, generic helpers for JSON HTTP handlers:
// response writing, request decoding, and validation-error formatting.
package httputil

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/go-playground/validator/v10"
)

// maxBodyBytes caps the size of a decoded JSON request body.
const maxBodyBytes = 1 << 20 // 1 MiB

// WriteJSON writes data as a JSON response with the given status code.
func WriteJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if data != nil {
		_ = json.NewEncoder(w).Encode(data)
	}
}

// ErrorResponse is the JSON error envelope.
type ErrorResponse struct {
	Error string `json:"error"`
}

// ValidationErrorResponse is the field-level validation error envelope.
type ValidationErrorResponse struct {
	Errors map[string]string `json:"errors"`
}

// WriteError writes a JSON error envelope: {"error": "..."}.
func WriteError(w http.ResponseWriter, status int, msg string) {
	WriteJSON(w, status, ErrorResponse{Error: msg})
}

// DecodeJSON reads and validates a JSON body into a value of type T. It caps the
// body size and rejects unknown fields.
func DecodeJSON[T any](w http.ResponseWriter, r *http.Request) (T, error) {
	var v T
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)

	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(&v); err != nil {
		return v, err
	}
	return v, nil
}

// WriteValidationErrors renders validator.ValidationErrors as a field->message
// map under {"errors": {...}} with a 422 status.
func WriteValidationErrors(w http.ResponseWriter, err error) {
	var ve validator.ValidationErrors
	if !errors.As(err, &ve) {
		WriteError(w, http.StatusBadRequest, "invalid request")
		return
	}

	problems := make(map[string]string, len(ve))
	for _, fe := range ve {
		problems[fe.Field()] = messageForTag(fe)
	}
	WriteJSON(w, http.StatusUnprocessableEntity, ValidationErrorResponse{Errors: problems})
}

func messageForTag(fe validator.FieldError) string {
	switch fe.Tag() {
	case "required":
		return "is required"
	case "email":
		return "must be a valid email address"
	case "uuid", "uuid4":
		return "must be a valid UUID"
	case "min":
		return fmt.Sprintf("must be at least %s characters", fe.Param())
	case "max":
		return fmt.Sprintf("must be at most %s characters", fe.Param())
	case "gte":
		return fmt.Sprintf("must be %s or greater", fe.Param())
	case "lte":
		return fmt.Sprintf("must be %s or less", fe.Param())
	case "oneof":
		return fmt.Sprintf("must be one of: %s", fe.Param())
	default:
		return "is invalid"
	}
}
