#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = (
    REPO_ROOT / "docs-main" / "snippets" / "generated" / "canton-topology-proto-link.mdx"
)
CANTON_RELEASE_REPO = "digital-asset/canton"
CANTON_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{CANTON_RELEASE_REPO}/releases/latest"
)
TOPOLOGY_PROTO_PATH = (
    "community/base/src/main/protobuf/com/digitalasset/canton/protocol/v30/topology.proto"
)
STABLE_TAG_RE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$")
USER_AGENT = "cf-docs-canton-topology-proto-link"
DEFAULT_TIMEOUT_SECONDS = 30.0


def request_headers(url: str) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json" if "api.github.com" in url else "*/*",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and urlparse(url).netloc == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def fetch_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(url, headers=request_headers(url))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def parse_canton_version(tag_or_version: str) -> str:
    match = STABLE_TAG_RE.fullmatch(tag_or_version.strip())
    if not match:
        raise ValueError(
            f"Expected stable Canton version or tag like 3.5.14 or v3.5.14, "
            f"got {tag_or_version!r}"
        )
    return match.group("version")


def fetch_latest_stable_canton_version(timeout: float) -> str:
    data = fetch_json(CANTON_LATEST_RELEASE_URL, timeout)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected release object from {CANTON_LATEST_RELEASE_URL}")
    if data.get("prerelease") or data.get("draft"):
        raise RuntimeError(
            f"Latest GitHub release at {CANTON_LATEST_RELEASE_URL} is not a stable release"
        )
    tag_name = data.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name:
        raise RuntimeError(f"Missing tag_name from {CANTON_LATEST_RELEASE_URL}")
    return parse_canton_version(tag_name)


def release_line_branch(canton_version: str) -> str:
    major, minor, _patch = canton_version.split(".")
    return f"release-line-{major}.{minor}"


def topology_proto_blob_url(release_line: str) -> str:
    return (
        f"https://github.com/{CANTON_RELEASE_REPO}/blob/{release_line}/{TOPOLOGY_PROTO_PATH}"
    )


def topology_proto_raw_url(release_line: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{CANTON_RELEASE_REPO}/"
        f"{release_line}/{TOPOLOGY_PROTO_PATH}"
    )


def assert_url_exists(url: str, timeout: float) -> None:
    request = urllib.request.Request(url, method="HEAD", headers=request_headers(url))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status >= 400:
                raise RuntimeError(f"Unexpected HTTP status {status} for {url}")
            return
    except urllib.error.HTTPError as error:
        if error.code in {403, 405}:
            fallback = urllib.request.Request(
                url,
                headers={**request_headers(url), "Range": "bytes=0-0"},
            )
            try:
                with urllib.request.urlopen(fallback, timeout=timeout):
                    return
            except urllib.error.HTTPError as fallback_error:
                if fallback_error.code == 404:
                    raise RuntimeError(
                        f"topology.proto URL returned 404: {url}"
                    ) from fallback_error
                raise RuntimeError(
                    f"Failed to verify topology.proto URL {url}: "
                    f"HTTP {fallback_error.code}"
                ) from fallback_error
            except urllib.error.URLError as fallback_error:
                raise RuntimeError(
                    f"Failed to verify topology.proto URL {url}: {fallback_error}"
                ) from fallback_error
        if error.code == 404:
            raise RuntimeError(f"topology.proto URL returned 404: {url}") from error
        raise RuntimeError(
            f"Failed to verify topology.proto URL {url}: HTTP {error.code}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Failed to verify topology.proto URL {url}: {error}") from error


def render_mdx(topology_proto_url: str) -> str:
    escaped = topology_proto_url.replace("\\", "\\\\").replace("'", "\\'")
    return f"export const topologyProtoUrl = '{escaped}';\n"


def write_output(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def main() -> int:
    try:
        canton_version = fetch_latest_stable_canton_version(DEFAULT_TIMEOUT_SECONDS)
        release_line = release_line_branch(canton_version)
        blob_url = topology_proto_blob_url(release_line)
        # Prefer the raw URL for a reliable 404; blob pages can soft-404.
        assert_url_exists(topology_proto_raw_url(release_line), DEFAULT_TIMEOUT_SECONDS)
        write_output(OUTPUT_PATH, render_mdx(blob_url))
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} "
        f"(Canton {canton_version} → {release_line})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
