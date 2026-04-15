# Project Status

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
