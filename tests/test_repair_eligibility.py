"""Tests for _eligible_for_s3_repair — the guard on the reconciler's
"Repair RUNNING/FAILED/PENDING -> SUCCEEDED from S3 results" path.

Root-cause requirement (the dropped-seed bug):
A DB-active job may be finalized to SUCCEEDED purely from S3 results ONLY when its
cluster work is gone/terminal — i.e. the pod has exited, which under the run
script's `set -e` + sequential `upload_results.py` means the upload is COMPLETE.

A still-LIVE pod must NEVER be finalized this way: mid-upload it has only its first
(smallest) files in S3 (summary + inputs upload before the large routes/fcd), so
`s3_prefix_has_files()` goes True long before the upload finishes. Finalizing then
fires SendTaskSuccess early and the downstream per-seed sync copies a PARTIAL result
set (no routes/fcd) -> ConvertRoutes 404. A live job must instead be finalized by the
active-jobs loop on real pod-exit.

'unknown' (K8s API read failed) is excluded too: we cannot confirm the pod is gone,
so we must not risk finalizing a job that may still be uploading.
"""
import pytest

from src.reconciler import _eligible_for_s3_repair


def test_live_job_never_repaired_even_with_files():
    # the exact bug: a running pod mid-upload must not be finalized from partial S3
    assert _eligible_for_s3_repair("live", True) is False


def test_unknown_workload_not_repaired_with_files():
    # can't confirm the pod is gone -> conservatively do not finalize
    assert _eligible_for_s3_repair("unknown", True) is False


@pytest.mark.parametrize("workload", ["missing", "idle", "terminal"])
def test_gone_or_terminal_with_files_is_repaired(workload):
    # genuine recovery: pod exited (=> upload complete) and results exist
    assert _eligible_for_s3_repair(workload, True) is True


@pytest.mark.parametrize("workload", ["missing", "idle", "terminal", "live", "unknown"])
def test_no_files_never_repaired(workload):
    # nothing in S3 -> never finalize, regardless of workload
    assert _eligible_for_s3_repair(workload, False) is False
