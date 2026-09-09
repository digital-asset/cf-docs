#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from generated_reference_sources import canton_release_bundles

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CONFIG = REPO_ROOT / "config" / "x2mdx" / "ledger-api" / "source-artifacts.json"
DEFAULT_CANTON_REPO_DIR = canton_release_bundles.DEFAULT_REPO_DIR
DEFAULT_CANTON_REMOTE = canton_release_bundles.DEFAULT_CANTON_REMOTE
DOCS_SOURCE = REPO_ROOT / "docs-source"
DEFAULT_RELEASE_INDEX = DOCS_SOURCE / "global-synchronizer" / "release-notes" / "canton.mdx"
DEFAULT_RELEASE_DIR = DOCS_SOURCE / "global-synchronizer" / "release-notes" / "canton-releases"
DEFAULT_LEGACY_RELEASE_PAGE = DOCS_SOURCE / "global-synchronizer" / "release-notes" / "canton" / "index.mdx"
DEFAULT_LEGACY_RELEASE_DIR = DOCS_SOURCE / "global-synchronizer" / "release-notes" / "canton"
DEFAULT_DOCS_JSON = DOCS_SOURCE / "docs.json"
USER_AGENT = "cf-docs-canton-release-note-updater"
DOWNLOAD_TIMEOUT_SECONDS = 300.0
RELEASE_HEADING_RE = re.compile(r"^# Release of Canton (?P<version>\d+\.\d+\.\d+)\s*$", re.MULTILINE)
CANTON_RELEASE_LINK_RE = re.compile(
    r"/global-synchronizer/release-notes/(?:canton|canton-releases)/(?P<slug>\d+-\d+-\d+)"
)
ANGLE_PLACEHOLDER_RE = re.compile(r"<([^>\n]+)>")
CANTON_PAGE_REF = "global-synchronizer/release-notes/canton"
CANTON_RELEASE_PAGE_ROOT = "global-synchronizer/release-notes/canton-releases"
CANTON_PAGE_TITLE = "Canton"
CANTON_PAGE_DESCRIPTION = "Release notes for Canton."


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> Version:
        parts = value.split(".")
        if len(parts) != 3:
            raise ValueError(f"Expected MAJOR.MINOR.PATCH version, got {value!r}")
        return cls(*(int(part) for part in parts))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class ReleaseNoteSource:
    version: Version
    url: str
    archive_path: str


@dataclass(frozen=True)
class PageUpdate:
    previous_versions: tuple[str, ...]
    current_versions: tuple[str, ...]
    source_paths: tuple[str, ...]
    changed: bool


def release_note_sources(
    *,
    source_config_path: Path,
    canton_repo_dir: Path,
    canton_remote: str,
    version_prefix: str | None,
) -> tuple[ReleaseNoteSource, ...]:
    source_config = canton_release_bundles.parse_source_config(source_config_path)
    selected_prefix = version_prefix or source_config.publish_version
    versions = canton_release_bundles.public_canton_bundle_versions(
        source_config,
        docs_version=selected_prefix,
        repo_dir=canton_repo_dir,
        remote=canton_remote,
    )
    if not versions:
        raise RuntimeError(f"No public Canton release bundles found for release line {selected_prefix}")
    return tuple(
        ReleaseNoteSource(
            version=Version.parse(version),
            url=canton_release_bundles.release_url(source_config, canton_version=version),
            archive_path=f"canton-open-source-{version}/RELEASE-NOTES.md",
        )
        for version in versions
    )


def fetch_release_note_markdown(
    *,
    source: ReleaseNoteSource,
) -> str:
    request = urllib.request.Request(
        source.url,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with tarfile.open(fileobj=response, mode="r|gz") as archive:
                for member in archive:
                    if member.name != source.archive_path:
                        continue
                    release_notes = archive.extractfile(member)
                    if release_notes is None:
                        break
                    return release_notes.read().decode("utf-8")
    except (urllib.error.URLError, tarfile.TarError, UnicodeDecodeError) as error:
        raise RuntimeError(f"Could not read {source.archive_path} from {source.url}: {error}") from error
    raise RuntimeError(f"Could not find {source.archive_path} in {source.url}")


def current_release_version(page_text: str) -> str:
    match = RELEASE_HEADING_RE.search(page_text)
    if match is None:
        raise ValueError("Could not find '# Release of Canton X.Y.Z' heading in release-note page")
    return match.group("version")


def release_versions(text: str) -> tuple[str, ...]:
    versions = [match.group("version") for match in RELEASE_HEADING_RE.finditer(text)]
    versions.extend(match.group("slug").replace("-", ".") for match in CANTON_RELEASE_LINK_RE.finditer(text))
    return tuple(dict.fromkeys(versions))


def normalized_release_markdown(markdown: str) -> str:
    body = markdown.strip()
    current_release_version(body)
    return escape_mdx_angle_placeholders(body)


def escape_mdx_angle_placeholders(markdown: str) -> str:
    escaped_lines: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            escaped_lines.append(line)
            continue
        if in_fence:
            escaped_lines.append(line)
            continue
        parts = line.split("`")
        for index in range(0, len(parts), 2):
            parts[index] = ANGLE_PLACEHOLDER_RE.sub(r"&lt;\1&gt;", parts[index])
            parts[index] = parts[index].replace("-->", "--&gt;")
        escaped_lines.append("`".join(parts))
    return "\n".join(escaped_lines)


def rewrite_release_page(page_text: str, release_markdown: str) -> str:
    frontmatter_end = page_text.find("---", 3)
    if not page_text.startswith("---\n") or frontmatter_end == -1:
        raise ValueError("Expected release-note index page to start with YAML frontmatter")
    return f"{page_text[: frontmatter_end + 3].rstrip()}\n\n{normalized_release_markdown(release_markdown)}\n"


def release_slug(version: Version) -> str:
    return str(version).replace(".", "-")


def release_page_ref(source: ReleaseNoteSource) -> str:
    return f"{CANTON_RELEASE_PAGE_ROOT}/{release_slug(source.version)}"


def release_page_path(release_dir: Path, source: ReleaseNoteSource) -> Path:
    return release_dir / f"{release_slug(source.version)}.mdx"


def release_page_frontmatter(source: ReleaseNoteSource) -> str:
    version = str(source.version)
    return (
        "---\n"
        f'title: "{version}"\n'
        f'description: "Canton {version} release notes."\n'
        "---\n\n"
    )


def release_index_markdown(sources: Sequence[ReleaseNoteSource]) -> str:
    newest_first = tuple(reversed(sources))
    lines = [
        "---",
        f'title: "{CANTON_PAGE_TITLE}"',
        f'description: "{CANTON_PAGE_DESCRIPTION}"',
        "---",
        "",
        "{/* Generated from the RELEASE-NOTES.md files in public Canton release bundles. */}",
        "",
        "Canton release notes are reproduced below from the published Canton release bundles.",
        "",
        "## Releases",
        "",
    ]
    for source in newest_first:
        version = str(source.version)
        lines.append(
            f"- [Canton {version}](/global-synchronizer/release-notes/canton-releases/{release_slug(source.version)})"
        )
    return "\n".join(lines) + "\n"


def read_source_markdown(
    *,
    sources: Sequence[ReleaseNoteSource],
) -> dict[ReleaseNoteSource, str]:
    return {source: fetch_release_note_markdown(source=source) for source in sources}


def write_release_pages(
    *,
    release_index: Path,
    release_dir: Path,
    legacy_release_page: Path,
    legacy_release_dir: Path,
    sources: Sequence[ReleaseNoteSource],
    source_markdown: dict[ReleaseNoteSource, str],
    dry_run: bool,
) -> bool:
    desired_files: dict[Path, str] = {
        release_index: release_index_markdown(sources),
    }
    for source in sources:
        path = release_page_path(release_dir, source)
        if source in source_markdown:
            desired_files[path] = (
                release_page_frontmatter(source) + normalized_release_markdown(source_markdown[source]) + "\n"
            )

    existing_release_files = set(release_dir.glob("*.mdx")) if release_dir.exists() else set()
    selected_release_files = {release_page_path(release_dir, source) for source in sources}
    stale_files = existing_release_files - selected_release_files
    if legacy_release_page != release_index and legacy_release_page.exists():
        stale_files.add(legacy_release_page)
    if legacy_release_dir != release_dir and legacy_release_dir.exists():
        stale_files.update(legacy_release_dir.glob("*.mdx"))
    changed = any(path.read_text(encoding="utf-8") != text if path.exists() else True for path, text in desired_files.items())
    changed = changed or bool(stale_files) or any(not path.exists() for path in selected_release_files)

    if dry_run or not changed:
        return changed

    release_dir.mkdir(parents=True, exist_ok=True)
    for path, text in desired_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for path in stale_files:
        path.unlink()
    return True


def replace_canton_nav_entry(pages: list[object], page_refs: Sequence[str]) -> None:
    canton_group = {"group": "Canton", "pages": list(page_refs)}
    insert_at: int | None = None
    filtered: list[object] = []
    for index, item in enumerate(pages):
        if isinstance(item, str) and item == CANTON_PAGE_REF:
            insert_at = len(filtered)
            continue
        if isinstance(item, str) and (
            item.startswith(f"{CANTON_PAGE_REF}/") or item.startswith(f"{CANTON_RELEASE_PAGE_ROOT}/")
        ):
            insert_at = len(filtered)
            continue
        if isinstance(item, dict) and item.get("group") == "Canton":
            insert_at = len(filtered)
            continue
        filtered.append(item)
        if item == "global-synchronizer/release-notes/splice":
            insert_at = len(filtered)
    if insert_at is None:
        insert_at = len(filtered)
    filtered.insert(insert_at, canton_group)
    pages[:] = filtered


def update_docs_json(*, docs_json: Path, page_refs: Sequence[str], dry_run: bool) -> bool:
    docs = json.loads(docs_json.read_text(encoding="utf-8"))
    products = docs.get("navigation", {}).get("products")
    if not isinstance(products, list):
        raise ValueError(f"{docs_json} must define navigation.products")

    for product in products:
        if not isinstance(product, dict):
            continue
        if product.get("product") == "Global Synchronizer":
            groups = product.get("groups")
            if isinstance(groups, list):
                for group in groups:
                    if isinstance(group, dict) and group.get("group") == "Release Notes":
                        pages = group.get("pages")
                        if isinstance(pages, list):
                            replace_canton_nav_entry(pages, page_refs)
        if product.get("product") == "Release Notes":
            pages = product.get("pages")
            if isinstance(pages, list):
                for group in pages:
                    if isinstance(group, dict) and group.get("group") == "Canton Network":
                        group_pages = group.get("pages")
                        if isinstance(group_pages, list):
                            replace_canton_nav_entry(group_pages, page_refs)

    updated = json.dumps(docs, indent=2) + "\n"
    before = docs_json.read_text(encoding="utf-8")
    if before == updated:
        return False
    if not dry_run:
        docs_json.write_text(updated, encoding="utf-8")
    return True


def update_release_page(
    *,
    release_index: Path,
    release_dir: Path,
    legacy_release_page: Path,
    legacy_release_dir: Path,
    docs_json: Path,
    source_config_path: Path,
    canton_repo_dir: Path,
    canton_remote: str,
    version_prefix: str | None,
    dry_run: bool,
) -> PageUpdate:
    previous_page = release_index if release_index.exists() else legacy_release_page
    previous_text = previous_page.read_text(encoding="utf-8") if previous_page.exists() else ""
    previous_versions = release_versions(previous_text)
    sources = release_note_sources(
        source_config_path=source_config_path,
        canton_repo_dir=canton_repo_dir,
        canton_remote=canton_remote,
        version_prefix=version_prefix,
    )
    missing_sources = tuple(source for source in sources if not release_page_path(release_dir, source).exists())
    source_markdown = {} if dry_run else read_source_markdown(sources=missing_sources)
    changed_pages = write_release_pages(
        release_index=release_index,
        release_dir=release_dir,
        legacy_release_page=legacy_release_page,
        legacy_release_dir=legacy_release_dir,
        sources=sources,
        source_markdown=source_markdown,
        dry_run=dry_run,
    )
    page_refs = [CANTON_PAGE_REF, *(release_page_ref(source) for source in reversed(sources))]
    changed_nav = update_docs_json(docs_json=docs_json, page_refs=page_refs, dry_run=dry_run)
    return PageUpdate(
        previous_versions=previous_versions,
        current_versions=tuple(str(source.version) for source in sources),
        source_paths=tuple(f"{source.url}#{source.archive_path}" for source in sources),
        changed=changed_pages or changed_nav,
    )


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the published Canton release-note pages from release bundles.")
    parser.add_argument("--source-config", type=Path, default=DEFAULT_SOURCE_CONFIG)
    parser.add_argument("--canton-repo-dir", type=Path, default=DEFAULT_CANTON_REPO_DIR)
    parser.add_argument("--canton-remote", default=DEFAULT_CANTON_REMOTE)
    parser.add_argument(
        "--version-prefix",
        help="Restrict source selection to a release line such as 3.5. Defaults to the configured publish line.",
    )
    parser.add_argument("--release-index", type=Path, default=DEFAULT_RELEASE_INDEX)
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE_DIR)
    parser.add_argument("--legacy-release-page", type=Path, default=DEFAULT_LEGACY_RELEASE_PAGE)
    parser.add_argument("--legacy-release-dir", type=Path, default=DEFAULT_LEGACY_RELEASE_DIR)
    parser.add_argument("--docs-json", type=Path, default=DEFAULT_DOCS_JSON)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(tuple(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    update = update_release_page(
        release_index=args.release_index,
        release_dir=args.release_dir,
        legacy_release_page=args.legacy_release_page,
        legacy_release_dir=args.legacy_release_dir,
        docs_json=args.docs_json,
        source_config_path=args.source_config,
        canton_repo_dir=args.canton_repo_dir,
        canton_remote=args.canton_remote,
        version_prefix=args.version_prefix,
        dry_run=args.dry_run,
    )
    action = "Would update" if args.dry_run and update.changed else "Updated" if update.changed else "Already current"
    previous = ", ".join(update.previous_versions) or "none"
    current = ", ".join(update.current_versions)
    print(
        f"{action} Canton release notes: "
        f"{previous} -> {current} "
        "from public Canton release bundles"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
