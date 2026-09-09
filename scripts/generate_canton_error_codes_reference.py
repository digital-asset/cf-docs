#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
import textwrap
from typing import TypedDict, cast

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
    REPO_ROOT / "docs-source" / "global-synchronizer" / "reference" / "error-codes.mdx"
)
REFERENCE_SCRIPT = REPO_ROOT / "scripts" / "canton_error_codes_reference.canton"
GENERATED_START = "{/* GENERATED_CANTON_ERROR_CODES_START */}"
GENERATED_END = "{/* GENERATED_CANTON_ERROR_CODES_END */}"
LEGACY_HEADING = "## Error Codes Inventory"
COPIED_END = "{/* COPIED_END */}"


class ErrorCodeItem(TypedDict):
    className: str
    category: str
    hierarchicalGrouping: list[str]
    conveyance: str | None
    code: str
    explanation: str | None
    resolution: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Canton error-code inventory from a public Canton release binary."
    )
    parser.add_argument("--release-repo", default=DEFAULT_RELEASE_REPO)
    parser.add_argument("--canton-tag")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reference-json", type=Path)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--docs-json")
    return parser.parse_args()


def load_error_codes(payload: object) -> list[ErrorCodeItem]:
    if not isinstance(payload, dict) or not isinstance(payload.get("errorCodes"), list):
        raise ValueError("Canton reference JSON must contain an errorCodes list")

    items: list[ErrorCodeItem] = []
    for index, item in enumerate(payload["errorCodes"]):
        if not isinstance(item, dict):
            raise ValueError(f"Error-code item {index} must be an object")
        if not all(
            isinstance(item.get(key), str) for key in ("className", "category", "code")
        ):
            raise ValueError(f"Error-code item {index} has invalid required fields")
        grouping = item.get("hierarchicalGrouping")
        if (
            not isinstance(grouping, list)
            or not grouping
            or not all(isinstance(value, str) and value for value in grouping)
        ):
            raise ValueError(f"Error-code item {index} has invalid grouping")
        if not all(
            item.get(key) is None or isinstance(item.get(key), str)
            for key in ("conveyance", "explanation", "resolution")
        ):
            raise ValueError(
                f"Error-code item {index} has invalid documentation fields"
            )
        items.append(cast(ErrorCodeItem, item))
    return items


def mdx_text(value: str) -> str:
    normalized = " ".join(textwrap.dedent(value).split())
    return (
        normalized.replace("<", r"\<")
        .replace(">", r"\>")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def anchor_base(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")


def render_inventory(items: list[ErrorCodeItem], *, asset: ReleaseAsset) -> str:
    grouped: dict[tuple[str, ...], list[ErrorCodeItem]] = defaultdict(list)
    for item in items:
        grouped[tuple(item["hierarchicalGrouping"])].append(item)

    anchor_counts: dict[str, int] = defaultdict(int)
    lines = [
        GENERATED_START,
        "",
        (
            "{/* GENERATED_FROM "
            f'source="{DEFAULT_RELEASE_REPO}" ref="{asset.tag}" asset="{asset.name}" '
            f'digest="{asset.digest}" error_code_count="{len(items)}" */}}'
        ),
        "",
        LEGACY_HEADING,
        "",
    ]
    for group_path in sorted(
        grouped, key=lambda path: tuple(part.casefold() for part in path)
    ):
        lines.extend([f"### {' › '.join(group_path)}", ""])
        for item in sorted(grouped[group_path], key=lambda value: value["code"]):
            base = f"error-code-{anchor_base(item['code'])}"
            occurrence = anchor_counts[base]
            anchor_counts[base] += 1
            anchor = base if occurrence == 0 else f"{base}-{occurrence + 1}"
            lines.extend([f'<div id="{anchor}" />', "", f"#### `{item['code']}`", ""])
            if item["explanation"]:
                lines.append(f"- **Explanation:** {mdx_text(item['explanation'])}")
            if item["resolution"]:
                lines.append(f"- **Resolution:** {mdx_text(item['resolution'])}")
            lines.append(f"- **Category:** `{item['category']}`")
            if item["conveyance"]:
                lines.append(f"- **Conveyance:** {mdx_text(item['conveyance'])}")
            lines.append("")
    lines.append(GENERATED_END)
    return "\n".join(lines)


def replace_inventory(page: str, inventory: str) -> str:
    if GENERATED_START in page:
        start = page.index(GENERATED_START)
        end = page.index(GENERATED_END, start) + len(GENERATED_END)
        return page[:start].rstrip() + "\n\n" + inventory + "\n\n" + page[end:].lstrip()

    start = page.index(LEGACY_HEADING)
    end = page.index(COPIED_END, start) + len(COPIED_END)
    prefix = page[:start].rstrip()
    suffix = page[end:].lstrip()
    return prefix + "\n\n" + COPIED_END + "\n\n" + inventory + "\n\n" + suffix


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
            cache_namespace="error-codes",
            asset=asset,
            force_refresh=args.force_refresh,
        )

    items = load_error_codes(payload)
    page = args.output.read_text(encoding="utf-8")
    args.output.write_text(
        replace_inventory(page, render_inventory(items, asset=asset)), encoding="utf-8"
    )
    print(
        f"Generated {len(items)} Canton error codes from {asset.tag} in {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
