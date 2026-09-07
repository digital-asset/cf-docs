#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from ledger_api_release_bundles import (
    ensure_bundle_archive,
    load_json,
    materialize_bundle_spec,
    read_bundle_spec_text,
    selected_versions,
)
import reference_nav
from x2mdx.history.events import history_events_for_item
from x2mdx.history.io import write_history_report
from x2mdx.history.models import SourceArtifact, SurfaceHistoryReport, VersionSelectionPolicy
from x2mdx.history.validation import validate_history_report
from x2mdx.output import Page, RawMarkdown
from x2mdx.openapi import (
    ManualOpenAPIRenderOptions,
    OpenAPIHistoryScope,
    build_openapi_history_report,
    render_manual_openapi_operation,
)
from x2mdx.reference_pages import (
    ReferenceBadge,
    ReferenceCard,
    ReferenceCollectionPage,
    ReferenceMetaItem,
    ReferenceSection,
    compact_text,
    render_collection_page,
    safe_markdown_text,
)
from x2mdx.render import write_page


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = (
    Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "x2mdx"
)
DEFAULT_SOURCE_CONFIG = (
    REPO_ROOT / "config" / "x2mdx" / "ledger-api" / "source-artifacts.json"
)
DEFAULT_CACHE_DIR = DEFAULT_CACHE_ROOT / "ledger-api-bundles"
DEFAULT_OUTPUT_SPEC = (
    REPO_ROOT / "docs-main" / "openapi" / "json-ledger-api" / "openapi.yaml"
)
DEFAULT_DOCS_JSON = REPO_ROOT / "docs-main" / "docs.json"
DEFAULT_HISTORY_REPORT = (
    REPO_ROOT / "docs-main" / "reference" / "json-api-reference" / "history-report.json"
)
DEFAULT_NAV_DROPDOWN = "API Reference"
DEFAULT_PARENT_GROUP = "Ledger API"
DEFAULT_GROUP_LABEL = "OpenAPI"
DEFAULT_OPENAPI_DIRECTORY = "reference/json-api-reference"
DEFAULT_OVERVIEW_PAGE_REF = "reference/json-api-reference/overview"
DEFAULT_DETAILS_PAGE_REF = "reference/json-api-reference/details"
LEGACY_OUTPUT_FILE = REPO_ROOT / "docs-main" / "reference" / "json-api-reference.mdx"
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
INTERNAL_TODO_LINE_RE = re.compile(r"(?m)^[ \t]*TODO\([^\r\n)]+\)[^\r\n]*(?:\r?\n|$)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish the latest JSON Ledger API OpenAPI spec for Mintlify's native API rendering "
            "and wire the OpenAPI section in docs.json."
        )
    )
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--output-spec", default=str(DEFAULT_OUTPUT_SPEC))
    parser.add_argument("--docs-json", default=str(DEFAULT_DOCS_JSON))
    parser.add_argument("--history-report", default=str(DEFAULT_HISTORY_REPORT))
    parser.add_argument("--nav-dropdown", default=DEFAULT_NAV_DROPDOWN)
    parser.add_argument("--parent-group", default=DEFAULT_PARENT_GROUP)
    parser.add_argument("--group-label", default=DEFAULT_GROUP_LABEL)
    parser.add_argument("--openapi-directory", default=DEFAULT_OPENAPI_DIRECTORY)
    parser.add_argument("--overview-page-ref", default=DEFAULT_OVERVIEW_PAGE_REF)
    parser.add_argument("--details-page-ref", default=DEFAULT_DETAILS_PAGE_REF)
    parser.add_argument(
        "--publish-version", help="Explicit docs major version to publish."
    )
    parser.add_argument(
        "--version",
        action="append",
        help="Restrict candidate versions before selecting the publish version. Repeat to filter the set.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Refresh cached Canton release bundles and rewrite the published OpenAPI spec even if cached.",
    )
    return parser.parse_args()


def docs_relative_file_ref(path: Path, docs_json_path: Path) -> str:
    return path.resolve().relative_to(docs_json_path.resolve().parent).as_posix()


def _find_group(items: list[Any], label: str) -> dict[str, Any] | None:
    for item in items:
        if isinstance(item, dict) and item.get("group") == label:
            return item
    return None


def resolve_publish_version(
    *,
    source_config: dict[str, Any],
    versions: list[dict[str, str]],
    requested_version: str | None,
) -> dict[str, str]:
    publish_version = requested_version
    if publish_version is None:
        configured = source_config.get("publish_version")
        if isinstance(configured, str) and configured.strip():
            publish_version = configured.strip()

    if publish_version is None:
        return versions[-1]

    selected = next(
        (entry for entry in versions if entry["version"] == publish_version), None
    )
    if selected is None:
        available = ", ".join(entry["version"] for entry in versions)
        raise ValueError(
            f"Publish version '{publish_version}' not found in selected versions: {available}"
        )
    return selected


def update_docs_navigation(
    *,
    docs_json_path: Path,
    dropdown_label: str,
    parent_group_label: str,
    group_label: str,
    openapi_source_ref: str,
    openapi_directory: str,
    overview_page_ref: str,
    details_page_ref: str,
    openapi_page_refs: list[str],
) -> None:
    payload = load_json(docs_json_path)
    pages = reference_nav.navigation_pages(
        payload, label=dropdown_label, docs_json_path=docs_json_path
    )

    parent_group = _find_group(pages, parent_group_label)
    if parent_group is None:
        raise ValueError(f"Parent group not found in docs.json: {parent_group_label}")

    parent_pages = parent_group.get("pages")
    if not isinstance(parent_pages, list):
        raise ValueError(f"Parent group pages missing for {parent_group_label}")

    group = _find_group(parent_pages, group_label)
    if group is None:
        group = {}
        parent_pages.append(group)

    group.clear()
    group["group"] = group_label
    has_native_pages = any(is_native_openapi_page_ref(ref) for ref in openapi_page_refs)
    if has_native_pages:
        group["openapi"] = {
            "source": openapi_source_ref,
            "directory": openapi_directory,
        }
        group["pages"] = [*openapi_page_refs, details_page_ref]
    else:
        group["pages"] = [overview_page_ref, *openapi_page_refs]
    docs_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_native_openapi_page_ref(page_ref: str) -> bool:
    method, separator, path = page_ref.partition(" ")
    return bool(separator and method.lower() in HTTP_METHODS and path.startswith("/"))


def remove_legacy_output(*, output_file: Path) -> None:
    if output_file.exists():
        output_file.unlink()
        print(f"Removed legacy output: {output_file}")


def missing_operation_summaries(spec: dict[str, Any]) -> set[tuple[str, str]]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return set()

    missing: set[tuple[str, str]] = set()
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            summary = operation.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                missing.add((path, method.lower()))
    return missing


def openapi_operation_page_refs(spec: dict[str, Any]) -> list[str]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []

    refs: list[str] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            refs.append(f"{method.upper()} {path}")
    return refs


def legacy_openapi_operation_page_ref(*, method: str, path: str, directory: str) -> str:
    mintlify_path = re.sub(r"\{([^{}]+)\}", r":\1", path)
    slug = mintlify_path.removeprefix("/").replace("/", "").lower()
    return f"{directory.rstrip('/')}/{method.lower()}-{slug}"


def configured_manual_operations(
    source_config: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    directory: str = DEFAULT_OPENAPI_DIRECTORY,
) -> list[dict[str, str]]:
    configured = source_config.get("manual_operations") or []
    if configured == "all":
        if spec is None:
            raise ValueError(
                "manual_operations 'all' requires the published OpenAPI spec"
            )
        return [
            {
                "method": method,
                "path": path,
                "page_ref": legacy_openapi_operation_page_ref(
                    method=method,
                    path=path,
                    directory=directory,
                ),
            }
            for method, path in openapi_operation_identities(spec)
        ]
    if not isinstance(configured, list):
        raise ValueError("manual_operations must be an array or 'all'")
    operations: list[dict[str, str]] = []
    for index, value in enumerate(configured):
        if not isinstance(value, dict):
            raise ValueError(f"manual_operations[{index}] must be an object")
        operation: dict[str, str] = {}
        for key in ("method", "path", "page_ref"):
            field = value.get(key)
            if not isinstance(field, str) or not field.strip():
                raise ValueError(
                    f"manual_operations[{index}].{key} must be a non-empty string"
                )
            operation[key] = field.strip()
        operation["method"] = operation["method"].upper()
        if operation["method"].lower() not in HTTP_METHODS:
            raise ValueError(
                f"manual_operations[{index}].method is not supported: {operation['method']}"
            )
        operations.append(operation)
    identities = [(operation["method"], operation["path"]) for operation in operations]
    if len(identities) != len(set(identities)):
        raise ValueError("manual_operations contains duplicate method/path identities")
    page_refs = [operation["page_ref"] for operation in operations]
    if len(page_refs) != len(set(page_refs)):
        raise ValueError("manual_operations contains duplicate page_ref values")
    return operations


def openapi_operation_identities(spec: dict[str, Any]) -> list[tuple[str, str]]:
    identities: list[tuple[str, str]] = []
    for page_ref in openapi_operation_page_refs(spec):
        method, path = page_ref.split(" ", 1)
        identities.append((method, path))
    return identities


def openapi_navigation_page_refs(
    spec: dict[str, Any], *, manual_operations: list[dict[str, str]]
) -> list[str]:
    manual_refs = {
        (operation["method"], operation["path"]): operation["page_ref"]
        for operation in manual_operations
    }
    page_refs: list[str] = []
    for page_ref in openapi_operation_page_refs(spec):
        method, path = page_ref.split(" ", 1)
        page_refs.append(manual_refs.get((method, path), page_ref))
    return page_refs


def generated_operation_summary(path: str, method: str) -> str:
    mintlify_path = re.sub(r"\{([^{}]+)\}", r":\1", path)
    return f"{method.upper()} {mintlify_path}"


def add_missing_operation_summaries(text: str) -> str:
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError("Expected generated OpenAPI YAML to parse as an object")

    missing = missing_operation_summaries(spec)
    if not missing:
        return text

    lines = text.splitlines()
    output_lines: list[str] = []
    in_paths = False
    paths_indent = ""
    current_path: str | None = None
    current_path_indent: str | None = None

    for line in lines:
        output_lines.append(line)

        paths_match = re.fullmatch(r"(?P<indent>\s*)paths:\s*", line)
        if paths_match:
            in_paths = True
            paths_indent = paths_match.group("indent")
            current_path = None
            current_path_indent = None
            continue

        if not in_paths:
            continue

        if line and not line.startswith(f"{paths_indent} "):
            in_paths = False
            current_path = None
            current_path_indent = None
            continue

        path_match = re.fullmatch(
            rf"(?P<indent>{re.escape(paths_indent)}\s{{2}})(?P<path>/.*):\s*", line
        )
        if path_match:
            current_path = path_match.group("path")
            current_path_indent = path_match.group("indent")
            continue

        if current_path is None or current_path_indent is None:
            continue

        method_match = re.fullmatch(
            rf"(?P<indent>{re.escape(current_path_indent)}\s{{2}})(?P<method>{'|'.join(sorted(HTTP_METHODS))}):\s*",
            line,
        )
        if method_match is None:
            continue

        method = method_match.group("method")
        if (current_path, method) in missing:
            summary_indent = f"{method_match.group('indent')}  "
            output_lines.append(
                f'{summary_indent}summary: "{generated_operation_summary(current_path, method)}"'
            )

    rendered = "\n".join(output_lines).rstrip() + "\n"
    parsed = yaml.safe_load(rendered)
    if not isinstance(parsed, dict):
        raise ValueError(
            "Generated OpenAPI YAML stopped parsing after summary insertion"
        )
    remaining = missing_operation_summaries(parsed)
    if remaining:
        details = ", ".join(
            f"{method.upper()} {path}" for path, method in sorted(remaining)
        )
        raise ValueError(
            f"Failed to insert generated summaries for OpenAPI operations: {details}"
        )
    return rendered


def sanitize_internal_todos(text: str) -> str:
    return INTERNAL_TODO_LINE_RE.sub("", text)


def normalize_mintlify_openapi_text(text: str) -> str:
    return add_missing_operation_summaries(sanitize_internal_todos(text))


def normalize_mintlify_openapi(openapi_path: Path) -> None:
    original = openapi_path.read_text(encoding="utf-8")
    normalized = normalize_mintlify_openapi_text(original)
    if normalized != original:
        openapi_path.write_text(normalized, encoding="utf-8")


def mintlify_openapi_page_refs(openapi_path: Path) -> list[str]:
    spec = yaml.safe_load(openapi_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError(
            f"Expected generated OpenAPI YAML to parse as an object: {openapi_path}"
        )
    return openapi_operation_page_refs(spec)


def operation_items(path_item: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (method.lower(), operation)
        for method, operation in path_item.items()
        if method.lower() in HTTP_METHODS and isinstance(operation, dict)
    ]


def operation_summary(path: str, path_item: dict[str, Any]) -> str:
    summaries: list[str] = []
    for method, operation in operation_items(path_item):
        summary = str(operation.get("summary") or "").strip()
        description = str(operation.get("description") or "").strip()
        label = (
            summary
            if summary
            and summary not in {path, generated_operation_summary(path, method)}
            else description
        )
        if label:
            summaries.append(f"{method.upper()}: {label}")
    if summaries:
        return compact_text("; ".join(summaries), limit=190)
    return "OpenAPI endpoint"


def operation_methods(path_item: dict[str, Any]) -> list[str]:
    return [method.upper() for method, _operation in operation_items(path_item)]


def path_item_fingerprint(path_item: Any) -> str:
    return json.dumps(
        path_item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def versioned_openapi_specs(
    *,
    source_config: dict[str, Any],
    cache_dir: Path,
    versions: list[dict[str, str]],
    spec_filename: str,
    force_refresh: bool,
) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for entry in versions:
        archive_path = ensure_bundle_archive(
            source_config=source_config,
            cache_dir=cache_dir,
            version_entry=entry,
            force_refresh=force_refresh,
        )
        spec = yaml.safe_load(
            normalize_mintlify_openapi_text(
                read_bundle_spec_text(
                    archive_path,
                    source_config=source_config,
                    spec_filename=spec_filename,
                )
            )
        )
        if not isinstance(spec, dict):
            raise ValueError(
                f"Expected OpenAPI spec for {entry['version']} to parse as an object"
            )
        specs[entry["version"]] = spec
    return specs


def write_manual_operation_pages(
    *,
    docs_json_path: Path,
    specs_by_version: dict[str, dict[str, Any]],
    publish_version: str,
    server: str,
    manual_operations: list[dict[str, str]],
    history_report: SurfaceHistoryReport,
) -> set[Path]:
    published_spec = specs_by_version[publish_version]
    history_items_by_route = {
        item.route: item for item in history_report.current_items() if item.route
    }
    written_paths: set[Path] = set()
    for operation in manual_operations:
        route = f"/{operation['page_ref']}"
        history_item = history_items_by_route.get(route)
        if history_item is None:
            raise ValueError(f"Current JSON OpenAPI operation has no history item: {route}")
        history_events = list(
            history_events_for_item(
                history_item,
                comparison_versions=history_report.comparison_versions,
            )
        )
        page = render_manual_openapi_operation(
            spec=published_spec,
            options=ManualOpenAPIRenderOptions(
                method=operation["method"],
                path=operation["path"],
                output_path=f"{operation['page_ref']}.mdx",
                server=server,
            ),
            history_events=history_events,
            publish_version=publish_version,
        )
        output_path = docs_json_path.parent / f"{operation['page_ref']}.mdx"
        write_page(page, output_path)
        written_paths.add(output_path.resolve())
        print(f"Generated manual OpenAPI page: {output_path}")
    return written_paths


def remove_stale_manual_operation_pages(
    *,
    docs_json_path: Path,
    openapi_directory: str,
    current_pages: set[Path],
    preserved_pages: set[Path],
) -> None:
    output_directory = docs_json_path.parent / openapi_directory
    if not output_directory.exists():
        return
    keep = {path.resolve() for path in current_pages | preserved_pages}
    for output_path in output_directory.glob("*.mdx"):
        if output_path.resolve() not in keep:
            output_path.unlink()
            print(f"Removed stale manual OpenAPI page: {output_path}")


def strip_raw_markdown_trailing_whitespace(page: Page) -> Page:
    return Page(
        path=page.path,
        title=page.title,
        description=page.description,
        blocks=[
            RawMarkdown("\n".join(line.rstrip() for line in block.text.splitlines()))
            if isinstance(block, RawMarkdown)
            else block
            for block in page.blocks
        ],
    )


def build_openapi_details_page(
    *,
    specs_by_version: dict[str, dict[str, Any]],
    versions: list[str],
    publish_version: str,
    details_page_ref: str,
    source_name: str,
) -> Any:
    latest = specs_by_version[publish_version]
    latest_paths = latest.get("paths")
    if not isinstance(latest_paths, dict):
        raise ValueError("Published OpenAPI spec must define a paths object")

    version_path_items: dict[str, dict[str, Any]] = {}
    for version in versions:
        paths = specs_by_version[version].get("paths")
        version_path_items[version] = paths if isinstance(paths, dict) else {}

    endpoint_cards: list[ReferenceCard] = []
    version_cards: list[ReferenceCard] = []
    for path, path_item in latest_paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        present_versions = [
            version
            for version in versions
            if isinstance(version_path_items[version].get(path), dict)
        ]
        if not present_versions:
            continue
        introduced = present_versions[0]
        last_seen = present_versions[-1]
        changed_versions: list[str] = []
        previous_fingerprint: str | None = None
        for version in present_versions:
            fingerprint = path_item_fingerprint(version_path_items[version][path])
            if previous_fingerprint is not None and fingerprint != previous_fingerprint:
                changed_versions.append(version)
            previous_fingerprint = fingerprint

        badges = [
            ReferenceBadge(
                ", ".join(operation_methods(path_item)) or "Endpoint", tone="protocol"
            ),
            ReferenceBadge(f"Since {introduced}", tone="added"),
        ]
        if changed_versions:
            badges.append(
                ReferenceBadge(f"Changed {changed_versions[-1]}", tone="changed")
            )
        if any(
            bool(operation.get("deprecated"))
            for _method, operation in operation_items(path_item)
        ):
            badges.append(ReferenceBadge("Deprecated", tone="removed"))
        endpoint_cards.append(
            ReferenceCard(
                title=path,
                summary=operation_summary(path, path_item),
                badges=badges,
                meta_items=[
                    ReferenceMetaItem(
                        "Operations", ", ".join(operation_methods(path_item)) or "-"
                    ),
                    ReferenceMetaItem("Last seen", last_seen),
                ],
            )
        )

    for version in versions:
        current_paths = version_path_items[version]
        previous_index = versions.index(version) - 1
        previous_paths = (
            version_path_items[versions[previous_index]] if previous_index >= 0 else {}
        )
        current_keys = {
            key
            for key, value in current_paths.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
        previous_keys = {
            key
            for key, value in previous_paths.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
        changed = sum(
            1
            for key in current_keys & previous_keys
            if path_item_fingerprint(current_paths[key])
            != path_item_fingerprint(previous_paths[key])
        )
        version_cards.append(
            ReferenceCard(
                title=version,
                summary="Endpoint changes included in this release snapshot.",
                badges=[
                    ReferenceBadge(
                        f"Added {len(current_keys - previous_keys)}", tone="added"
                    ),
                    ReferenceBadge(f"Changed {changed}", tone="changed"),
                    ReferenceBadge(
                        f"Removed {len(previous_keys - current_keys)}", tone="removed"
                    ),
                ],
            )
        )

    return strip_raw_markdown_trailing_whitespace(
        render_collection_page(
            ReferenceCollectionPage(
                path=f"{details_page_ref}.mdx",
                title="Details and history",
                description="JSON Ledger API OpenAPI endpoint details and version history.",
                eyebrow="OpenAPI Reference",
                summary="Endpoint overview for the JSON Ledger API OpenAPI surface, built from versioned release snapshots.",
                badges=[
                    ReferenceBadge("OpenAPI", tone="protocol"),
                    ReferenceBadge(publish_version, tone="neutral"),
                ],
                meta_items=[
                    ReferenceMetaItem("Publish version", publish_version),
                    ReferenceMetaItem("Source", source_name),
                    ReferenceMetaItem("Version filter", ", ".join(versions)),
                ],
                sections=[
                    ReferenceSection(
                        heading="Endpoints",
                        body_markdown=safe_markdown_text(
                            "Select an OpenAPI operation from the sidebar for request and response details. "
                            "This page summarizes endpoint lifecycle changes across the configured Ledger API versions."
                        ),
                        cards=endpoint_cards,
                    ),
                    ReferenceSection(
                        heading="Version Summary",
                        cards=version_cards,
                    ),
                ],
            )
        )
    )


def write_openapi_details_page(
    *,
    docs_json_path: Path,
    details_page_ref: str,
    page: Any,
) -> None:
    write_page(page, docs_json_path.parent / f"{details_page_ref}.mdx")


def build_openapi_overview_page(
    *,
    overview_page_ref: str,
    publish_version: str,
    source_name: str,
    raw_spec_ref: str,
    operation_count: int,
) -> Page:
    return strip_raw_markdown_trailing_whitespace(
        render_collection_page(
            ReferenceCollectionPage(
                path=f"{overview_page_ref}.mdx",
                title="JSON Ledger API OpenAPI",
                description="JSON Ledger API OpenAPI reference overview and raw specification download.",
                eyebrow="Ledger API",
                summary=(
                    "Generated operation reference for the JSON Ledger API, with lifecycle "
                    "history embedded on each operation page."
                ),
                badges=[
                    ReferenceBadge("OpenAPI", tone="protocol"),
                    ReferenceBadge(publish_version, tone="neutral"),
                ],
                meta_items=[
                    ReferenceMetaItem("Operations", str(operation_count)),
                    ReferenceMetaItem("Source", source_name),
                ],
                sections=[
                    ReferenceSection(
                        heading="Specification",
                        body_markdown=(
                            "[Download the published OpenAPI specification]"
                            f"(/{raw_spec_ref})."
                        ),
                    )
                ],
            )
        )
    )


def write_openapi_overview_page(
    *, docs_json_path: Path, overview_page_ref: str, page: Page
) -> Path:
    output_path = docs_json_path.parent / f"{overview_page_ref}.mdx"
    write_page(page, output_path)
    return output_path


def remove_openapi_details_page(*, docs_json_path: Path, details_page_ref: str) -> None:
    output_path = docs_json_path.parent / f"{details_page_ref}.mdx"
    if output_path.exists():
        output_path.unlink()
        print(f"Removed OpenAPI details/history page: {output_path}")


def ensure_redirect(*, docs_json_path: Path, source: str, destination: str) -> None:
    payload = load_json(docs_json_path)
    redirects = payload.setdefault("redirects", [])
    if not isinstance(redirects, list):
        raise ValueError("docs.json redirects must be an array")
    matches = [
        redirect
        for redirect in redirects
        if isinstance(redirect, dict) and redirect.get("source") == source
    ]
    if len(matches) > 1:
        raise ValueError(f"Duplicate redirect source in docs.json: {source}")
    redirect = {"source": source, "destination": destination}
    if matches:
        matches[0].clear()
        matches[0].update(redirect)
    else:
        redirects.append(redirect)
    docs_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    source_config = load_json(Path(args.source_config).resolve())
    include_versions = set(args.version) if args.version else None
    versions = selected_versions(source_config, include_versions)
    publish_entry = resolve_publish_version(
        source_config=source_config,
        versions=versions,
        requested_version=args.publish_version,
    )

    output_spec = Path(args.output_spec).resolve()
    cache_dir = Path(args.cache_dir).resolve()
    materialize_bundle_spec(
        source_config=source_config,
        cache_dir=cache_dir,
        version_entry=publish_entry,
        spec_filename="openapi.yaml",
        output_path=output_spec,
        force_refresh=args.force_refresh,
    )
    normalize_mintlify_openapi(output_spec)
    print(f"Published Mintlify OpenAPI source: {output_spec}")

    docs_json_path = Path(args.docs_json).resolve()
    version_labels = [entry["version"] for entry in versions]
    source_name = str(
        source_config.get("source")
        or "Canton release bundle JSON Ledger API OpenAPI fixtures"
    )
    specs_by_version = versioned_openapi_specs(
        source_config=source_config,
        cache_dir=cache_dir,
        versions=versions,
        spec_filename="openapi.yaml",
        force_refresh=args.force_refresh,
    )
    manual_operations = configured_manual_operations(
        source_config,
        spec=specs_by_version[publish_entry["version"]],
        directory=args.openapi_directory,
    )
    history_report = build_openapi_history_report(
        surface_id="json-ledger-api-openapi",
        title="JSON Ledger API OpenAPI",
        configured_scope="JSON Ledger API OpenAPI operations",
        scopes=(
            OpenAPIHistoryScope(
                id="json-ledger-api",
                specs_by_version=specs_by_version,
                current_routes={
                    (operation["method"].lower(), operation["path"]): (
                        f"/{operation['page_ref']}"
                    )
                    for operation in manual_operations
                },
            ),
        ),
        comparison_versions=tuple(version_labels),
        publish_version=publish_entry["version"],
        source_artifacts=tuple(
            SourceArtifact(
                version=entry["version"],
                source=source_name,
                revision=entry.get("canton_version"),
            )
            for entry in versions
        ),
        version_policy=VersionSelectionPolicy.CONFIGURED_PUBLISH_VERSION,
        limitations=(
            "Release-bundle OpenAPI snapshots establish operation additions, normalized updates, authored deprecations, and removals.",
        ),
    )
    validate_history_report(history_report)
    history_report_path = Path(args.history_report).resolve()
    write_history_report(history_report_path, history_report)
    print(f"Generated normalized JSON OpenAPI history report: {history_report_path}")
    manual_page_paths = write_manual_operation_pages(
        docs_json_path=docs_json_path,
        specs_by_version=specs_by_version,
        publish_version=publish_entry["version"],
        server=str(source_config.get("manual_api_server") or "http://localhost:7575"),
        manual_operations=manual_operations,
        history_report=history_report,
    )
    reference_nav.regroup_ledger_api_nav(
        docs_json_path=docs_json_path,
        dropdown_label=args.nav_dropdown,
    )
    update_docs_navigation(
        docs_json_path=docs_json_path,
        dropdown_label=args.nav_dropdown,
        parent_group_label=args.parent_group,
        group_label=args.group_label,
        openapi_source_ref=docs_relative_file_ref(output_spec, docs_json_path),
        openapi_directory=args.openapi_directory,
        overview_page_ref=args.overview_page_ref,
        details_page_ref=args.details_page_ref,
        openapi_page_refs=openapi_navigation_page_refs(
            specs_by_version[publish_entry["version"]],
            manual_operations=manual_operations,
        ),
    )
    has_native_pages = any(
        is_native_openapi_page_ref(page_ref)
        for page_ref in openapi_navigation_page_refs(
            specs_by_version[publish_entry["version"]],
            manual_operations=manual_operations,
        )
    )
    if has_native_pages:
        write_openapi_details_page(
            docs_json_path=docs_json_path,
            details_page_ref=args.details_page_ref,
            page=build_openapi_details_page(
                specs_by_version=specs_by_version,
                versions=version_labels,
                publish_version=publish_entry["version"],
                details_page_ref=args.details_page_ref,
                source_name=source_name,
            ),
        )
    else:
        raw_spec_ref = docs_relative_file_ref(output_spec, docs_json_path)
        overview_path = write_openapi_overview_page(
            docs_json_path=docs_json_path,
            overview_page_ref=args.overview_page_ref,
            page=build_openapi_overview_page(
                overview_page_ref=args.overview_page_ref,
                publish_version=publish_entry["version"],
                source_name=source_name,
                raw_spec_ref=raw_spec_ref,
                operation_count=len(manual_operations),
            ),
        )
        remove_openapi_details_page(
            docs_json_path=docs_json_path,
            details_page_ref=args.details_page_ref,
        )
        ensure_redirect(
            docs_json_path=docs_json_path,
            source=f"/{args.details_page_ref}",
            destination=f"/{args.overview_page_ref}",
        )
        remove_stale_manual_operation_pages(
            docs_json_path=docs_json_path,
            openapi_directory=args.openapi_directory,
            current_pages=manual_page_paths,
            preserved_pages={overview_path},
        )
    remove_legacy_output(output_file=LEGACY_OUTPUT_FILE.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
