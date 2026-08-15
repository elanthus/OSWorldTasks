"""Append a redacted lifecycle API exchange to the D4.11 transcript."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.cookiejar
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, str]]:
    encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        response = opener.open(request, timeout=15)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(), dict(error.headers.items())
    return response.status, response.read().decode(), dict(response.headers.items())


def _safe_body(text: str, content_type: str) -> object:
    if "application/json" in content_type:
        return json.loads(text)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", text, flags=re.DOTALL)
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", text, flags=re.DOTALL)
    clean = lambda value: html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
    return {
        "title": clean(title.group(1)) if title else "non-JSON response",
        "detail": clean(paragraphs[0]) if paragraphs else "response body redacted",
    }


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("event", choices=("blocked-approval", "policy", "ground"))
    parser.add_argument("--base-url", default="http://localhost:5800")
    parser.add_argument("--candidate-id")
    parser.add_argument("--reason", default="D4.11 blocked-path rehearsal")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--target")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/platform/demo-api-transcript.jsonl")
    )
    parser.add_argument("--expect-status", type=int, required=True)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    request_evidence: dict[str, Any]
    if args.event == "blocked-approval":
        if not args.candidate_id:
            raise SystemExit("--candidate-id is required for blocked-approval")
        candidate_path = f"/candidates/{args.candidate_id}"
        _, page, _ = _request(opener, base_url + candidate_path)
        match = re.search(r'csrf-token" content="([0-9a-f]+)', page)
        if match is None:
            raise SystemExit("candidate page did not provide a CSRF token")
        path = f"/api/candidates/{args.candidate_id}/approve"
        request_body = {"reason": args.reason}
        status, response_text, response_headers = _request(
            opener,
            base_url + path,
            method="POST",
            body=request_body,
            headers={"X-CSRF-Token": match.group(1)},
        )
        request_evidence = {"method": "POST", "path": path, "body": request_body}
    elif args.event == "policy":
        path = "/api/v1/policy"
        status, response_text, response_headers = _request(opener, base_url + path)
        request_evidence = {"method": "GET", "path": path}
    else:
        if args.image is None or not args.target:
            raise SystemExit("--image and --target are required for ground")
        image_bytes = args.image.read_bytes()
        path = "/api/v1/ground"
        request_body = {
            "image_base64": base64.b64encode(image_bytes).decode(),
            "media_type": "image/png",
            "target": args.target,
        }
        status, response_text, response_headers = _request(
            opener, base_url + path, method="POST", body=request_body
        )
        request_evidence = {
            "method": "POST",
            "path": path,
            "body": {
                "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "media_type": "image/png",
                "target": args.target,
                "image_base64": "<redacted; digest recorded>",
            },
        }

    content_type = next(
        (value for key, value in response_headers.items() if key.lower() == "content-type"), ""
    )
    record = {
        "schema_version": "pixelgym-platform-api-transcript-v1",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "event": args.event,
        "request": request_evidence,
        "response": {
            "status": status,
            "body": _safe_body(response_text, content_type),
            "policy_id": next(
                (
                    value
                    for key, value in response_headers.items()
                    if key.lower() == "x-pixelgym-policy-id"
                ),
                None,
            ),
            "deployment_id": next(
                (
                    value
                    for key, value in response_headers.items()
                    if key.lower() == "x-pixelgym-deployment-id"
                ),
                None,
            ),
        },
    }
    _append(args.output, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    if status != args.expect_status:
        raise SystemExit(f"expected HTTP {args.expect_status}, received {status}")


if __name__ == "__main__":
    main()
