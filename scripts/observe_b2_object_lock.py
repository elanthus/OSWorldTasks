"""Read-only observation of a Backblaze B2 bucket's Object Lock state for stored evidence.

Lists every file under the given prefixes with its size, SHA-1, retention mode and
retain-until time, plus the bucket's lock and lifecycle configuration. Credentials come
from ``BACKBLAZE_KEY_ID``, ``BACKBLAZE_APPLICATION_KEY`` and ``BACKBLAZE_BUCKET_NAME`` in the
environment and are never printed. Nothing is uploaded, modified or deleted.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from typing import Any

SCHEMA_VERSION = "pixelgym-d510-offhost-object-lock-observation-v1"
AUTHORIZE_URL = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"


def _call(url: str, body: dict[str, Any] | None, authorization: str) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Authorization": authorization}
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(
        urllib.request.Request(url, data=data, headers=headers), timeout=60
    ) as r:
        return json.load(r)


def _file_entry(file: dict[str, Any]) -> dict[str, Any]:
    retention = (file.get("fileRetention") or {}).get("value") or {}
    return {
        "name": file["fileName"],
        "size": file["contentLength"],
        "sha1": file.get("contentSha1"),
        "large_file_sha1": (file.get("fileInfo") or {}).get("large_file_sha1"),
        "retention_mode": retention.get("mode"),
        "retain_until_ms": retention.get("retainUntilTimestamp"),
    }


def observe(prefixes: list[str], environ: dict[str, str] = os.environ) -> dict[str, Any]:
    """Return the bucket lock configuration and per-file retention under each prefix."""
    key_id, key, bucket = (
        environ[name]
        for name in ("BACKBLAZE_KEY_ID", "BACKBLAZE_APPLICATION_KEY", "BACKBLAZE_BUCKET_NAME")
    )
    basic = "Basic " + base64.b64encode(f"{key_id}:{key}".encode()).decode()
    auth = _call(AUTHORIZE_URL, None, basic)
    api, token = auth["apiInfo"]["storageApi"]["apiUrl"], auth["authorizationToken"]
    buckets = _call(
        f"{api}/b2api/v3/b2_list_buckets",
        {"accountId": auth["accountId"], "bucketName": bucket},
        token,
    )["buckets"]
    info = next(b for b in buckets if b["bucketName"] == bucket)
    lock = (info.get("fileLockConfiguration") or {}).get("value") or {}
    files: list[dict[str, Any]] = []
    for prefix in prefixes:
        start: str | None = None
        while True:
            body: dict[str, Any] = {
                "bucketId": info["bucketId"],
                "prefix": prefix.rstrip("/") + "/",
                "maxFileCount": 1000,
            }
            if start:
                body["startFileName"] = start
            page = _call(f"{api}/b2api/v3/b2_list_file_names", body, token)
            files.extend(_file_entry(f) for f in page["files"])
            start = page.get("nextFileName")
            if not start:
                break
    return summarize(bucket, info["bucketType"], lock, info.get("lifecycleRules"), files)


def summarize(
    bucket: str,
    bucket_type: str,
    lock: dict[str, Any],
    lifecycle_rules: Any,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure summary over listed files; exercised by the unit test without network."""
    return {
        "schema_version": SCHEMA_VERSION,
        "bucket": bucket,
        "bucket_type": bucket_type,
        "object_lock_enabled": lock.get("isFileLockEnabled"),
        "default_retention": lock.get("defaultRetention"),
        "lifecycle_rules": lifecycle_rules,
        "files": files,
        "file_count": len(files),
        "bytes": sum(f["size"] for f in files),
        "all_governance": bool(files) and all(f["retention_mode"] == "governance" for f in files),
        "min_retain_until_ms": min((f["retain_until_ms"] or 0) for f in files) if files else None,
        "model_calls": 0,
        "mutations": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+", help="bucket path prefixes to list")
    args = parser.parse_args(argv)
    print(json.dumps(observe(args.prefixes), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
