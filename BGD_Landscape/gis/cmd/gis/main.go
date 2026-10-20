// Command gis is the entrypoint for the GIS application.
//
// It exposes subcommands for running the HTTP server, the RabbitMQ worker, and
// database migrations. See `gis --help` for details.
package main

import "gis/internal/cli"

func main() {
	cli.Execute()
}
