# Queue Dashboard API

Read-only API for SUMO-K8 queue stats, backed by the SUMO-K8 Postgres database.

## Endpoints
- `GET /stats`
- `GET /time_series?minutes=60`
- `GET /recent_jobs?limit=50`

## Environment
- `DATABASE_URL` (required) - from existing Kubernetes secret `sumo-k8-secrets` key `DATABASE_URL`
- `DB_POOL_MIN` (default: `2`)
- `DB_POOL_MAX` (default: `10`)
- `CACHE_TTL_SECONDS` (default: `5`)

