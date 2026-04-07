#!/usr/bin/env python3
"""Upload simulation results to S3."""
import boto3
import os
import sys
from pathlib import Path


def upload_results():
    bucket = os.environ.get('S3_BUCKET')
    if not bucket:
        print("S3_BUCKET not set, skipping upload")
        return 0

    region = os.environ.get('S3_REGION', 'us-east-1')
    job_id = os.environ.get('JOB_ID', 'unknown')
    tenant_id = os.environ.get('TENANT_ID', 'unknown')
    workspace = os.environ.get('WORKSPACE', '/workspace')

    s3 = boto3.client('s3', region_name=region)
    prefix = f"results/{tenant_id}/{job_id}/"

    uploaded = 0
    for ext in ['*.xml', '*.parquet', '*.txt', '*.log']:
        for f in Path(workspace).rglob(ext):
            if f.is_file():
                key = f"{prefix}{f.name}"
                s3.upload_file(str(f), bucket, key)
                print(f"Uploaded {f.name} to s3://{bucket}/{key}")
                uploaded += 1

    print(f"Uploaded {uploaded} files to S3")
    return uploaded


if __name__ == '__main__':
    try:
        upload_results()
    except Exception as e:
        print(f"Upload failed: {e}", file=sys.stderr)
        sys.exit(1)
