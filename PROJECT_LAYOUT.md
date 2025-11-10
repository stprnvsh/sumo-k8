# SUMO-K8 Project Layout

## Directory Structure

```
sumo-k8/
├── app.py                    # Main FastAPI application
├── src/                      # Source modules
│   ├── __init__.py
│   ├── auth.py              # Authentication & tenant management
│   ├── jobs.py              # Job submission & management
│   ├── scaling.py           # K8s resource management & scaling
│   ├── logs.py              # Log streaming
│   ├── database.py          # Database connection pooling
│   ├── k8s_client.py       # Kubernetes client initialization
│   ├── reconciler.py       # Background job status sync
│   ├── config.py            # Configuration management
│   └── models.py            # Pydantic models
│
├── docs/                     # Documentation
│   ├── README.md            # Documentation index
│   ├── ARCHITECTURE.md      # Architecture documentation
│   ├── TECHNICAL_SPEC.md    # Technical specification
│   ├── PRODUCTION.md        # Production deployment guide
│   ├── README_SETUP.md      # Setup instructions
│   ├── FEATURES.md          # Feature list
│   └── PROJECT_STRUCTURE.md # Project status tracker
│
├── scripts/                  # Utility scripts
│   └── test_api.sh          # API testing script
│
├── test_networks/            # SUMO test scenarios
│   ├── README.md
│   └── ready_to_use/        # Ready-to-use test networks
│       ├── square.zip
│       ├── cross.zip
│       ├── corridor.zip
│       └── ...
│
├── schema.sql               # Database schema
├── requirements.txt         # Python dependencies
├── Dockerfile              # Container image definition
├── docker-compose.yml      # Local development setup
├── Makefile               # Development commands
├── .gitignore             # Git ignore rules
├── .editorconfig          # Editor configuration
├── .dockerignore          # Docker ignore rules
│
├── setup_local.sh         # Local setup script
├── setup_k8s.sh          # Kubernetes setup script
│
├── README.md              # Main README
├── CONTRIBUTING.md        # Contribution guidelines
└── PROJECT_LAYOUT.md      # This file
```

## File Descriptions

### Core Application
- **app.py** - FastAPI application with all endpoints
- **src/** - Modular source code

### Configuration
- **schema.sql** - PostgreSQL database schema
- **requirements.txt** - Python dependencies
- **.env.example** - Environment variable template (if exists)

### Docker
- **Dockerfile** - Production container image
- **docker-compose.yml** - Local development environment
- **.dockerignore** - Files to exclude from Docker builds

### Documentation
- **README.md** - Quick start and overview
- **docs/** - Detailed documentation
- **CONTRIBUTING.md** - Contribution guidelines

### Scripts
- **setup_local.sh** - Database setup
- **setup_k8s.sh** - Kubernetes cluster setup
- **scripts/test_api.sh** - API testing

### Testing
- **test_networks/** - SUMO test scenarios for integration testing

## Module Organization

### `src/auth.py`
- API key generation
- Tenant authentication
- Tenant CRUD operations

### `src/jobs.py`
- Job submission
- Job status retrieval
- K8s Job creation

### `src/scaling.py`
- Namespace/quota management
- Cluster monitoring
- Resource cleanup

### `src/logs.py`
- Log streaming (SSE)
- Log retrieval

### `src/database.py`
- Connection pooling
- Transaction management

### `src/k8s_client.py`
- K8s client initialization
- Availability detection

### `src/reconciler.py`
- Background job status sync
- ConfigMap cleanup

### `src/config.py`
- Environment variables
- Default values

### `src/models.py`
- Pydantic models
- Request/response validation

