BINARY    := gis
PKG       := ./cmd/gis
BIN_DIR   := bin
COMPOSE   := docker compose -f deployments/docker-compose.yml

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: build
build: ## Build the binary into ./bin
	go build -o $(BIN_DIR)/$(BINARY) $(PKG)

.PHONY: run
run: ## Run the HTTP server
	go run $(PKG) serve

.PHONY: worker
worker: ## Run the RabbitMQ worker
	go run $(PKG) worker

.PHONY: test
test: ## Run unit tests
	go test ./...

.PHONY: cover
cover: ## Run tests with coverage summary
	go test -cover ./...

.PHONY: vet
vet: ## Run go vet
	go vet ./...

.PHONY: fmt
fmt: ## Format the code
	gofmt -w cmd internal pkg

.PHONY: lint
lint: ## Run golangci-lint (requires golangci-lint installed)
	golangci-lint run

.PHONY: tidy
tidy: ## Tidy go.mod / go.sum
	go mod tidy

.PHONY: check
check: vet test ## Run vet and tests

.PHONY: migrate-up
migrate-up: ## Apply all migrations
	go run $(PKG) migrate up

.PHONY: migrate-fresh
migrate-fresh: ## Drop the schema and re-apply all migrations
	go run $(PKG) migrate fresh

.PHONY: migrate-status
migrate-status: ## Show migration status
	go run $(PKG) migrate status

.PHONY: up
up: ## Start infrastructure (postgres, minio, rabbitmq)
	$(COMPOSE) up -d postgres minio rabbitmq

.PHONY: down
down: ## Stop infrastructure
	$(COMPOSE) down

.PHONY: docker-build
docker-build: ## Build the application image
	docker build -f build/package/Dockerfile -t $(BINARY):latest .
