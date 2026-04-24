# Project Status

## 2026-04-16
- Fixed tenant namespace startup reliability for simulation jobs:
  - Added `serviceaccounts` permissions to controller RBAC manifests (`k8s/serviceaccount.yaml`, `k8s/rbac.yaml`) so controller can create `simulation-runner` in org namespaces.
- Backfilled missing `simulation-runner` service accounts in existing org namespaces (`org-3` to `org-9`) after `org-2` incident.
- Added cost-safe AZ scheduling defaults (prefer + fallback, no hard lock):
  - Added `SIMULATION_PREFERRED_ZONES` config support in `src/config.py`.
  - Updated `src/jobs.py` scheduler to use preferred node affinity on `topology.kubernetes.io/zone` while retaining existing node selector behavior.
  - Set default preferred order in `k8s/configmap.yaml` to `eu-central-2c,eu-central-2a,eu-central-2b`.
  - Constrained simulation node pool to valid fallback zones in `k8s/karpenter-nodepool-simulation.yaml` (`2c`,`2a`,`2b`) to avoid strict single-AZ failure risk.

## 2026-04-15
- Added Step Functions task-token callback support in `src/jobs.py` by accepting and persisting `task_token` in `scenario_data`.
- Added JSON `/jobs/s3` endpoint in `app.py` to support backend payloads (`scenario_id`, `sumo_files_s3_url`, optional `task_token`).
- Added Step Functions callback sender in `src/reconciler.py`:
  - Sends `SendTaskSuccess` when job reaches `SUCCEEDED`
  - Sends `SendTaskFailure` when job reaches `FAILED`
- Enforced EU region defaults in `src/config.py`:
  - `S3_REGION` default set to `eu-central-2`
  - `AWS_REGION` follows env or `S3_REGION`
- Redeployed controller image to ECR and rolled out `sumo-k8-controller` deployment successfully.
- Added list-based S3 input support for `/jobs/s3` in `app.py`: payload can now pass `sumo_files_s3_urls` (array) as an alternative to `sumo_files_s3_url`.
- Updated `src/jobs.py` queue path to support list-based S3 inputs:
  - Stores `s3_file_urls` in `scenario_data`
  - Defers zip assembly to queued dispatch (`_dispatch_one_queued`) via `_build_zip_from_s3_urls(...)` instead of blocking submit request.
- Added webhook progress metadata plumbing for `/jobs/s3` in `app.py` and `src/jobs.py`:
  - Accepts/stores `progress_webhook_url`, `progress_simulation_id`, `progress_start_sec`, `progress_end_sec`, `premium_sim` in `scenario_data`.
- Added running-step progress webhook integration in `src/reconciler.py`:
  - Parses latest `sumo_progress` step from pod JSON logs.
  - Computes progress percent from `(step - start_sec) / (end_sec - start_sec)`.
  - Sends throttled webhook updates (only on percent increase) with `status_type=progress`, `simulation_status=Running`.
- Fixed progress webhook reliability in `src/reconciler.py`:
  - `_send_progress_webhook` now returns success/failure and `_LAST_PROGRESS_SENT` advances only on successful send.
  - Added explicit warning when webhook config is missing.
  - Added initial `0%` webhook send while running if no step is available yet.
  - Added optional insecure TLS retry for cert-verification failures (`PROGRESS_WEBHOOK_INSECURE_TLS=true`).
- Fixed controller image architecture deployment issue:
  - Rebuilt and pushed `sumo-k8-controller:latest` as `linux/amd64` to ECR.
  - Restarted rollout for `sumo-k8-controller`; pod is now `Running` and `Ready`.
- Fixed `Database error: 0` in `src/reconciler.py` ConfigMap cleanup query:
  - Updated `row[0]` to `row["job_id"]` for `RealDictCursor` rows.
- Redeployed controller after this fix:
  - Rebuilt/pushed amd64 image and rolled `sumo-k8-controller` successfully.
