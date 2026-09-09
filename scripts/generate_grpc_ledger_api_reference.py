#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from docs_env import ensure_repo_direnv

REPO_ROOT = Path(__file__).resolve().parents[1]
ensure_repo_direnv(repo_root=REPO_ROOT, script_path=Path(__file__).resolve(), argv=sys.argv[1:])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import generate_canton_protobuf_history as canton_protobuf_history
import reference_nav
from x2mdx.protobuf.lifecycle import (
    build_endpoint_lifecycle,
    build_protobuf_history_report_from_sources,
    build_release_diffs,
)
from x2mdx.history import ReferenceFormat, validate_history_report, write_history_report
from x2mdx.protobuf.history import build_protobuf_surface_history_report
from x2mdx.protobuf.render import build_pages, package_page_path, slugify_segment
from x2mdx.protobuf.render import operation_page_path
from x2mdx.protobuf.snapshots import load_protobuf_sources
from x2mdx.render import write_pages


DEFAULT_SOURCE_CONFIG = REPO_ROOT / "config" / "x2mdx" / "grpc-ledger-api-reference" / "source-artifacts.json"
DEFAULT_CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "x2mdx"
DEFAULT_CACHE_DIR = DEFAULT_CACHE_ROOT / "protobuf-history"
DEFAULT_MANIFEST = REPO_ROOT / ".internal" / "generated" / "x2mdx" / "grpc-ledger-api-reference" / "manifest.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs-source" / "reference" / "grpc-ledger-api-reference"
DEFAULT_DOCS_JSON = REPO_ROOT / "docs-source" / "docs.json"
DEFAULT_REPO_DIR = DEFAULT_CACHE_DIR / "repos" / "canton"
GROUP_LABEL = "gRPC API"
LEGACY_GROUP_LABEL = "gRPC Ledger API Reference"
OVERVIEW_NAME = "overview.mdx"
OVERVIEW_TITLE = "Ledger API gRPC"
DEFAULT_INSERT_AFTER_GROUP = "Ledger API Endpoints"
DEFAULT_SOURCE_NAME = "Canton Ledger API protobuf release bundles"
DEFAULT_NAV_GROUP = ["Ledger API"]
LEDGER_API_PACKAGE_PREFIX = "com.daml.ledger.api."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Canton release-bundle protobuf inputs, filter them to Ledger API packages, and render a gRPC Ledger API reference."
    )
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--manifest-out", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--docs-json", default=str(DEFAULT_DOCS_JSON))
    parser.add_argument("--nav-dropdown", default="API Reference")
    parser.add_argument("--nav-group", action="append")
    parser.add_argument("--insert-after-group", default=DEFAULT_INSERT_AFTER_GROUP)
    parser.add_argument("--repo-dir", default=str(DEFAULT_REPO_DIR))
    parser.add_argument("--version", action="append", help="Explicit version to include. Repeat to limit generation.")
    parser.add_argument("--min-version", help="Minimum stable version to include when auto-discovering tags.")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip fetching tags from origin before generation.")
    parser.add_argument("--force-refresh", action="store_true", help="Refresh cached protobuf bundles and descriptor images.")
    parser.add_argument(
        "--source-name",
        default=DEFAULT_SOURCE_NAME,
        help="Source label embedded in generated content.",
    )
    parser.add_argument(
        "--version-filter",
        help="Version-filter label embedded in generated content.",
    )
    args = parser.parse_args()
    if args.nav_group is None:
        args.nav_group = DEFAULT_NAV_GROUP
    return args


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def package_prefixes(source_config: dict[str, Any]) -> tuple[str, ...]:
    configured = source_config.get("package_prefixes")
    if configured is None:
        return ("com.daml.ledger.api.v2",)
    if not isinstance(configured, list) or not all(isinstance(item, str) and item for item in configured):
        raise ValueError("Source config package_prefixes must be a list of non-empty strings")
    return tuple(configured)


def package_matches(package_name: str, *, prefixes: tuple[str, ...]) -> bool:
    return any(package_name.startswith(prefix) for prefix in prefixes)


def filter_snapshot(snapshot: dict[str, Any], *, prefixes: tuple[str, ...]) -> dict[str, Any]:
    packages = [
        copy.deepcopy(package)
        for package in snapshot["packages"]
        if package_matches(str(package["package"]), prefixes=prefixes)
    ]
    files = {
        key: copy.deepcopy(value)
        for key, value in snapshot["files"].items()
        if package_matches(str(value["package"]), prefixes=prefixes)
    }
    services = {
        key: copy.deepcopy(value)
        for key, value in snapshot["services"].items()
        if package_matches(str(value["package"]), prefixes=prefixes)
    }
    endpoints = {
        key: copy.deepcopy(value)
        for key, value in snapshot["endpoints"].items()
        if package_matches(str(value["package"]), prefixes=prefixes)
    }
    messages = {
        key: copy.deepcopy(value)
        for key, value in snapshot["messages"].items()
        if package_matches(str(value["package"]), prefixes=prefixes)
    }
    fields = {
        key: copy.deepcopy(value)
        for key, value in snapshot["fields"].items()
        if package_matches(str(value["package"]), prefixes=prefixes)
    }
    enums = {
        key: copy.deepcopy(value)
        for key, value in snapshot["enums"].items()
        if package_matches(str(value["package"]), prefixes=prefixes)
    }
    enum_values = {
        key: copy.deepcopy(value)
        for key, value in snapshot["enumValues"].items()
        if package_matches(str(value["package"]), prefixes=prefixes)
    }
    return {
        **snapshot,
        "packages": packages,
        "files": files,
        "services": services,
        "endpoints": endpoints,
        "messages": messages,
        "fields": fields,
        "enums": enums,
        "enumValues": enum_values,
        "stats": {
            "protoFiles": len(files),
            "packages": len(packages),
            "services": len(services),
            "endpoints": len(endpoints),
            "messages": len(messages),
            "fields": len(fields),
            "enums": len(enums),
            "enumValues": len(enum_values),
        },
    }


def build_filtered_report(
    manifest_path: Path,
    *,
    prefixes: tuple[str, ...],
    source_name: str,
    version_filter: str,
) -> dict[str, Any]:
    sources = load_protobuf_sources(manifest_path)
    report = build_protobuf_history_report_from_sources(
        sources,
        source_name=source_name,
        version_filter=version_filter,
    )
    releases = [
        {
            **copy.deepcopy(release),
            "snapshot": filter_snapshot(release["snapshot"], prefixes=prefixes),
        }
        for release in report["releases"]
    ]
    if not any(release["snapshot"]["packages"] for release in releases):
        selected = ", ".join(prefixes)
        raise ValueError(f"No protobuf packages matched the configured prefixes: {selected}")

    build_release_diffs(releases)
    endpoint_lifecycle = [
        copy.deepcopy(entry)
        for entry in build_endpoint_lifecycle(releases)
        if package_matches(str(entry["package"]), prefixes=prefixes)
    ]
    latest_snapshot = releases[-1]["snapshot"]
    return {
        "sourceName": source_name,
        "versionFilter": version_filter,
        "repo": copy.deepcopy(report["repo"]),
        "latestRelease": releases[-1]["tag"],
        "latestSnapshot": latest_snapshot,
        "releases": releases,
        "endpointLifecycle": endpoint_lifecycle,
    }


def replace_text(path: Path, replacements: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text
    for old, new in replacements:
        updated = updated.replace(old, new)
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def mdx_title(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^title:\s*"([^"]+)"\s*$', text, flags=re.MULTILINE)
    if match:
        return match.group(1)
    match = re.search(r"^title:\s*'([^']+)'\s*$", text, flags=re.MULTILINE)
    if match:
        return match.group(1)
    return path.stem


def service_name(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"<dt>Service</dt>\s*<dd>([^<]+)</dd>", text)
    if match:
        return match.group(1)
    return path.parent.name


def package_nav_label(package_name: str) -> str:
    if package_name.startswith(LEDGER_API_PACKAGE_PREFIX):
        return package_name.removeprefix(LEDGER_API_PACKAGE_PREFIX)
    return package_name


def package_sort_key(package_name: str) -> tuple[str, str]:
    label = package_nav_label(package_name)
    return (label.lower(), package_name.lower())


def sort_report_packages(report: dict[str, Any]) -> None:
    for release in report["releases"]:
        release["snapshot"]["packages"].sort(key=lambda package: package_sort_key(str(package["package"])))


def retitle_generated_pages(*, output_dir: Path) -> None:
    overview_path = output_dir / "index.mdx"
    if overview_path.exists():
        replace_text(
            overview_path,
            [
                ('title: "Canton Protobuf History"', f'title: "{OVERVIEW_TITLE}"'),
                ('title: "Canton Protobuf Reference"', f'title: "{OVERVIEW_TITLE}"'),
                (
                    'description: "Descriptor-backed protobuf API history grouped by package."',
                    'description: "Generated Ledger API gRPC reference grouped by package."',
                ),
                (
                    "This page is generated from local descriptor-image snapshots with source info.",
                    "This page is generated from published Canton protobuf release bundles and filtered to the Ledger API gRPC packages.",
                ),
                ('<p class="x2mdx-ref-eyebrow">Protobuf Reference</p>', '<p class="x2mdx-ref-eyebrow">gRPC API</p>'),
                ('<h1 class="x2mdx-ref-title">Canton Protobuf Reference</h1>', f'<h1 class="x2mdx-ref-title">{OVERVIEW_TITLE}</h1>'),
                (
                    '<span class="x2mdx-ref-badge x2mdx-ref-badge--protocol">Protobuf</span>',
                    '<span class="x2mdx-ref-badge x2mdx-ref-badge--protocol">gRPC</span>',
                ),
            ],
        )
    packages_dir = output_dir / "packages"
    for package_page in sorted(packages_dir.glob("*.mdx")):
        replace_text(package_page, [("Canton Protobuf History", GROUP_LABEL)])


def shorten_package_page_titles(*, output_dir: Path, report: dict[str, Any]) -> None:
    for package_doc in report["latestSnapshot"]["packages"]:
        package_name = str(package_doc["package"])
        label = package_nav_label(package_name)
        if label == package_name:
            continue
        package_page = package_page_path(output_dir, package_name)
        if not package_page.exists():
            package_page = output_dir / f"{slugify_segment(package_name)}.mdx"
        if not package_page.exists():
            continue
        replace_text(
            package_page,
            [
                (f'title: "{package_name}"', f'title: "{label}"'),
                (f'<h1 class="x2mdx-ref-title">{html_text(package_name)}</h1>', f'<h1 class="x2mdx-ref-title">{html_text(label)}</h1>'),
            ],
        )


def normalize_flattened_links_and_labels(*, output_dir: Path, report: dict[str, Any]) -> None:
    package_labels = {
        slugify_segment(str(package_doc["package"])): package_nav_label(str(package_doc["package"]))
        for package_doc in report["latestSnapshot"]["packages"]
    }
    package_names = {
        slugify_segment(str(package_doc["package"])): str(package_doc["package"])
        for package_doc in report["latestSnapshot"]["packages"]
    }

    overview_path = output_dir / OVERVIEW_NAME
    if overview_path.exists():
        replacements: list[tuple[str, str]] = []
        for package_slug, label in package_labels.items():
            package_name = package_names[package_slug]
            replacements.extend(
                [
                    (f'href="packages/{package_slug}"', f'href="./{package_slug}"'),
                    (f'href="./packages/{package_slug}"', f'href="./{package_slug}"'),
                    (
                        f'<a class="x2mdx-ref-card-title" href="./{package_slug}">{html_text(package_name)}</a>',
                        f'<a class="x2mdx-ref-card-title" href="./{package_slug}">{html_text(label)}</a>',
                    ),
                ]
            )
        replace_text(overview_path, replacements)

    for package_slug, label in package_labels.items():
        package_page = output_dir / f"{package_slug}.mdx"
        if package_page.exists():
            replace_text(
                package_page,
                [
                    ('href="../index"', 'href="./overview"'),
                    (f'href="../operations/{package_slug}/', f'href="./{package_slug}/'),
                ],
            )

        package_operation_dir = output_dir / package_slug
        if not package_operation_dir.is_dir():
            continue
        package_name = package_names[package_slug]
        for operation_page in package_operation_dir.glob("*/*.mdx"):
            replace_text(
                operation_page,
                [
                    ('href="../../../index">Protobuf</a>', 'href="../../overview">gRPC API</a>'),
                    (
                        f'href="../../../packages/{package_slug}">{html_text(package_name)}</a>',
                        f'href="../../{package_slug}">{html_text(label)}</a>',
                    ),
                ],
            )


def flattened_page_path(path: Path, *, output_dir: Path) -> Path:
    relative = path.resolve().relative_to(output_dir.resolve())
    if relative == Path("index.mdx"):
        return output_dir / OVERVIEW_NAME
    if relative.parts and relative.parts[0] in {"packages", "operations"}:
        return output_dir.joinpath(*relative.parts[1:])
    return path


def flatten_generated_tree(*, output_dir: Path, page_paths: list[Path]) -> list[Path]:
    flattened_paths = [flattened_page_path(path, output_dir=output_dir) for path in page_paths]

    index_path = output_dir / "index.mdx"
    overview_path = output_dir / OVERVIEW_NAME
    if index_path.exists():
        if overview_path.exists():
            overview_path.unlink()
        index_path.rename(overview_path)

    for source_dir_name in ("packages", "operations"):
        source_dir = output_dir / source_dir_name
        if not source_dir.is_dir():
            continue
        for source_path in sorted(source_dir.iterdir()):
            destination = output_dir / source_path.name
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            source_path.rename(destination)
        source_dir.rmdir()

    return flattened_paths


def html_text(value: str) -> str:
    return html.escape(value, quote=False)


def split_package_local_name(name: str, *, package_names: list[str]) -> tuple[str, str | None]:
    for package_name in sorted(package_names, key=len, reverse=True):
        prefix = f"{package_name}."
        if name.startswith(prefix):
            return name.removeprefix(prefix), package_name
    return name, None


def shorten_package_page_headings(*, output_dir: Path, report: dict[str, Any]) -> None:
    latest_snapshot = report["latestSnapshot"]
    files = latest_snapshot["files"]
    messages = latest_snapshot["messages"]
    enums = latest_snapshot["enums"]
    package_names = [str(package["package"]) for package in latest_snapshot["packages"]]

    for package_doc in latest_snapshot["packages"]:
        package_name = str(package_doc["package"])
        package_page = package_page_path(output_dir, package_name)
        if not package_page.exists():
            package_page = output_dir / f"{slugify_segment(package_name)}.mdx"
        if not package_page.exists():
            continue

        text = package_page.read_text(encoding="utf-8")
        for file_id in package_doc["fileIds"]:
            file_doc = files.get(file_id)
            if not file_doc:
                continue
            repo_path = str(file_doc["repoPath"])
            path = PurePosixPath(repo_path)
            text = text.replace(
                f'<span class="x2mdx-ref-card-title">{html_text(repo_path)}</span>',
                f'<span class="x2mdx-ref-card-title">{html_text(path.name)}</span>',
            )
            text = text.replace(
                "    <p class=\"x2mdx-ref-card-summary\">Current source file in the latest published descriptor snapshot.</p>",
                "    <p class=\"x2mdx-ref-card-summary\">Source file from the latest descriptor snapshot.</p>",
                1,
            )

        package_types = [
            *[
                message
                for message in messages.values()
                if any(str(message["id"]).startswith(f"{candidate}.") for candidate in package_names)
            ],
            *[
                enum_doc
                for enum_doc in enums.values()
                if any(str(enum_doc["id"]).startswith(f"{candidate}.") for candidate in package_names)
            ],
        ]
        for type_doc in package_types:
            full_name = str(type_doc["id"])
            local_name, type_package = split_package_local_name(full_name, package_names=package_names)
            if type_package is None:
                continue
            pattern = re.compile(
                rf"    <h3>{re.escape(html_text(full_name))}</h3>\n"
                rf"    \n"
                rf"    <p class=\"x2mdx-ref-schema-summary\">(?P<summary>[^<]+)</p>"
            )
            text = pattern.sub(
                lambda match: (
                    f"    <h3>{html_text(local_name)}</h3>\n"
                    f"    \n"
                    f"    <p class=\"x2mdx-ref-schema-summary\">{html_text(type_package)} · {match.group('summary')}</p>"
                ),
                text,
            )

        package_page.write_text(text, encoding="utf-8")


def build_nav_group(
    *,
    docs_json_path: Path,
    overview_path: Path,
    page_paths: list[Path],
) -> tuple[dict[str, Any], set[str]]:
    overview_ref = canton_protobuf_history.docs_json_page_ref(overview_path, docs_json_path)
    refs = {overview_ref}
    output_dir = overview_path.parent
    package_groups: list[Any] = []
    for package_page in sorted(
        (path for path in page_paths if path.parent == output_dir and path.name != overview_path.name),
        key=lambda path: mdx_title(path).lower(),
    ):
        package_ref = canton_protobuf_history.docs_json_page_ref(package_page, docs_json_path)
        refs.add(package_ref)
        package_pages: list[Any] = [package_ref]
        package_operation_dir = output_dir / package_page.stem
        service_groups: list[Any] = []
        if package_operation_dir.is_dir():
            service_dirs = sorted(
                (path for path in package_operation_dir.iterdir() if path.is_dir()),
                key=lambda path: path.name,
            )
            for service_dir in service_dirs:
                operation_pages = sorted(service_dir.glob("*.mdx"), key=lambda path: mdx_title(path).lower())
                if not operation_pages:
                    continue
                operation_refs = [
                    canton_protobuf_history.docs_json_page_ref(operation_page, docs_json_path)
                    for operation_page in operation_pages
                ]
                refs.update(operation_refs)
                service_groups.append(
                    {
                        "group": service_name(operation_pages[0]),
                        "pages": operation_refs,
                    }
                )
        if service_groups:
            package_pages.append({"group": "Services", "pages": service_groups})
        package_groups.append({"group": mdx_title(package_page), "pages": package_pages})

    pages: list[Any] = [overview_ref]
    if package_groups:
        pages.append({"group": "Packages", "pages": package_groups})
    return {"group": GROUP_LABEL, "pages": pages}, refs


def insert_group(items: list[Any], *, group: dict[str, Any], after_group: str | None) -> None:
    if after_group:
        for index, item in enumerate(items):
            if isinstance(item, dict) and item.get("group") == after_group:
                items.insert(index + 1, group)
                return
    items.append(group)


def update_docs_navigation(
    *,
    docs_json_path: Path,
    dropdown_label: str,
    parent_groups: list[str],
    insert_after_group: str | None,
    overview_path: Path,
    page_paths: list[Path],
) -> None:
    docs = load_json(docs_json_path)
    pages = reference_nav.navigation_pages(docs, label=dropdown_label, docs_json_path=docs_json_path)

    nav_group, generated_refs = build_nav_group(
        docs_json_path=docs_json_path,
        overview_path=overview_path,
        page_paths=page_paths,
    )
    target_pages = canton_protobuf_history.ensure_group_path(pages, parent_groups)
    target_pages[:] = canton_protobuf_history.prune_nav_items(
        target_pages,
        page_refs=generated_refs,
        group_labels={GROUP_LABEL, LEGACY_GROUP_LABEL},
    )
    insert_group(target_pages, group=nav_group, after_group=insert_after_group)
    docs_json_path.write_text(json.dumps(docs, indent=2) + "\n", encoding="utf-8")
    print(f"Updated docs navigation: {docs_json_path}")


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


def ensure_grpc_redirects(*, docs_json_path: Path, output_dir: Path) -> None:
    prefix = "/" + output_dir.resolve().relative_to(docs_json_path.resolve().parent).as_posix()
    destination = f"{prefix}/overview"
    ensure_redirect(docs_json_path=docs_json_path, source=prefix, destination=destination)
    ensure_redirect(
        docs_json_path=docs_json_path,
        source=f"{prefix}/details",
        destination=destination,
    )


def endpoint_routes(
    report: dict[str, Any],
    *,
    output_dir: Path,
    docs_json_path: Path,
) -> dict[str, str]:
    routes: dict[str, str] = {}
    for endpoint_id, endpoint in report["latestSnapshot"]["endpoints"].items():
        generated_path = operation_page_path(
            output_dir,
            str(endpoint["package"]),
            str(endpoint["service"]),
            str(endpoint["name"]),
        )
        flattened_path = flattened_page_path(generated_path, output_dir=output_dir)
        routes[str(endpoint_id)] = "/" + canton_protobuf_history.docs_json_page_ref(
            flattened_path,
            docs_json_path,
        )
    return routes


def write_manifest(
    *,
    source_config: dict[str, Any],
    cache_dir: Path,
    manifest_path: Path,
    repo_dir: Path,
    include_versions: set[str] | None,
    min_version: str,
    skip_fetch: bool,
    force_refresh: bool,
) -> Path:
    repo_config = source_config.get("repo") if isinstance(source_config.get("repo"), dict) else {}
    remote = repo_config.get("remote")
    if not isinstance(remote, str) or not remote:
        raise ValueError("Source config must define repo.remote")
    bundle_proto_dir = source_config.get("bundle_proto_dir") or "protobuf"
    if not isinstance(bundle_proto_dir, str) or not bundle_proto_dir:
        raise ValueError("Source config must define bundle_proto_dir")

    repo_dir = canton_protobuf_history.ensure_repo(repo_dir, remote=remote, fetch=not skip_fetch)
    selected_tags = canton_protobuf_history.stable_tags(
        repo_dir,
        min_version=min_version,
        include_versions=include_versions,
    )
    if not selected_tags:
        raise ValueError("No stable Canton tags selected")

    releases: list[dict[str, Any]] = []
    for version, tag in selected_tags:
        archive_path = canton_protobuf_history.ensure_bundle_archive(
            source_config=source_config,
            version=version,
            output_path=canton_protobuf_history.bundle_archive_path(cache_dir, version),
            force_refresh=force_refresh,
        )
        protobuf_root = canton_protobuf_history.extract_archive(
            archive_path,
            extract_root=canton_protobuf_history.bundle_extract_root(cache_dir, version),
            bundle_proto_dir=bundle_proto_dir,
            force_refresh=force_refresh,
        )
        import_to_repo_path = canton_protobuf_history.import_to_repo_path_from_bundle(
            protobuf_root,
            selections=canton_protobuf_history.LEDGER_API_SELECTIONS,
        )
        if not import_to_repo_path:
            print(f"Skipping {tag}: no published owned protobuf files found")
            continue
        image_path = canton_protobuf_history.descriptor_image_path(
            cache_dir,
            version,
            surface="grpc-ledger-api",
            selections=canton_protobuf_history.LEDGER_API_SELECTIONS,
        )
        if not image_path.exists() or force_refresh:
            canton_protobuf_history.compile_descriptor_image(
                protobuf_root,
                output_path=image_path,
                selections=canton_protobuf_history.LEDGER_API_SELECTIONS,
            )
        releases.append(
            {
                "version": version,
                "tag": tag,
                "date": canton_protobuf_history.release_date(repo_dir, tag),
                "descriptor_image_path": str(image_path.resolve()),
                "import_to_repo_path": import_to_repo_path,
            }
        )
    if not releases:
        raise ValueError("No protobuf releases were materialized")
    return canton_protobuf_history.write_manifest(
        source_config=source_config,
        releases=releases,
        manifest_path=manifest_path,
    )


def main() -> int:
    args = parse_args()
    source_config = load_json(Path(args.source_config).resolve())
    prefixes = package_prefixes(source_config)
    include_versions = set(args.version) if args.version else None
    min_version = args.min_version or source_config.get("min_version") or "0.0.0"
    if not isinstance(min_version, str):
        raise ValueError("min_version must be a string")

    manifest_path = write_manifest(
        source_config=source_config,
        cache_dir=Path(args.cache_dir).resolve(),
        manifest_path=Path(args.manifest_out).resolve(),
        repo_dir=Path(args.repo_dir).resolve(),
        include_versions=include_versions,
        min_version=min_version,
        skip_fetch=args.skip_fetch,
        force_refresh=args.force_refresh,
    )

    version_filter = args.version_filter
    if not version_filter:
        if include_versions:
            version_filter = "selected Canton release bundles"
        else:
            version_filter = f"stable Canton release bundles >= {min_version}"

    report = build_filtered_report(
        manifest_path,
        prefixes=prefixes,
        source_name=args.source_name,
        version_filter=version_filter,
    )
    sort_report_packages(report)
    output_dir = Path(args.output_dir).resolve()
    docs_json_path = Path(args.docs_json).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    history_report = build_protobuf_surface_history_report(
        report,
        routes=endpoint_routes(
            report,
            output_dir=output_dir,
            docs_json_path=docs_json_path,
        ),
        surface_id="ledger-api-grpc",
        title=OVERVIEW_TITLE,
        configured_scope="Ledger API gRPC endpoints",
        format=ReferenceFormat.GRPC,
    )
    validate_history_report(history_report)
    root, pages = build_pages(
        report,
        output_dir=output_dir,
        history_report=history_report,
    )
    written_paths = write_pages(pages, root)
    write_history_report(output_dir / "history-report.json", history_report)
    retitle_generated_pages(output_dir=output_dir)
    page_paths = flatten_generated_tree(
        output_dir=output_dir,
        page_paths=[root / page.path for page in pages[1:]],
    )
    shorten_package_page_headings(output_dir=output_dir, report=report)
    shorten_package_page_titles(output_dir=output_dir, report=report)
    normalize_flattened_links_and_labels(output_dir=output_dir, report=report)

    overview_path = output_dir / OVERVIEW_NAME
    update_docs_navigation(
        docs_json_path=docs_json_path,
        dropdown_label=args.nav_dropdown,
        parent_groups=args.nav_group or [],
        insert_after_group=args.insert_after_group,
        overview_path=overview_path,
        page_paths=page_paths,
    )
    reference_nav.regroup_ledger_api_nav(
        docs_json_path=docs_json_path,
        dropdown_label=args.nav_dropdown,
    )
    ensure_grpc_redirects(docs_json_path=docs_json_path, output_dir=output_dir)
    print(f"Wrote {len(written_paths)} generated pages under {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
