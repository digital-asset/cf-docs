#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

from docs_env import ensure_repo_direnv, repo_direnv_command
import reference_nav


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "x2mdx"
DEFAULT_SOURCE_CONFIG = REPO_ROOT / "config" / "x2mdx" / "ledger-bindings" / "source-artifacts.json"
DEFAULT_CACHE_DIR = DEFAULT_CACHE_ROOT / "ledger-bindings"
DEFAULT_MANIFEST = REPO_ROOT / ".internal" / "generated" / "x2mdx" / "ledger-bindings" / "manifest.json"
DEFAULT_RENDER_ROOT = REPO_ROOT / ".internal" / "generated" / "x2mdx" / "ledger-bindings" / "site"
DEFAULT_RENDER_HISTORY_REPORT = DEFAULT_RENDER_ROOT / "history-report.json"
DEFAULT_OVERVIEW_FILE = REPO_ROOT / "docs-main" / "reference" / "java-bindings.mdx"
DEFAULT_DETAILS_DIR = REPO_ROOT / "docs-main" / "reference"
DEFAULT_HISTORY_REPORT = REPO_ROOT / "docs-main" / "reference" / "java" / "history-report.json"
DEFAULT_DOCS_JSON = REPO_ROOT / "docs-main" / "docs.json"
LEGACY_JVM_OVERVIEW_FILE = REPO_ROOT / "docs-main" / "reference" / "ledger-api-jvm-bindings.mdx"
LEGACY_OVERVIEW_FILE = REPO_ROOT / "docs-main" / "appdev" / "reference" / "ledger-bindings-api-lifecycle.mdx"
LEGACY_DETAILS_DIR = REPO_ROOT / "docs-main" / "appdev" / "reference" / "ledger-bindings-api-lifecycle"
LANGUAGE_LABELS = {
    "java": "Javadocs",
}
LANGUAGE_ORDER = {
    "java": 0,
}
LANGUAGE_DIRS = {
    "java": "java",
}
REMOVED_LANGUAGE_DIRS = {"scala"}
ARTIFACT_PAGE_DESCRIPTIONS = {
    "java": "Generated package reference and version summary from local Javadoc snapshots",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download configured Java doc sources, write a local x2mdx manifest, and generate the Ledger bindings reference pages."
    )
    parser.add_argument(
        "--source-config",
        default=str(DEFAULT_SOURCE_CONFIG),
        help="Checked-in source config listing artifacts and versions to download.",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="Directory used to cache downloaded Java doc artifacts and generated jars.",
    )
    parser.add_argument(
        "--manifest-out",
        default=str(DEFAULT_MANIFEST),
        help="Path to the generated local x2mdx manifest.",
    )
    parser.add_argument(
        "--overview-file",
        default=str(DEFAULT_OVERVIEW_FILE),
        help="Path to the published hidden overview MDX page.",
    )
    parser.add_argument(
        "--details-dir",
        default=str(DEFAULT_DETAILS_DIR),
        help="Directory for published Java package pages, such as reference/java/....",
    )
    parser.add_argument(
        "--docs-json",
        default=str(DEFAULT_DOCS_JSON),
        help="Path to the Mintlify docs.json file to update.",
    )
    parser.add_argument(
        "--nav-dropdown",
        default="API Reference",
        help="Top-level Mintlify dropdown to update with the generated Java bindings section.",
    )
    parser.add_argument(
        "--nav-group",
        action="append",
        help="Mintlify group path to update. Repeat for nested groups.",
    )
    parser.add_argument(
        "--version",
        action="append",
        help="Artifact version to include. Repeat to limit generation to a subset of configured versions.",
    )
    parser.add_argument(
        "--repo-base",
        help="Override the Maven repository base URL from the checked-in source config.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download source artifacts even if they already exist in the local cache.",
    )
    parser.add_argument(
        "--source-name",
        default="Published Java docs snapshots",
        help="Source label embedded in generated content.",
    )
    parser.add_argument(
        "--overview-title",
        default="Java Bindings",
        help="Title to use for the generated overview page and parent Mintlify nav group.",
    )
    parser.add_argument(
        "--version-filter",
        default="eligible stable Maven releases from the configured lower bound",
        help="Version-filter label embedded in generated content.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def slugify(value: str) -> str:
    out = value.lower()
    out = re.sub(r"[^a-z0-9]+", "-", out)
    return re.sub(r"-{2,}", "-", out).strip("-")


def docs_json_page_ref(path: Path, docs_json_path: Path) -> str:
    relative = path.resolve().relative_to(docs_json_path.resolve().parent)
    if relative.suffix != ".mdx":
        raise ValueError(f"Expected MDX file under docs root, got: {path}")
    page_ref = relative.with_suffix("").as_posix()
    if page_ref.endswith("/index"):
        return page_ref[: -len("/index")]
    return page_ref


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


def read_package_label(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"## Package `(.*)`$", line)
        if match:
            return match.group(1)
    return read_mdx_title(path)


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
        group = next(
            (item for item in current_pages if isinstance(item, dict) and item.get("group") == label),
            None,
        )
        if group is None:
            group = {"group": label, "pages": []}
            current_pages.append(group)
        pages = group.get("pages")
        if not isinstance(pages, list):
            pages = []
            group["pages"] = pages
        current_pages = pages
    return current_pages


def build_jvm_nav_group(
    *,
    publish_root: Path,
    docs_json_path: Path,
    group_label: str,
) -> tuple[dict[str, Any], set[str]]:
    language_pages: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    generated_refs: set[str] = set()

    for language, directory_name in LANGUAGE_DIRS.items():
        language_dir = publish_root / directory_name
        if not language_dir.exists():
            continue
        artifact_index = language_dir / "index.mdx"
        if artifact_index.exists():
            generated_refs.add(docs_json_page_ref(artifact_index, docs_json_path))
        for package_dir in sorted(path for path in language_dir.iterdir() if path.is_dir()):
            package_index = package_dir / "index.mdx"
            if not package_index.exists():
                continue
            package_label = read_package_label(package_index)
            package_pages: list[tuple[str, str]] = []
            generated_refs.add(docs_json_page_ref(package_index, docs_json_path))
            for object_page in sorted(package_dir.glob("*.mdx")):
                if object_page.name == "index.mdx":
                    continue
                page_ref = docs_json_page_ref(object_page, docs_json_path)
                package_pages.append((read_mdx_title(object_page), page_ref))
                generated_refs.add(page_ref)
            package_pages.sort(key=lambda item: (item[0].lower(), item[0]))
            language_pages[language].append(
                (
                    package_label,
                    {
                        "group": package_label,
                        "pages": [page_ref for _, page_ref in package_pages],
                    },
                )
            )

    language_groups: list[tuple[int, str, dict[str, Any]]] = []
    for language, page_entries in language_pages.items():
        if not page_entries:
            continue
        page_entries.sort(key=lambda item: (item[0].lower(), item[0]))
        label = "Packages"
        language_groups.append(
            (
                LANGUAGE_ORDER.get(language, 99),
                label,
                {
                    "group": label,
                    "pages": [group for _, group in page_entries],
                },
            )
        )

    language_groups.sort(key=lambda item: (item[0], item[1]))
    group_pages: list[Any] = [group for _, _, group in language_groups]
    return (
        {
            "group": group_label,
            "pages": group_pages,
        },
        generated_refs,
    )


def update_docs_navigation(
    *,
    docs_json_path: Path,
    dropdown_label: str,
    parent_groups: list[str],
    group_label: str,
    overview_file: Path,
    publish_root: Path,
) -> Path:
    docs = load_json(docs_json_path)
    pages = reference_nav.navigation_pages(docs, label=dropdown_label, docs_json_path=docs_json_path)

    jvm_group, generated_refs = build_jvm_nav_group(
        publish_root=publish_root,
        docs_json_path=docs_json_path,
        group_label=group_label,
    )
    overview_ref = docs_json_page_ref(overview_file, docs_json_path)
    generated_refs.add(overview_ref)
    generated_refs.add(
        docs_json_page_ref(publish_root / "java" / "index.mdx", docs_json_path)
    )
    jvm_pages = jvm_group.setdefault("pages", [])
    if isinstance(jvm_pages, list) and overview_ref not in jvm_pages:
        jvm_pages.insert(0, overview_ref)
    pruned_pages = prune_nav_items(
        pages,
        page_refs=generated_refs,
        group_labels={group_label},
    )
    pages.clear()
    pages.extend(pruned_pages)
    target_pages = ensure_group_path(pages, parent_groups)
    target_pages.append(jvm_group)

    docs_json_path.write_text(json.dumps(docs, indent=2) + "\n", encoding="utf-8")
    print(f"Updated docs navigation: {docs_json_path}")
    return docs_json_path


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


def ensure_java_bindings_redirects(*, docs_json_path: Path) -> None:
    for source in ("/reference/java", "/reference/java/index"):
        ensure_redirect(
            docs_json_path=docs_json_path,
            source=source,
            destination="/reference/java-bindings",
        )


def maven_javadoc_url(repo_base: str, group: str, artifact: str, version: str) -> str:
    group_path = group.replace(".", "/")
    file_name = f"{artifact}-{version}-javadoc.jar"
    return f"{repo_base.rstrip('/')}/{group_path}/{artifact}/{version}/{file_name}"


def semver_key(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"Expected stable semantic version, got: {version}")
    return tuple(int(part) for part in match.groups())


def discover_stable_maven_versions(
    *,
    repo_base: str,
    group: str,
    artifact: str,
    min_version: str,
) -> list[str]:
    group_path = group.replace(".", "/")
    url = f"{repo_base.rstrip('/')}/{group_path}/{artifact}/maven-metadata.xml"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "digital-asset-docs-x2mdx/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            root = ET.fromstring(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while downloading {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error while downloading {url}: {exc}") from exc

    minimum = semver_key(min_version)
    versions = [
        element.text.strip()
        for element in root.findall("./versioning/versions/version")
        if element.text
        and re.fullmatch(r"\d+\.\d+\.\d+", element.text.strip())
        and semver_key(element.text.strip()) >= minimum
    ]
    return sorted(set(versions), key=semver_key)


def download_file(url: str, target: Path, *, force: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        print(f"Using cached artifact: {target}")
        return

    print(f"Downloading: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "digital-asset-docs-x2mdx/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            target.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while downloading {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error while downloading {url}: {exc}") from exc


def resolve_cached_jar(
    *,
    repo_base: str,
    cache_dir: Path,
    group: str,
    artifact: str,
    version: str,
    force_download: bool,
) -> Path:
    jar_target = cache_dir / "jars" / group / artifact / version / f"{artifact}-{version}-javadoc.jar"
    download_file(
        maven_javadoc_url(repo_base, group, artifact, version),
        jar_target,
        force=force_download,
    )
    return jar_target


def build_manifest(
    *,
    source_config: dict[str, Any],
    source_config_path: Path,
    cache_dir: Path,
    manifest_path: Path,
    include_versions: set[str] | None,
    repo_base_override: str | None,
    force_download: bool,
) -> Path:
    repo_base = repo_base_override or str(source_config.get("repo_base") or "https://repo1.maven.org/maven2")
    artifacts_payload: list[dict[str, Any]] = []

    artifacts = source_config.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Source config must contain an `artifacts` list")

    for artifact_entry in artifacts:
        if not isinstance(artifact_entry, dict):
            continue
        group = artifact_entry.get("group")
        artifact = artifact_entry.get("artifact")
        language = artifact_entry.get("language")
        configured_versions = artifact_entry.get("versions")
        min_version = artifact_entry.get("min_version")
        include_prefixes = artifact_entry.get("include_prefixes") or []
        if not isinstance(group, str) or not group:
            continue
        if not isinstance(artifact, str) or not artifact:
            continue
        if language != "java":
            continue
        if isinstance(configured_versions, list):
            versions = configured_versions
        elif isinstance(min_version, str) and min_version:
            versions = discover_stable_maven_versions(
                repo_base=repo_base,
                group=group,
                artifact=artifact,
                min_version=min_version,
            )
        else:
            continue

        version_entries: list[dict[str, str]] = []
        for version in versions:
            if not isinstance(version, str) or not version:
                continue
            if include_versions is not None and version not in include_versions:
                continue

            jar_target = resolve_cached_jar(
                repo_base=repo_base,
                cache_dir=cache_dir,
                group=group,
                artifact=artifact,
                version=version,
                force_download=force_download,
            )
            version_entries.append(
                {
                    "version": version,
                    "jar_path": str(jar_target.resolve()),
                }
            )

        if not version_entries:
            continue

        artifacts_payload.append(
            {
                "group": group,
                "artifact": artifact,
                "language": language,
                "include_prefixes": [prefix for prefix in include_prefixes if isinstance(prefix, str)],
                "versions": version_entries,
            }
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": source_config.get("source") or "digital-asset/docs local Java docs cache",
        "repo_base": repo_base,
        "artifacts": artifacts_payload,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote manifest: {manifest_path}")
    return manifest_path


def build_command(args: argparse.Namespace, manifest_path: Path) -> list[str]:
    render_overview_file = DEFAULT_RENDER_ROOT / "java-bindings.mdx"
    render_details_dir = DEFAULT_RENDER_ROOT / "details"
    command = repo_direnv_command(
        REPO_ROOT,
        "x2mdx",
        "jvm-docs",
        "build-api-pages-from-manifest",
        "--manifest",
        str(manifest_path.resolve()),
        "--overview-file",
        str(render_overview_file.resolve()),
        "--details-dir",
        str(render_details_dir.resolve()),
        "--overview-title",
        args.overview_title,
        "--source-name",
        args.source_name,
        "--version-filter",
        args.version_filter,
        "--history-report",
        str(DEFAULT_RENDER_HISTORY_REPORT.resolve()),
        "--reader-route-prefix",
        "reference/java",
        "--surface-id",
        "java-bindings",
        "--surface-title",
        args.overview_title,
        "--configured-scope",
        "Ledger API Java binding types",
    )
    for version in args.version or []:
        command.extend(["--version", version])
    return command


def render_output_paths() -> tuple[Path, Path]:
    return DEFAULT_RENDER_ROOT / "java-bindings.mdx", DEFAULT_RENDER_ROOT / "details"


def rewrite_markdown_links(text: str, replacements: list[tuple[str, str]]) -> str:
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def split_frontmatter(text: str) -> tuple[list[str], str]:
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0] == "---":
        try:
            closing_index = lines[1:].index("---") + 1
        except ValueError as exc:
            raise ValueError("Unterminated frontmatter in generated page") from exc
        return lines[1:closing_index], "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return [], text


def extract_markdown_sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    intro: list[str] = []
    sections: dict[str, list[str]] = {}
    current_heading: str | None = None

    for line in text.splitlines():
        if line.startswith("## "):
            current_heading = line[3:].strip()
            sections[current_heading] = []
            continue
        if current_heading is None:
            intro.append(line)
            continue
        sections[current_heading].append(line)
    return intro, sections


def trim_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def strip_inline_code(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`"):
        return stripped[1:-1]
    return stripped


def strip_markdown_link_label(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^\[(.+)\]\([^)]+\)$", stripped)
    if match:
        return match.group(1).strip()
    return stripped


def remove_overview_backlinks(text: str) -> str:
    filtered_lines = [
        line
        for line in text.splitlines()
        if line.strip() not in {
            "Back to [overview](../ledger-api-jvm-bindings).",
            "Back to [overview](../java-bindings).",
        }
    ]
    return "\n".join(filtered_lines).strip() + "\n"


def remove_inline_code_links(text: str) -> str:
    return re.sub(r"\[(`[^`]+`)\]\([^)]+\)", r"\1", text)


def rewrite_java_only_generated_text(text: str) -> str:
    replacements = {
        "Javadoc/Scaladoc artifacts": "Javadoc artifacts",
        "Javadoc/Scaladoc jars": "Javadoc jars",
        "Javadoc/Scaladoc snapshots": "Javadoc snapshots",
        "local Java docs snapshots": "local Javadoc snapshots",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    filtered_lines = [
        line
        for line in text.splitlines()
        if line.strip() != "- Scala deprecation is not inferred from Scaladoc indexes in this initial implementation."
    ]
    return "\n".join(filtered_lines).rstrip() + "\n"


def parse_markdown_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    table_lines = [line.strip() for line in lines if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return [], []

    def split_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    headers = split_row(table_lines[0])
    rows = [split_row(line) for line in table_lines[2:]]
    rows = [row for row in rows if len(row) == len(headers)]
    return headers, rows


def markdown_link_target(cell: str) -> str | None:
    match = re.search(r"\(([^)]+)\)", cell)
    if not match:
        return None
    return match.group(1).strip()


def parse_int_cell(cell: str) -> int:
    stripped = strip_inline_code(cell)
    if not stripped or stripped == "-":
        return 0
    return int(stripped)


def build_package_status_cell(*, versions: list[str], introduced: int, deprecated: int, removed: int) -> str:
    if not versions:
        return "-"
    parts = [f"🟢 `{versions[0]}`"]
    if introduced or deprecated or removed:
        change_version = versions[-1]
        if change_version:
            parts.append(f"🔵 `{change_version}`")
    return " ".join(parts)


def build_package_summary(*, type_count: int, introduced: int, deprecated: int, removed: int) -> str:
    type_label = "type" if type_count == 1 else "types"
    changes: list[str] = []
    if introduced:
        changes.append(f"{introduced} introduced")
    if deprecated:
        changes.append(f"{deprecated} deprecated")
    if removed:
        changes.append(f"{removed} removed")
    if changes:
        return f"{type_count} {type_label}. Changes in range: {', '.join(changes)}."
    return f"{type_count} {type_label}. No lifecycle changes in selected range."


def extract_versions_from_artifact_lines(lines: list[str]) -> list[str]:
    for line in lines:
        match = re.match(r"^- Versions:\s+`([^`]+)`\s*$", line.strip())
        if not match:
            continue
        return [part.strip() for part in match.group(1).split(",") if part.strip()]
    return []


def render_artifact_toc_rows(package_reference_lines: list[str], *, versions: list[str]) -> list[str]:
    _, rows = parse_markdown_table(package_reference_lines)
    output_rows: list[str] = []
    for row in rows:
        if len(row) != 6:
            continue
        _, package_cell, types_cell, introduced_cell, deprecated_cell, removed_cell = row
        package_name = strip_inline_code(strip_markdown_link_label(package_cell))
        type_count = parse_int_cell(types_cell)
        introduced = parse_int_cell(introduced_cell)
        deprecated = parse_int_cell(deprecated_cell)
        removed = parse_int_cell(removed_cell)

        name_cell = f"`{package_name}`"
        status_cell = build_package_status_cell(
            versions=versions,
            introduced=introduced,
            deprecated=deprecated,
            removed=removed,
        )
        summary_cell = build_package_summary(
            type_count=type_count,
            introduced=introduced,
            deprecated=deprecated,
            removed=removed,
        )
        output_rows.append(f"| {name_cell} | {status_cell} | {summary_cell} |")
    return output_rows


def rewrite_artifact_page_layout(text: str, *, artifact_entry: dict[str, Any]) -> str:
    language = str(artifact_entry.get("language", ""))
    title = LANGUAGE_LABELS.get(language, language.title())
    description = ARTIFACT_PAGE_DESCRIPTIONS.get(
        language,
        "Generated package reference and version summary from local Java docs snapshots",
    )
    _, body = split_frontmatter(text)
    normalized_frontmatter = "\n".join(
        [
            "---",
            f'title: "{title}"',
            f'description: "{description}"',
            "---",
            "",
        ]
    )
    normalized_body = body.lstrip("\n")
    intro_lines, sections = extract_markdown_sections(normalized_body)

    # Current x2mdx Java artifact pages already have the desired section layout.
    # Keep that body intact and only normalize the published title/description.
    if {
        "Table of Contents",
        "Version Change Summary",
        "Reference",
    }.issubset(sections.keys()):
        return normalized_frontmatter + remove_inline_code_links(remove_overview_backlinks(normalized_body))

    # Older x2mdx revisions emitted different section names. Preserve compatibility
    # with those raw pages by reshaping them into the current published layout.
    toc_lines = trim_blank_lines(sections.get("Package Reference", []))
    artifact_lines = trim_blank_lines(sections.get("Artifact", []))
    lifecycle_lines = trim_blank_lines(sections.get("Lifecycle Summary", []))
    changed_lines = trim_blank_lines(sections.get("Changed Symbols", []))
    deprecation_lines = trim_blank_lines(sections.get("Deprecation Notes", []))
    failure_lines = trim_blank_lines(sections.get("Input Failures", []))

    output_lines = [
        normalized_frontmatter.rstrip(),
    ]

    intro_lines = [
        line
        for line in trim_blank_lines(intro_lines)
        if line.strip()
        not in {
            "Back to [overview](../ledger-api-jvm-bindings).",
            "Back to [overview](../java-bindings).",
        }
    ]
    if intro_lines:
        output_lines.extend(intro_lines)
    output_lines.extend(["", "## Table of Contents", ""])

    toc_versions = extract_versions_from_artifact_lines(artifact_lines)
    if toc_lines:
        output_lines.append("🟢 Active Since  🔵 Changed  🔴 Removed")
        output_lines.extend(
            [
                "",
                "| NAME | STATUS | SUMMARY |",
                "| --- | --- | --- |",
                *render_artifact_toc_rows(toc_lines, versions=toc_versions),
            ]
        )
    else:
        output_lines.append("No package-level symbols were found for this artifact.")

    version_summary_lines = artifact_lines + lifecycle_lines
    output_lines.extend(["", "## Version Change Summary", ""])
    if version_summary_lines:
        output_lines.extend(version_summary_lines)
    else:
        output_lines.append("No lifecycle metadata was generated for this artifact.")

    output_lines.extend(["", "## Reference", "", "### Changed Symbols", ""])
    if changed_lines:
        output_lines.extend(changed_lines)
    else:
        output_lines.append("No lifecycle changes were detected in the configured version range.")

    if deprecation_lines:
        output_lines.extend(["", "### Deprecation Notes", ""])
        output_lines.extend(deprecation_lines)

    if failure_lines:
        output_lines.extend(["", "### Input Failures", ""])
        output_lines.extend(failure_lines)

    return "\n".join(output_lines).rstrip() + "\n"


def major_minor_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) < 2:
        raise ValueError(f"Expected semantic version with major.minor components, got: {version}")
    return ".".join(parts[:2])


def rewrite_upstream_docs_links(text: str, artifact_entry: dict[str, Any]) -> str:
    template = artifact_entry.get("upstream_docs_base_url_template")
    group = artifact_entry.get("group")
    artifact = artifact_entry.get("artifact")
    versions = artifact_entry.get("versions")
    if not isinstance(template, str) or not template:
        return text
    if not isinstance(group, str) or not isinstance(artifact, str):
        return text
    if not isinstance(versions, list):
        return text

    for version in versions:
        if not isinstance(version, str) or not version:
            continue
        old_prefix = f"https://javadoc.io/doc/{group}/{artifact}/{version}/"
        new_prefix = template.format(version=version, major_minor=major_minor_version(version)).rstrip("/") + "/"
        text = text.replace(old_prefix, new_prefix)
    return text


def copy_rewritten_page(
    source: Path,
    target: Path,
    replacements: list[tuple[str, str]],
    *,
    artifact_entry: dict[str, Any] | None = None,
    rewrite_artifact_layout: bool = False,
    rewrite_java_only_text: bool = False,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8")
    text = rewrite_markdown_links(text, replacements)
    if artifact_entry is not None:
        text = rewrite_upstream_docs_links(text, artifact_entry)
        if rewrite_artifact_layout:
            text = rewrite_artifact_page_layout(text, artifact_entry=artifact_entry)
        if artifact_entry.get("language") == "java":
            rewrite_java_only_text = True
    if rewrite_java_only_text:
        text = rewrite_java_only_generated_text(text)
    target.write_text(text, encoding="utf-8")


def publish_rendered_pages(
    *,
    source_config: dict[str, Any],
    publish_overview_file: Path,
    publish_root: Path,
) -> tuple[Path, set[str]]:
    _, render_details_dir = render_output_paths()
    publish_root.mkdir(parents=True, exist_ok=True)

    artifact_entries = [
        entry
        for entry in source_config.get("artifacts", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("artifact"), str)
        and isinstance(entry.get("language"), str)
        and entry.get("language") in LANGUAGE_DIRS
    ]
    if len(artifact_entries) != 1:
        raise ValueError(
            "The Java bindings reader surface requires exactly one configured artifact"
        )

    preserved_language_dirs = {
        LANGUAGE_DIRS[str(entry["language"])]
        for entry in artifact_entries
        if bool(entry.get("preserve_existing_output"))
    }
    for language_dir in LANGUAGE_DIRS.values():
        if language_dir in preserved_language_dirs:
            continue
        shutil.rmtree(publish_root / language_dir, ignore_errors=True)
    for language_dir in REMOVED_LANGUAGE_DIRS:
        shutil.rmtree(publish_root / language_dir, ignore_errors=True)
    if publish_overview_file.exists():
        publish_overview_file.unlink()
    if LEGACY_JVM_OVERVIEW_FILE.exists():
        LEGACY_JVM_OVERVIEW_FILE.unlink()
    if LEGACY_OVERVIEW_FILE.exists():
        LEGACY_OVERVIEW_FILE.unlink()
    shutil.rmtree(LEGACY_DETAILS_DIR, ignore_errors=True)

    generated_refs: set[str] = set()

    for artifact_entry in artifact_entries:
        artifact = str(artifact_entry["artifact"])
        language = str(artifact_entry["language"])
        preserve_existing_output = bool(artifact_entry.get("preserve_existing_output"))
        language_dir = publish_root / LANGUAGE_DIRS[language]
        artifact_slug = slugify(artifact)
        source_artifact_page = render_details_dir / f"{artifact_slug}.mdx"
        source_package_dir = render_details_dir / f"{artifact_slug}-packages"
        target_artifact_page = publish_overview_file
        copy_rewritten_page(
            source_artifact_page,
            target_artifact_page,
            replacements=[
                (
                    f"./{source_package_dir.name}/",
                    f"./{LANGUAGE_DIRS[language]}/",
                )
            ],
            artifact_entry=artifact_entry,
        )
        generated_refs.add(docs_json_page_ref(target_artifact_page, DEFAULT_DOCS_JSON))

        if preserve_existing_output:
            continue

        if not source_package_dir.exists():
            continue
        language_dir.mkdir(parents=True, exist_ok=True)
        for package_dir in sorted(path for path in source_package_dir.iterdir() if path.is_dir()):
            source_package_page = package_dir / "index.mdx"
            if not source_package_page.exists():
                continue
            target_package_dir = language_dir / package_dir.name
            target_package_page = target_package_dir / "index.mdx"
            copy_rewritten_page(
                source_package_page,
                target_package_page,
                replacements=[
                    (
                        f"../../{artifact_slug}",
                        "../../java-bindings",
                    )
                ],
                artifact_entry=artifact_entry,
            )
            generated_refs.add(docs_json_page_ref(target_package_page, DEFAULT_DOCS_JSON))
            for object_page in sorted(package_dir.glob("*.mdx")):
                if object_page.name == "index.mdx":
                    continue
                target_object_page = target_package_dir / object_page.name
                copy_rewritten_page(
                    object_page,
                    target_object_page,
                    replacements=[],
                    artifact_entry=artifact_entry,
                )
                generated_refs.add(docs_json_page_ref(target_object_page, DEFAULT_DOCS_JSON))

    history_report_target = DEFAULT_HISTORY_REPORT
    history_report_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_RENDER_HISTORY_REPORT, history_report_target)
    return publish_overview_file, generated_refs


def main() -> int:
    ensure_repo_direnv(repo_root=REPO_ROOT, script_path=Path(__file__).resolve(), argv=sys.argv[1:])
    args = parse_args()
    source_config_path = Path(args.source_config).resolve()
    source_config = load_json(source_config_path)
    include_versions = set(args.version) if args.version else None
    manifest_path = build_manifest(
        source_config=source_config,
        source_config_path=source_config_path,
        cache_dir=Path(args.cache_dir).resolve(),
        manifest_path=Path(args.manifest_out).resolve(),
        include_versions=include_versions,
        repo_base_override=args.repo_base,
        force_download=args.force_download,
    )
    command = build_command(args, manifest_path)
    print("Running:", " ".join(command))
    completed = subprocess.run(command, cwd=REPO_ROOT)
    if completed.returncode != 0:
        return completed.returncode

    publish_overview_file, _ = publish_rendered_pages(
        source_config=source_config,
        publish_overview_file=Path(args.overview_file).resolve(),
        publish_root=Path(args.details_dir).resolve(),
    )
    update_docs_navigation(
        docs_json_path=Path(args.docs_json).resolve(),
        dropdown_label=args.nav_dropdown,
        parent_groups=args.nav_group or [],
        group_label=args.overview_title,
        overview_file=publish_overview_file.resolve(),
        publish_root=Path(args.details_dir).resolve(),
    )
    reference_nav.regroup_ledger_api_nav(
        docs_json_path=Path(args.docs_json).resolve(),
        dropdown_label=args.nav_dropdown,
    )
    ensure_java_bindings_redirects(
        docs_json_path=Path(args.docs_json).resolve(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
