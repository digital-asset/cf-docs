#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from docs_env import ensure_repo_direnv, repo_direnv_command
from generated_reference_sources.common import stable_dpm_versions
import reference_nav

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "x2mdx"
DEFAULT_SOURCE_CONFIG = REPO_ROOT / "config" / "x2mdx" / "daml-standard-library" / "source-artifacts.json"
DEFAULT_CACHE_DIR = DEFAULT_CACHE_ROOT / "daml-standard-library"
DEFAULT_MANIFEST = REPO_ROOT / ".internal" / "generated" / "x2mdx" / "daml-standard-library" / "manifest.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs-main" / "appdev" / "reference" / "daml-standard-library"
DEFAULT_DOCS_JSON = REPO_ROOT / "docs-main" / "docs.json"
GROUP_LABEL = "Daml Standard Library"
MODULES_GROUP_LABEL = "Modules"
DEFAULT_LIFECYCLE_METADATA = REPO_ROOT / "config" / "x2mdx" / "daml-standard-library" / "lifecycle.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Daml Standard Library docs from local SDK artifacts, write an x2mdx manifest, and render MDX pages."
    )
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--manifest-out", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--docs-json", default=str(DEFAULT_DOCS_JSON))
    parser.add_argument("--nav-dropdown", default="API Reference")
    parser.add_argument("--nav-group", action="append")
    parser.add_argument("--version", action="append", help="Version to include. Repeat to limit generation.")
    parser.add_argument("--publish-version", help="Version whose docs should be published.")
    parser.add_argument("--lifecycle-metadata", default=str(DEFAULT_LIFECYCLE_METADATA))
    parser.add_argument("--force-regenerate", action="store_true")
    parser.add_argument(
        "--source-name",
        default="Published Daml Standard Library docs JSON from local SDK artifacts",
        help="Source label embedded in generated content.",
    )
    parser.add_argument(
        "--version-filter",
        default="configured Daml SDK artifact versions",
        help="Version-filter label embedded in generated content.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def docs_json_page_ref(path: Path, docs_json_path: Path) -> str:
    relative = path.resolve().relative_to(docs_json_path.resolve().parent)
    if relative.suffix != ".mdx":
        raise ValueError(f"Expected MDX file under docs root, got: {path}")
    return relative.with_suffix("").as_posix()


def docs_route_prefix(path: Path, docs_json_path: Path) -> str:
    relative = path.resolve().relative_to(docs_json_path.resolve().parent)
    return "/" + relative.as_posix().strip("/")


def read_mdx_title(path: Path) -> str:
    in_frontmatter = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if not in_frontmatter:
            continue
        if line.startswith("title: "):
            return line.split(":", 1)[1].strip().strip('"')
    raise ValueError(f"Missing title frontmatter in {path}")


def set_mdx_title(path: Path, title: str) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return
    frontmatter_end = text.find("\n---", 4)
    if frontmatter_end == -1:
        return
    frontmatter = text[:frontmatter_end]
    replacement = f'title: "{title}"'
    updated_frontmatter, count = re.subn(
        r"(?m)^title:\s*(?:\"[^\"]*\"|'[^']*'|.+?)\s*$",
        replacement,
        frontmatter,
        count=1,
    )
    if count == 0:
        updated_frontmatter = frontmatter + "\n" + replacement
    updated = updated_frontmatter + text[frontmatter_end:]
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def prune_nav_items(items: list[Any], *, page_refs: set[str], group_labels: set[str]) -> list[Any]:
    pruned: list[Any] = []
    for item in items:
        if isinstance(item, str):
            if item not in page_refs:
                pruned.append(item)
            continue
        if isinstance(item, dict):
            if item.get("group") in group_labels:
                continue
            updated = dict(item)
            pages = updated.get("pages")
            if isinstance(pages, list):
                updated["pages"] = prune_nav_items(pages, page_refs=page_refs, group_labels=group_labels)
            pruned.append(updated)
            continue
        pruned.append(item)
    return pruned


def ensure_group_path(items: list[Any], group_path: list[str]) -> list[Any]:
    current_pages = items
    for label in group_path:
        group = next((item for item in current_pages if isinstance(item, dict) and item.get("group") == label), None)
        if group is None:
            group = {"group": label, "pages": []}
            current_pages.append(group)
        pages = group.get("pages")
        if not isinstance(pages, list):
            pages = []
            group["pages"] = pages
        current_pages = pages
    return current_pages


def find_group_path(items: list[Any], group_path: list[str]) -> list[Any] | None:
    current_pages = items
    for label in group_path:
        group = next((item for item in current_pages if isinstance(item, dict) and item.get("group") == label), None)
        if group is None:
            return None
        pages = group.get("pages")
        if not isinstance(pages, list):
            return None
        current_pages = pages
    return current_pages


def find_group_index(items: list[Any] | None, group_label: str) -> int | None:
    if items is None:
        return None
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("group") == group_label:
            return index
    return None


def generate_json_snapshot(
    *,
    version: str,
    output_json: Path,
    package_set: str,
    sdk_source: str,
    lf_target: str | None,
    force_regenerate: bool,
) -> None:
    if output_json.exists() and not force_regenerate:
        print(f"Using cached Daml docs JSON: {output_json}")
        return

    output_json.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "bash",
        str(REPO_ROOT / "scripts" / "generate_daml_standard_library_json.sh"),
        "--output-json",
        str(output_json),
        "--sdk-version",
        version,
        "--package-set",
        package_set,
        "--sdk-source",
        sdk_source,
    ]
    if lf_target:
        command.extend(["--lf-target", lf_target])
    print("Running:", " ".join(command))
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def write_manifest(
    *,
    source_config: dict[str, Any],
    cache_dir: Path,
    manifest_path: Path,
    versions: list[str],
) -> Path:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": source_config.get("source") or "digital-asset/docs local Daml docs cache",
        "publish_version": source_config.get("publish_version"),
        "versions": [
            {
                "version": version,
                "json_path": str((cache_dir / "json" / version / "modules.json").resolve()),
            }
            for version in versions
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote manifest: {manifest_path}")
    return manifest_path


def update_docs_navigation(
    *,
    docs_json_path: Path,
    dropdown_label: str,
    parent_groups: list[str],
    output_dir: Path,
) -> Path:
    docs = load_json(docs_json_path)
    pages = reference_nav.navigation_pages(docs, label=dropdown_label, docs_json_path=docs_json_path)

    page_entries: list[tuple[str, str, Path]] = []
    for page in sorted(output_dir.glob("*.mdx")):
        title = read_mdx_title(page)
        page_entries.append((title, docs_json_page_ref(page, docs_json_path), page))
    page_entries.sort(key=lambda item: (0 if item[2].name == "index.mdx" else 1, item[0].lower(), item[0]))
    page_refs = {page_ref for _title, page_ref, _path in page_entries}

    existing_group_index = find_group_index(find_group_path(pages, parent_groups), GROUP_LABEL)
    pruned_pages = prune_nav_items(pages, page_refs=page_refs, group_labels={GROUP_LABEL})
    pages.clear()
    pages.extend(pruned_pages)
    target_pages = ensure_group_path(pages, parent_groups)
    overview_entry = next(((page_ref, path) for _title, page_ref, path in page_entries if path.name == "index.mdx"), None)
    module_refs = [page_ref for _title, page_ref, path in page_entries if path.name != "index.mdx"]
    group_pages: list[Any] = []
    if overview_entry is not None:
        overview_ref, overview_path = overview_entry
        set_mdx_title(overview_path, "Overview")
        group_pages.append(overview_ref)
    if module_refs:
        group_pages.append({"group": MODULES_GROUP_LABEL, "pages": module_refs})
    group = {
        "group": GROUP_LABEL,
        "pages": group_pages,
    }
    if existing_group_index is None or existing_group_index >= len(target_pages):
        target_pages.append(group)
    else:
        target_pages.insert(existing_group_index, group)

    docs_json_path.write_text(json.dumps(docs, indent=2) + "\n", encoding="utf-8")
    print(f"Updated docs navigation: {docs_json_path}")
    return docs_json_path


def main() -> int:
    ensure_repo_direnv(repo_root=REPO_ROOT, script_path=Path(__file__).resolve(), argv=sys.argv[1:])
    args = parse_args()
    source_config = load_json(Path(args.source_config).resolve())
    min_version = source_config.get("min_version")
    if not isinstance(min_version, str) or not min_version:
        raise ValueError("Source config must define a non-empty `min_version`")
    selected_versions = (
        sorted(
            set(args.version),
            key=lambda value: tuple(int(part) for part in value.split(".")),
        )
        if args.version
        else stable_dpm_versions(min_version=min_version)
    )
    if not selected_versions:
        raise ValueError("No Daml SDK versions selected")

    package_set = str(source_config.get("package_set") or "base")
    sdk_source = str(source_config.get("sdk_source") or "dpm")
    lf_target = source_config.get("lf_target") if isinstance(source_config.get("lf_target"), str) else None
    publish_version = args.publish_version or selected_versions[-1]

    cache_dir = Path(args.cache_dir).resolve()
    for version in selected_versions:
        generate_json_snapshot(
            version=version,
            output_json=cache_dir / "json" / version / "modules.json",
            package_set=package_set,
            sdk_source=sdk_source,
            lf_target=lf_target,
            force_regenerate=args.force_regenerate,
        )

    manifest_path = write_manifest(
        source_config={**source_config, "publish_version": publish_version},
        cache_dir=cache_dir,
        manifest_path=Path(args.manifest_out).resolve(),
        versions=selected_versions,
    )
    command = repo_direnv_command(
        REPO_ROOT,
        "x2mdx",
        "daml-json",
        "build-api-pages-from-manifest",
        "--manifest",
        str(manifest_path),
        "--output-dir",
        str(Path(args.output_dir).resolve()),
        "--publish-version",
        str(publish_version),
        "--source-name",
        args.source_name,
        "--version-filter",
        args.version_filter,
        "--link-prefix",
        docs_route_prefix(Path(args.output_dir).resolve(), Path(args.docs_json).resolve()),
        "--history-report",
        str(Path(args.output_dir).resolve() / "history-report.json"),
        "--reader-route-prefix",
        docs_route_prefix(Path(args.output_dir).resolve(), Path(args.docs_json).resolve()),
        "--surface-id",
        "daml-standard-library",
        "--surface-title",
        GROUP_LABEL,
        "--configured-scope",
        "Daml Standard Library modules from stable DPM SDK releases",
        "--lifecycle-metadata",
        str(Path(args.lifecycle_metadata).resolve()),
    )
    for version in args.version or []:
        command.extend(["--version", version])
    print("Running:", " ".join(command))
    completed = subprocess.run(command, cwd=REPO_ROOT)
    if completed.returncode != 0:
        return completed.returncode

    update_docs_navigation(
        docs_json_path=Path(args.docs_json).resolve(),
        dropdown_label=args.nav_dropdown,
        parent_groups=args.nav_group or [],
        output_dir=Path(args.output_dir).resolve(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
