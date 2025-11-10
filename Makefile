.PHONY: help setup setup-k8s run test clean docker-build docker-run

help:
	@echo "SUMO-K8 Controller - Makefile"
	@echo ""
	@echo "Commands:"
	@echo "  make setup          - Setup database only"
	@echo "  make setup-k8s     - Setup database + Kubernetes"
	@echo "  make run           - Run the application"
	@echo "  make test          - Run API tests"
	@echo "  make clean         - Clean Python cache files"
	@echo "  make docker-build  - Build Docker image"
	@echo "  make docker-run    - Run with Docker Compose"

setup:
	@./setup_local.sh

setup-k8s:
	@./setup_local.sh --with-k8s

run:
	@export DATABASE_URL="postgresql://$$(whoami)@localhost/sumo_k8" && \
	 python app.py

test:
	@./scripts/test_api.sh

clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "✓ Cleaned Python cache files"

docker-build:
	@docker build -t sumo-k8-controller:latest .

docker-run:
	@docker-compose up

docker-stop:
	@docker-compose down

