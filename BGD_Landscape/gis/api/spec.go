// Package api embeds the OpenAPI 3.1.1 specification for the HTTP API.
package api

import _ "embed"

// Spec is the OpenAPI 3.1.1 document (YAML).
//
//go:embed openapi.yaml
var Spec []byte
