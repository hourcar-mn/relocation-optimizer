"""Checks the R2 sync stays inert without R2 config — that's what keeps local
dev working unchanged. Run: python test_r2_sync.py
"""

import os
from core_functions import r2_sync


def test_no_op_without_config():
    for var in ("R2_ENDPOINT", "R2_BUCKET"):
        os.environ.pop(var, None)
    assert r2_sync._client() == (None, None)
    # neither should touch the filesystem or raise
    r2_sync.pull_from_r2()
    r2_sync.push_to_r2()


def test_endpoint_without_bucket_is_not_configured():
    os.environ["R2_ENDPOINT"] = "https://example.r2.cloudflarestorage.com"
    os.environ.pop("R2_BUCKET", None)
    assert r2_sync._client() == (None, None)
    os.environ.pop("R2_ENDPOINT")


if __name__ == "__main__":
    test_no_op_without_config()
    test_endpoint_without_bucket_is_not_configured()
    print("ok")
