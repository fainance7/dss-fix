package http

import "net/http"

// redocHTML renders the OpenAPI spec with Redoc (loaded from a CDN), which
// supports OpenAPI 3.1.
const redocHTML = `<!doctype html>
<html>
  <head>
    <title>GIS API</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <style>body { margin: 0; padding: 0; }</style>
  </head>
  <body>
    <redoc spec-url="/openapi.yaml"></redoc>
    <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
  </body>
</html>`

// openAPISpec serves the embedded OpenAPI 3.1.1 document.
func (deps RouterDeps) openAPISpec(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/yaml")
	_, _ = w.Write(deps.OpenAPISpec)
}

// docsUI serves the Redoc documentation page.
func docsUI(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(redocHTML))
}
