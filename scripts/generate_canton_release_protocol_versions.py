#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import cast

from canton_release_reference import (
    DEFAULT_RELEASE_REPO,
    ReleaseAsset,
    ensure_release_archive,
    extract_release,
    resolve_release_asset,
    run_reference_script,
)
from docs_env import ensure_repo_direnv


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = REPO_ROOT / ".internal" / "cache" / "canton-release-reference"
DEFAULT_OUTPUT = (
    REPO_ROOT / "docs-source" / "release-notes" / "releases-and-versioning.mdx"
)
REFERENCE_SCRIPT = REPO_ROOT / "scripts" / "canton_release_protocol_versions.canton"
GENERATED_START = "{/* GENERATED_CANTON_RELEASE_PROTOCOL_VERSIONS_START */}"
GENERATED_END = "{/* GENERATED_CANTON_RELEASE_PROTOCOL_VERSIONS_END */}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Canton release-to-protocol compatibility from a public release binary."
    )
    parser.add_argument("--release-repo", default=DEFAULT_RELEASE_REPO)
    parser.add_argument("--canton-tag")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reference-json", type=Path)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--docs-json")
    return parser.parse_args()


def load_rows(payload: object) -> list[tuple[str, list[str]]]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("releaseVersionsToProtocolVersions"), list
    ):
        raise ValueError(
            "Canton reference JSON must contain a releaseVersionsToProtocolVersions list"
        )
    rows: list[tuple[str, list[str]]] = []
    for index, row in enumerate(payload["releaseVersionsToProtocolVersions"]):
        if (
            not isinstance(row, list)
            or len(row) != 2
            or not isinstance(row[0], str)
            or not isinstance(row[1], list)
            or not row[1]
            or not all(isinstance(value, str) and value for value in row[1])
        ):
            raise ValueError(f"Release/protocol row {index} is invalid")
        rows.append((row[0], cast(list[str], row[1])))
    return rows


def render_section(rows: list[tuple[str, list[str]]], *, asset: ReleaseAsset) -> str:
    lines = [
        GENERATED_START,
        "",
        (
            "{/* GENERATED_FROM "
            f'source="{DEFAULT_RELEASE_REPO}" ref="{asset.tag}" asset="{asset.name}" '
            f'digest="{asset.digest}" release_line_count="{len(rows)}" */}}'
        ),
        "",
        "## Canton Release and Protocol Compatibility",
        "",
        (
            f"The public Canton {asset.version} release reports the following protocol-version support by release line."
        ),
        "",
        "| Canton release | Supported protocol versions |",
        "| --- | --- |",
    ]
    lines.extend(f"| {release} | {', '.join(versions)} |" for release, versions in rows)
    lines.extend(
        [
            "",
            r"\* A trailing asterisk identifies a beta protocol version.",
            "",
            GENERATED_END,
        ]
    )
    return "\n".join(lines)


def replace_section(page: str, section: str) -> str:
    if GENERATED_START in page:
        start = page.index(GENERATED_START)
        end = page.index(GENERATED_END, start) + len(GENERATED_END)
        return page[:start].rstrip() + "\n\n" + section + "\n" + page[end:].lstrip()
    return page.rstrip() + "\n\n" + section + "\n"


def main() -> int:
    ensure_repo_direnv(
        repo_root=REPO_ROOT, script_path=Path(__file__).resolve(), argv=sys.argv[1:]
    )
    args = parse_args()
    asset = resolve_release_asset(release_repo=args.release_repo, tag=args.canton_tag)
    if args.reference_json:
        payload = json.loads(args.reference_json.read_text(encoding="utf-8"))
    else:
        archive_path = ensure_release_archive(
            asset=asset, cache_dir=args.cache_dir, force_refresh=args.force_refresh
        )
        distribution_root = extract_release(
            archive_path=archive_path,
            asset=asset,
            cache_dir=args.cache_dir,
            force_refresh=args.force_refresh,
        )
        payload = run_reference_script(
            distribution_root=distribution_root,
            script_path=REFERENCE_SCRIPT,
            cache_dir=args.cache_dir,
            cache_namespace="release-protocol-versions",
            asset=asset,
            force_refresh=args.force_refresh,
        )

    rows = load_rows(payload)
    page = args.output.read_text(encoding="utf-8")
    args.output.write_text(
        replace_section(page, render_section(rows, asset=asset)), encoding="utf-8"
    )
    print(
        f"Generated {len(rows)} Canton release/protocol rows from {asset.tag} in {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
