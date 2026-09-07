#!/usr/bin/env python3
"""Inventory public-release links, redaction risks, and restricted evidence surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCHEMA_VERSION = "pixelgym-public-release-inventory-v1"
TEXT_SIZE_LIMIT = 8 * 1024 * 1024
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
PRIVATE_PATH = re.compile(
    r"(?P<path>(?:file://)?/(?:Users|home)/(?P<user>[^/\s\"']+)(?:/[^\s\"'<>)]*)?"
    r"|[A-Za-z]:\\Users\\(?P<windows_user>[^\\\s\"']+)(?:\\[^\s\"'<>)]*)?)"
)
EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])")
TOKEN_SHAPES = (
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("openai_style_token", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{16,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{16,}\b", re.IGNORECASE)),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
GATED_ASSET_SUFFIXES = {".ova", ".ovf", ".qcow2", ".vdi", ".vmdk", ".vhd", ".vhdx", ".iso"}
RAW_PAYLOAD_KEY = re.compile(r'"(?:raw_response|provider_response|response_body|response_content)"\s*:')
SAFE_PATH_USERS = {"<operator>", "operator", "private", "example", "example-user", "user"}
SAFE_EMAIL_SUFFIXES = (".example", ".invalid", ".test")
SAFE_EMAIL_DOMAINS = {"example.com", "example.net", "example.org"}
SYNTHETIC_EMAIL_PATHS = ("pixelgym/tasks/", "legacy/grounding/")
SAFE_TOKEN_TEST_FINGERPRINTS = {
    "sha256:2e6ad69016f66d4b5a95aa38017878b0b4a537bc138b2a374e4e69ae1af59c33",
    "sha256:32f4cf588c77f0941514cadc1cb18fa0e186716c93e067c22d9ef4e27718f506",
}


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def repository_files(root: Path) -> tuple[list[Path], set[str]]:
    """Return public working-tree files and paths already tracked by Git."""

    tracked_result = _run_git(root, "ls-files", "-z")
    visible_result = _run_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if tracked_result.returncode == 0 and visible_result.returncode == 0:
        tracked = {path for path in tracked_result.stdout.split("\0") if path}
        visible = sorted(path for path in visible_result.stdout.split("\0") if path)
        return [root / path for path in visible], tracked

    excluded = {".git", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
    files = sorted(
        path for path in root.rglob("*") if path.is_file() and not excluded.intersection(path.parts)
    )
    return files, {path.relative_to(root).as_posix() for path in files}


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > TEXT_SIZE_LIMIT:
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if b"\0" in payload:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _line_number(text: str, start: int) -> int:
    return text.count("\n", 0, start) + 1


def _slug(value: str) -> str:
    value = re.sub(r"[`*_~]", "", value).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return re.sub(r"[\s-]+", "-", value).strip("-")


def _tracked_target(path: Path, root: Path, tracked: set[str]) -> bool:
    relative = path.relative_to(root).as_posix()
    if path.is_dir():
        prefix = relative.rstrip("/") + "/"
        return any(item.startswith(prefix) for item in tracked)
    return relative in tracked


def scan_readme_links(root: Path, tracked: set[str]) -> dict[str, object]:
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8")
    anchors = {_slug(match.group(1)) for match in HEADING.finditer(text)}
    links: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for match in MARKDOWN_LINK.finditer(text):
        raw_target = match.group(1).strip()
        if raw_target.startswith("<") and raw_target.endswith(">"):
            raw_target = raw_target[1:-1]
        target = raw_target.split(maxsplit=1)[0]
        parsed = urlsplit(target)
        record: dict[str, object] = {
            "line": _line_number(text, match.start()),
            "target": target,
        }
        if parsed.scheme or target.startswith("//"):
            record["classification"] = "external_not_fetched"
        elif not parsed.path:
            anchor = unquote(parsed.fragment)
            record["classification"] = "local_anchor"
            record["resolved"] = _slug(anchor) in anchors
        else:
            decoded = unquote(parsed.path)
            candidate = (readme.parent / decoded).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                record["classification"] = "outside_repository"
                record["resolved"] = False
            else:
                record["classification"] = "repository_path"
                record["exists"] = candidate.exists()
                record["tracked"] = candidate.exists() and _tracked_target(candidate, root, tracked)
                record["resolved"] = bool(record["exists"] and record["tracked"])
                if parsed.fragment and candidate.is_file() and candidate.suffix.lower() == ".md":
                    target_text = candidate.read_text(encoding="utf-8")
                    target_anchors = {_slug(item.group(1)) for item in HEADING.finditer(target_text)}
                    record["resolved"] = bool(
                        record["resolved"] and _slug(unquote(parsed.fragment)) in target_anchors
                    )
        links.append(record)
        if record.get("resolved") is False:
            failures.append(record)

    return {
        "checked_file": "README.md",
        "link_count": len(links),
        "external_link_count": sum(
            item["classification"] == "external_not_fetched" for item in links
        ),
        "local_link_count": sum(item["classification"] != "external_not_fetched" for item in links),
        "failure_count": len(failures),
        "failures": failures,
        "passed": not failures,
    }


def _finding(path: str, line: int | None, value: str, classification: str, **extra: object) -> dict[str, object]:
    result: dict[str, object] = {
        "path": path,
        "classification": classification,
        "value_fingerprint": _fingerprint(value),
    }
    if line is not None:
        result["line"] = line
    result.update(extra)
    return result


def scan_release_surface(root: Path, files: Iterable[Path]) -> dict[str, object]:
    private_paths: list[dict[str, object]] = []
    usernames: list[dict[str, object]] = []
    emails: list[dict[str, object]] = []
    tokens: list[dict[str, object]] = []
    raw_payloads: list[dict[str, object]] = []
    gated_assets: list[dict[str, object]] = []
    scanned_text = 0
    scanned_binary = 0

    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() in GATED_ASSET_SUFFIXES:
            gated_assets.append(
                {
                    "path": relative,
                    "classification": "review_required_gated_or_vm_asset",
                }
            )
        text = _read_text(path)
        if text is None:
            scanned_binary += 1
            continue
        scanned_text += 1

        for match in PRIVATE_PATH.finditer(text):
            user = match.group("user") or match.group("windows_user") or ""
            source_line = text.splitlines()[_line_number(text, match.start()) - 1]
            classification = (
                "acknowledged_placeholder"
                if (
                    user.lower() in SAFE_PATH_USERS
                    or (relative.startswith("scripts/") and "re.compile" in source_line)
                )
                else "review_required_operator_path"
            )
            private_paths.append(
                _finding(relative, _line_number(text, match.start()), match.group("path"), classification)
            )
            usernames.append(
                _finding(relative, _line_number(text, match.start()), user, classification)
            )

        for match in EMAIL.finditer(text):
            address = match.group(0)
            domain = match.group(2).lower()
            classification = (
                "acknowledged_synthetic_address"
                if (
                    domain.endswith(SAFE_EMAIL_SUFFIXES)
                    or domain in SAFE_EMAIL_DOMAINS
                    or relative.startswith(SYNTHETIC_EMAIL_PATHS)
                )
                else "review_required_email_address"
            )
            emails.append(
                _finding(relative, _line_number(text, match.start()), address, classification)
            )

        for shape_name, pattern in TOKEN_SHAPES:
            for match in pattern.finditer(text):
                classification = (
                    "acknowledged_test_vector"
                    if relative.startswith("tests/")
                    and _fingerprint(match.group(0)) in SAFE_TOKEN_TEST_FINGERPRINTS
                    else "review_required_credential_shape"
                )
                tokens.append(
                    _finding(
                        relative,
                        _line_number(text, match.start()),
                        match.group(0),
                        classification,
                        shape=shape_name,
                    )
                )

        if relative.startswith("artifacts/") and RAW_PAYLOAD_KEY.search(text):
            raw_payloads.append(
                {
                    "path": relative,
                    "classification": (
                        "published_structured_model_output"
                        if "prediction" in path.name
                        or relative == "artifacts/day-3/pilot/audit.json"
                        else "review_required_raw_provider_payload"
                    ),
                }
            )

    categories: dict[str, list[dict[str, object]]] = {
        "private_paths": private_paths,
        "usernames": usernames,
        "email_addresses": emails,
        "credential_or_token_shapes": tokens,
        "gated_osworld_or_vm_assets": gated_assets,
        "raw_provider_payloads": raw_payloads,
    }
    review_required = sum(
        item["classification"].startswith("review_required")
        for findings in categories.values()
        for item in findings
    )
    return {
        "files_scanned": scanned_text + scanned_binary,
        "text_files_scanned": scanned_text,
        "binary_or_large_files_skipped_for_content": scanned_binary,
        "categories": {
            name: {
                "finding_count": len(findings),
                "review_required_count": sum(
                    item["classification"].startswith("review_required") for item in findings
                ),
                "findings": findings,
            }
            for name, findings in categories.items()
        },
        "review_required_count": review_required,
        "passed": review_required == 0,
    }


def scan_licenses(root: Path, files: Iterable[Path], tracked: set[str]) -> dict[str, object]:
    visible = {path.relative_to(root).as_posix(): path for path in files}
    license_path = root / "LICENSE"
    notice_path = root / "NOTICE"
    license_text = _read_text(license_path) or ""
    notice_text = _read_text(notice_path) or ""
    fonts = sorted(
        relative
        for relative in visible
        if Path(relative).suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}
    )
    dejavu_fonts = [path for path in fonts if "dejavu" in path.lower()]
    apache_2_0 = "Apache License" in license_text and "Version 2.0" in license_text
    font_license_paths = sorted(
        {str(Path(path).parent / "LICENSE-DejaVu.txt") for path in dejavu_fonts}
    )
    missing_font_licenses = [
        path for path in font_license_paths if path not in tracked or not (root / path).is_file()
    ]
    failures: list[str] = []
    if "LICENSE" not in tracked or not license_path.is_file():
        failures.append("root_license_missing_or_untracked")
    if not apache_2_0:
        failures.append("root_license_is_not_apache_2_0_text")
    if "NOTICE" not in tracked or not notice_path.is_file():
        failures.append("root_notice_missing_or_untracked")
    if missing_font_licenses:
        failures.append("bundled_dejavu_font_license_missing_or_untracked")
    if dejavu_fonts and not all(path in notice_text for path in font_license_paths):
        failures.append("notice_does_not_index_each_dejavu_license")

    return {
        "license_path": "LICENSE",
        "notice_path": "NOTICE",
        "license_expression": "Apache-2.0" if apache_2_0 else None,
        "bundled_font_count": len(fonts),
        "bundled_dejavu_font_count": len(dejavu_fonts),
        "dejavu_license_paths": font_license_paths,
        "missing_dejavu_license_paths": missing_font_licenses,
        "failure_count": len(failures),
        "failures": failures,
        "passed": not failures,
    }


def build_inventory(root: Path) -> dict[str, object]:
    files, tracked = repository_files(root)
    links = scan_readme_links(root, tracked)
    surface = scan_release_surface(root, files)
    licenses = scan_licenses(root, files, tracked)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "repository_files": "tracked files plus non-ignored working-tree additions",
            "external_links": "inventoried but not fetched",
            "sensitive_values": "represented only by SHA-256 fingerprints",
        },
        "links": links,
        "license_inventory": licenses,
        "redaction_and_asset_inventory": surface,
        "summary": {
            "link_failures": links["failure_count"],
            "license_failures": licenses["failure_count"],
            "review_required_findings": surface["review_required_count"],
            "passed": bool(links["passed"] and licenses["passed"] and surface["passed"]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--mode", choices=("inventory", "links", "redaction"), default="inventory")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    inventory = build_inventory(root)
    if args.mode == "links":
        output: object = inventory["links"]
        passed = bool(inventory["links"]["passed"])
    elif args.mode == "redaction":
        output = {
            "license_inventory": inventory["license_inventory"],
            "redaction_and_asset_inventory": inventory["redaction_and_asset_inventory"],
            "passed": bool(
                inventory["license_inventory"]["passed"]
                and inventory["redaction_and_asset_inventory"]["passed"]
            ),
        }
        passed = bool(output["passed"])
    else:
        output = inventory
        passed = bool(inventory["summary"]["passed"])
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
