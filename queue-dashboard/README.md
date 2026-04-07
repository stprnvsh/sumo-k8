# SUMO-K8 Queue Dashboard

Standalone dashboard for the SUMO-K8 Postgres DB. It shows queue counts, submission volume, and recent jobs.

## Services
- `queue-dashboard-api` (ClusterIP): `http://queue-dashboard-api:8000`
  - `GET /stats`
  - `GET /time_series?minutes=60`
  - `GET /recent_jobs?limit=50`
- `queue-dashboard-web` (LoadBalancer): external UI URL

## Environment variables
For the API (from secret `sumo-k8-secrets`):
- `DATABASE_URL`
- `CACHE_TTL_SECONDS` (optional, default `5`)

For the web:
- `DASHBOARD_API_URL` (optional, default `http://queue-dashboard-api:8000`)

## Verify (in cluster)
- API:
  - `POD=$(kubectl -n sumo-k8 get pods -l app.kubernetes.io/name=queue-dashboard-api -o jsonpath='{.items[0].metadata.name}'); kubectl -n sumo-k8 exec $POD -- curl -s http://localhost:8000/stats`
- Web:
  - `kubectl -n sumo-k8 get svc queue-dashboard-web`

