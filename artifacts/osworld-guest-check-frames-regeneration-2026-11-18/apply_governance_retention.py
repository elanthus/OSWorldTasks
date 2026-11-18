"""Set one governance-mode retain-until time on every file under the given bucket prefixes.

Credentials come from BACKBLAZE_KEY_ID, BACKBLAZE_APPLICATION_KEY and BACKBLAZE_BUCKET_NAME in
the environment and are never printed. Nothing is uploaded or deleted. The listing is checked
against --expect-files before any file is changed, and a file that already carries a different
retention stops the run.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

AUTHORIZE_URL = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"


def call(url: str, body: dict[str, Any] | None, authorization: str) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Authorization": authorization}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read() or b"{}")
        raise SystemExit(
            f"B2 call {url.rsplit('/', 1)[-1]} failed: status {error.code},"
            f" code {detail.get('code')}, message {detail.get('message')}"
        ) from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+")
    parser.add_argument("--retain-until-ms", type=int, required=True)
    parser.add_argument("--expect-files", type=int, required=True)
    args = parser.parse_args()

    key_id, key, bucket = (
        os.environ[name]
        for name in ("BACKBLAZE_KEY_ID", "BACKBLAZE_APPLICATION_KEY", "BACKBLAZE_BUCKET_NAME")
    )
    basic = "Basic " + base64.b64encode(f"{key_id}:{key}".encode()).decode()
    auth = call(AUTHORIZE_URL, None, basic)
    api, token = auth["apiInfo"]["storageApi"]["apiUrl"], auth["authorizationToken"]
    buckets = call(
        f"{api}/b2api/v3/b2_list_buckets",
        {"accountId": auth["accountId"], "bucketName": bucket},
        token,
    )["buckets"]
    info = next(b for b in buckets if b["bucketName"] == bucket)

    files: list[dict[str, Any]] = []
    for prefix in args.prefixes:
        start: str | None = None
        while True:
            body: dict[str, Any] = {
                "bucketId": info["bucketId"],
                "prefix": prefix.rstrip("/") + "/",
                "maxFileCount": 1000,
            }
            if start:
                body["startFileName"] = start
            page = call(f"{api}/b2api/v3/b2_list_file_names", body, token)
            files.extend(page["files"])
            start = page.get("nextFileName")
            if not start:
                break

    wanted = {"mode": "governance", "retainUntilTimestamp": args.retain_until_ms}
    current = [(f.get("fileRetention") or {}).get("value") or {} for f in files]
    conflicting = [
        f["fileName"]
        for f, held in zip(files, current, strict=True)
        if held.get("mode") is not None and held != wanted
    ]
    print(f"bucket: {bucket}")
    print(f"prefixes: {args.prefixes}")
    print(f"files listed: {len(files)} (expected {args.expect_files})")
    print(f"files already carrying a different retention: {len(conflicting)}")
    if len(files) != args.expect_files or conflicting:
        print("stopping before any change")
        return 1

    applied = already = 0
    for file, held in zip(files, current, strict=True):
        if held == wanted:
            already += 1
            print(f"already set  {file['fileName']}")
            continue
        result = call(
            f"{api}/b2api/v3/b2_update_file_retention",
            {"fileName": file["fileName"], "fileId": file["fileId"], "fileRetention": wanted},
            token,
        )
        returned = result["fileRetention"]
        if returned != wanted:
            print(f"unexpected reply for {file['fileName']}: {returned}")
            return 1
        applied += 1
        print(f"governance {returned['retainUntilTimestamp']}  {file['fileName']}")

    print(
        json.dumps(
            {
                "files_listed": len(files),
                "retention_applied": applied,
                "already_set": already,
                "mode": "governance",
                "retain_until_ms": args.retain_until_ms,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
