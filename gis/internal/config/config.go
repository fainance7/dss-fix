// Package config loads application configuration from the environment.
package config

import (
	"fmt"
	"strings"
	"time"

	"github.com/caarlos0/env/v11"
	"github.com/joho/godotenv"
)

// Config holds all configuration for the application. Values are read from
// environment variables; a local .env file (if present) is loaded first.
type Config struct {
	HTTP      HTTPConfig
	DB        DBConfig
	S3        S3Config
	RabbitMQ  RabbitMQConfig
	Landscape LandscapeConfig
}

// HTTPConfig configures the HTTP server.
type HTTPConfig struct {
	Port              int           `env:"PORT" envDefault:"8080"`
	ReadHeaderTimeout time.Duration `env:"HTTP_READ_HEADER_TIMEOUT" envDefault:"5s"`
	ReadTimeout       time.Duration `env:"HTTP_READ_TIMEOUT" envDefault:"120s"`
	WriteTimeout      time.Duration `env:"HTTP_WRITE_TIMEOUT" envDefault:"120s"`
	IdleTimeout       time.Duration `env:"HTTP_IDLE_TIMEOUT" envDefault:"60s"`
	ShutdownTimeout   time.Duration `env:"HTTP_SHUTDOWN_TIMEOUT" envDefault:"10s"`

	// APIKey is the shared-secret key clients must present in the X-API-Key
	// header on every non-health endpoint. Required (>=32 chars) unless
	// AuthDisabled is set explicitly for local development.
	APIKey string `env:"API_KEY"`
	// AuthDisabled turns API-key auth off. Local development only; it must
	// never be set in a shared or production deployment (audit C-2).
	AuthDisabled bool `env:"API_AUTH_DISABLED" envDefault:"false"`
	// DocsEnabled serves /docs and /openapi.yaml without a key. Off by default
	// (Этап 7): enable on a development or integration stand only.
	DocsEnabled bool `env:"API_DOCS_ENABLED" envDefault:"false"`
	// ReadKeys are optional read-only API keys (GET/HEAD only), one per
	// consumer system that only reads data (Этап 7, ТЗ п. 4.5).
	ReadKeys []string `env:"API_READ_KEYS" envSeparator:","`
}

// MinAPIKeyLen is the minimum accepted length of API_KEY.
const MinAPIKeyLen = 32

// ValidateAuth fails fast when the HTTP API would start without
// authentication (audit C-2): an empty API_KEY is an error unless
// API_AUTH_DISABLED=true was set on purpose.
func (c HTTPConfig) ValidateAuth() error {
	if c.AuthDisabled {
		return nil
	}
	if c.APIKey == "" {
		return fmt.Errorf("API_KEY is not set: refusing to serve an unauthenticated API (set API_KEY, or API_AUTH_DISABLED=true for local dev only)")
	}
	if len(c.APIKey) < MinAPIKeyLen {
		return fmt.Errorf("API_KEY is too short: need at least %d characters", MinAPIKeyLen)
	}
	for _, k := range c.ReadKeys {
		if len(strings.TrimSpace(k)) < MinAPIKeyLen {
			return fmt.Errorf("API_READ_KEYS: every key needs at least %d characters", MinAPIKeyLen)
		}
	}
	return nil
}

// Addr returns the listen address for the HTTP server.
func (c HTTPConfig) Addr() string {
	return fmt.Sprintf(":%d", c.Port)
}

// DBConfig configures the Postgres connection.
type DBConfig struct {
	URL string `env:"DB_URL,required"`
	// Schema is the Postgres schema migrations operate on. It is used by
	// `migrate fresh` to know which schema to drop and recreate; in production
	// this may be something other than "public".
	Schema string `env:"DB_SCHEMA" envDefault:"public"`
}

// S3Config configures the S3/MinIO object store.
type S3Config struct {
	Endpoint  string `env:"S3_ENDPOINT,required"`
	AccessKey string `env:"S3_ACCESS_KEY,required"`
	SecretKey string `env:"S3_SECRET_KEY,required"`
	Bucket    string `env:"S3_BUCKET" envDefault:"geofiles"`
	UseSSL    bool   `env:"S3_USE_SSL" envDefault:"false"`
}

// RabbitMQConfig configures the RabbitMQ connection and example topology.
type RabbitMQConfig struct {
	URL      string `env:"RABBITMQ_URL,required"`
	Exchange string `env:"RABBITMQ_EXCHANGE" envDefault:"gis.events"`
	Queue    string `env:"RABBITMQ_QUEUE" envDefault:"gis.events.example"`
}

// LandscapeConfig bounds the Landscape Program's parallelism, and with it the
// worker's peak memory: a run holds up to MaxConcurrentNodes nodes in flight,
// each running a pool of MaxWorkers/MaxConcurrentNodes goroutines that own one
// window buffer per input and output. The defaults (8 total workers spread
// over 2 nodes) keep that product bounded regardless of how many cores the
// host has.
type LandscapeConfig struct {
	// MaxWorkers is the total window-worker budget shared by all nodes of a run.
	MaxWorkers int `env:"LP_MAX_WORKERS" envDefault:"8"`
	// MaxConcurrentNodes is how many graph nodes of one stage run at a time.
	MaxConcurrentNodes int `env:"LP_MAX_NODES" envDefault:"2"`
}

// Load reads configuration from the environment, loading an optional .env file
// from the current working directory first.
func Load() (*Config, error) {
	// A missing .env file is not an error: in production we rely on real env vars.
	_ = godotenv.Load()

	cfg := &Config{}
	if err := env.Parse(cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	return cfg, nil
}
