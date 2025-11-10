#!/bin/bash

set -e

echo "Setting up SUMO-K8 local development environment..."
echo ""

SETUP_K8S="${SETUP_K8S:-false}"
if [ "$1" == "--with-k8s" ] || [ "$1" == "-k" ]; then
    SETUP_K8S="true"
fi

DB_NAME="sumo_k8"
DB_USER="${DB_USER:-$(whoami)}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

# Check if PostgreSQL is running
if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; then
    echo "⚠️  Warning: PostgreSQL doesn't appear to be running on $DB_HOST:$DB_PORT"
    echo "   Please start PostgreSQL and try again, or use Docker Compose:"
    echo "   docker-compose up -d db"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "Step 1: Setting up PostgreSQL database..."
echo "Creating database $DB_NAME..."
createdb -h $DB_HOST -p $DB_PORT -U $DB_USER $DB_NAME 2>/dev/null || echo "Database may already exist"

echo "Running schema.sql..."
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f schema.sql

echo "Setting DATABASE_URL..."
export DATABASE_URL="postgresql://$DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
echo "✓ Database setup complete!"
echo ""

if [ "$SETUP_K8S" == "true" ]; then
    echo "Step 2: Setting up Kubernetes cluster..."
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash "$SCRIPT_DIR/setup_k8s.sh"
    echo "✓ Kubernetes setup complete!"
    echo ""
fi

echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Database:"
echo "  DATABASE_URL=\"$DATABASE_URL\""
echo ""

if [ "$SETUP_K8S" == "true" ]; then
    echo "Kubernetes:"
    if command -v kind &> /dev/null && kind get clusters | grep -q "sumo-k8-cluster"; then
        echo "  Cluster: sumo-k8-cluster (kind)"
        echo "  export KUBECONFIG=/tmp/sumo-k8-kubeconfig"
    elif command -v minikube &> /dev/null && minikube status -p sumo-k8-cluster &> /dev/null; then
        echo "  Cluster: sumo-k8-cluster (minikube)"
        echo "  kubectl configured automatically"
    fi
    echo ""
fi

echo "To run the application:"
echo "  export DATABASE_URL=\"$DATABASE_URL\""
if [ "$SETUP_K8S" == "true" ] && [ -f "/tmp/sumo-k8-kubeconfig" ]; then
    echo "  export KUBECONFIG=/tmp/sumo-k8-kubeconfig"
fi
echo "  pip install -r requirements.txt"
echo "  python app.py"
echo ""
echo "Or use Docker Compose:"
echo "  docker-compose up"
echo ""
echo "Or use Docker:"
echo "  docker build -t sumo-k8-controller ."
echo "  docker run -e DATABASE_URL=\"$DATABASE_URL\" -p 8000:8000 sumo-k8-controller"
echo ""
echo "Health check:"
echo "  curl http://localhost:8000/health"
echo ""
if [ "$SETUP_K8S" != "true" ]; then
    echo "To also set up Kubernetes, run:"
    echo "  ./setup_local.sh --with-k8s"
    echo "  or"
    echo "  ./setup_k8s.sh"
fi

