#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import posixpath
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import github_api_utils

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_SOURCE = REPO_ROOT / "docs-source"
GITHUB_API = "https://api.github.com"
USER_AGENT = "cf-docs-release-note-updater"
PACKAGE_HEADING_RE = re.compile(
    r"^## (?P<version>\d+\.\d+\.\d+) \((?P<date>\d{4}-\d{2}-\d{2})\)[ \t]*$",
    re.MULTILINE,
)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ANGLE_PLACEHOLDER_RE = re.compile(r"<([^>\n]+)>")


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
class GithubContent:
    text: str
    sha: str
    html_url: str


@dataclass(frozen=True)
class PackageRelease:
    version: Version
    tag_name: str
    published_at: str
    body: str
    html_url: str


@dataclass(frozen=True)
class PackageReleaseTarget:
    key: str
    title: str
    package_name: str
    description: str
    index_path: Path
    release_dir: Path
    page_ref: str
    release_page_root: str


@dataclass(frozen=True)
class ReleaseNoteSection:
    version: Version
    title: str
    body: str
    source_id: str
    source_url: str


@dataclass(frozen=True)
class ReleaseNoteTarget:
    key: str
    title: str
    description: str
    source_description: str
    source_url: str
    index_path: Path
    release_dir: Path
    page_ref: str
    release_page_root: str


PACKAGE_RELEASE_REPO = "canton-network/wallet"
WALLET_SDK_REPO = "canton-network/wallet"
WALLET_SDK_SOURCE_PATH = "docs/wallet-integration-guide/src/release-notes/index.rst"
DEFAULT_DOCS_JSON = DOCS_SOURCE / "docs.json"
RELEASE_NOTES_ROOT = "integrations/release-notes"
WALLET_RELEASE_TARGETS = {
    "wallet-gateway": ReleaseNoteTarget(
        key="wallet-gateway",
        title="Wallet Gateway",
        description="Release notes for the Canton Network Wallet Gateway",
        source_description="`@canton-network/wallet-gateway-remote` GitHub releases",
        source_url=f"https://github.com/{PACKAGE_RELEASE_REPO}/releases?q=wallet-gateway-remote",
        index_path=DOCS_SOURCE / "integrations" / "release-notes" / "wallet-gateway.mdx",
        release_dir=DOCS_SOURCE / "integrations" / "release-notes" / "wallet-gateway-releases",
        page_ref=f"{RELEASE_NOTES_ROOT}/wallet-gateway",
        release_page_root=f"{RELEASE_NOTES_ROOT}/wallet-gateway-releases",
    ),
    "wallet-sdk": ReleaseNoteTarget(
        key="wallet-sdk",
        title="Wallet SDK",
        description="Release notes for the Canton Network Wallet SDK",
        source_description=f"`{WALLET_SDK_SOURCE_PATH}` in `canton-network/wallet`",
        source_url=f"https://github.com/{WALLET_SDK_REPO}/blob/main/{WALLET_SDK_SOURCE_PATH}",
        index_path=DOCS_SOURCE / "integrations" / "release-notes" / "wallet-sdk.mdx",
        release_dir=DOCS_SOURCE / "integrations" / "release-notes" / "wallet-sdk-releases",
        page_ref=f"{RELEASE_NOTES_ROOT}/wallet-sdk",
        release_page_root=f"{RELEASE_NOTES_ROOT}/wallet-sdk-releases",
    ),
    "dapp-sdk": ReleaseNoteTarget(
        key="dapp-sdk",
        title="dApp SDK",
        description="Release notes for the Canton Network dApp SDK",
        source_description="`@canton-network/dapp-sdk` GitHub releases",
        source_url=f"https://github.com/{PACKAGE_RELEASE_REPO}/releases?q=dapp-sdk",
        index_path=DOCS_SOURCE / "integrations" / "release-notes" / "dapp-sdk.mdx",
        release_dir=DOCS_SOURCE / "integrations" / "release-notes" / "dapp-sdk-releases",
        page_ref=f"{RELEASE_NOTES_ROOT}/dapp-sdk",
        release_page_root=f"{RELEASE_NOTES_ROOT}/dapp-sdk-releases",
    ),
}
PACKAGE_RELEASE_TARGETS = {
    "wallet-gateway": PackageReleaseTarget(
        key="wallet-gateway",
        title="Wallet Gateway",
        package_name="@canton-network/wallet-gateway-remote",
        description="Release notes for the Canton Network Wallet Gateway",
        index_path=WALLET_RELEASE_TARGETS["wallet-gateway"].index_path,
        release_dir=WALLET_RELEASE_TARGETS["wallet-gateway"].release_dir,
        page_ref=WALLET_RELEASE_TARGETS["wallet-gateway"].page_ref,
        release_page_root=WALLET_RELEASE_TARGETS["wallet-gateway"].release_page_root,
    ),
    "dapp-sdk": PackageReleaseTarget(
        key="dapp-sdk",
        title="dApp SDK",
        package_name="@canton-network/dapp-sdk",
        description="Release notes for the Canton Network dApp SDK",
        index_path=WALLET_RELEASE_TARGETS["dapp-sdk"].index_path,
        release_dir=WALLET_RELEASE_TARGETS["dapp-sdk"].release_dir,
        page_ref=WALLET_RELEASE_TARGETS["dapp-sdk"].page_ref,
        release_page_root=WALLET_RELEASE_TARGETS["dapp-sdk"].release_page_root,
    ),
}


def github_request_json(url: str) -> Any:
    return github_api_utils.request_json(url, user_agent=USER_AGENT)


def github_api_url(source_repo: str, path: str, *, query: str = "") -> str:
    suffix = f"?{query}" if query else ""
    return f"{GITHUB_API}/repos/{source_repo}/{path}{suffix}"


def github_content(source_repo: str, source_path: str, *, source_ref: str) -> GithubContent:
    payload = github_request_json(
        github_api_url(
            source_repo,
            f"contents/{source_path}",
            query=urllib.parse.urlencode({"ref": source_ref}),
        )
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected file payload for {source_repo}@{source_ref}:{source_path}")
    content = payload.get("content")
    encoding = payload.get("encoding")
    sha = payload.get("sha")
    html_url = payload.get("html_url")
    if not isinstance(content, str) or encoding != "base64":
        raise RuntimeError(f"Expected base64 file content for {source_repo}@{source_ref}:{source_path}")
    if not isinstance(sha, str) or not isinstance(html_url, str):
        raise RuntimeError(f"Expected source metadata for {source_repo}@{source_ref}:{source_path}")
    return GithubContent(text=base64.b64decode(content).decode("utf-8"), sha=sha, html_url=html_url)


def github_releases(source_repo: str) -> Iterable[dict[str, Any]]:
    for page in range(1, 21):
        payload = github_request_json(
            github_api_url(source_repo, "releases", query=urllib.parse.urlencode({"per_page": 100, "page": page}))
        )
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected release list from {source_repo}")
        if not payload:
            break
        for item in payload:
            if isinstance(item, dict):
                yield item


def package_releases(package_name: str) -> tuple[PackageRelease, ...]:
    prefix = f"{package_name}@"
    releases: list[PackageRelease] = []
    for release in github_releases(PACKAGE_RELEASE_REPO):
        tag_name = release.get("tag_name")
        body = release.get("body")
        published_at = release.get("published_at")
        html_url = release.get("html_url")
        if not isinstance(tag_name, str) or not tag_name.startswith(prefix):
            continue
        if release.get("draft") or release.get("prerelease"):
            continue
        if not isinstance(body, str) or not isinstance(published_at, str) or not isinstance(html_url, str):
            continue
        releases.append(
            PackageRelease(
                version=Version.parse(tag_name.removeprefix(prefix)),
                tag_name=tag_name,
                published_at=published_at,
                body=body,
                html_url=html_url,
            )
        )
    if not releases:
        raise RuntimeError(f"No releases found for {package_name} in {PACKAGE_RELEASE_REPO}")
    return tuple(sorted(releases, key=lambda release: release.version, reverse=True))


def escape_mdx(markdown: str) -> str:
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
    return "\n".join(escaped_lines).strip()


def normalize_package_release_body(body: str) -> str:
    markdown = PACKAGE_HEADING_RE.sub(r"## \g<version> — \g<date>", body.strip())
    return escape_mdx(markdown)


def source_comment(**attrs: str) -> str:
    serialized = " ".join(f'{key}="{value}"' for key, value in attrs.items())
    return f"{{/* GENERATED_RELEASE_NOTES {serialized} */}}"


def release_slug(version: Version) -> str:
    return str(version).replace(".", "-")


def release_page_ref(target: ReleaseNoteTarget, section: ReleaseNoteSection) -> str:
    return f"{target.release_page_root}/{release_slug(section.version)}"


def release_page_path(target: ReleaseNoteTarget, section: ReleaseNoteSection) -> Path:
    return target.release_dir / f"{release_slug(section.version)}.mdx"


def page_frontmatter(*, title: str, description: str) -> str:
    return "---\n" f'title: "{title}"\n' f'description: "{description}"\n' "---\n\n"


def release_index_page(target: ReleaseNoteTarget, sections: Sequence[ReleaseNoteSection]) -> str:
    latest = sections[0]
    frontmatter = (
        "---\n"
        f'title: "{target.title}"\n'
        f'description: "{target.description}"\n'
        'sidebarTitle: "Release Notes"\n'
        "---"
    )
    comment = source_comment(
        target=target.key,
        format="split-index",
        latest_version=str(latest.version),
        latest_source=latest.source_id,
    )
    lines = [
        frontmatter,
        "",
        comment,
        "",
        f"Release notes are reproduced from {target.source_description}.",
        "",
        "## Releases",
        "",
    ]
    for section in sections:
        lines.append(f"- [{target.title} {section.title}](/{release_page_ref(target, section)})")
    return "\n".join(lines) + "\n"


def release_page(target: ReleaseNoteTarget, section: ReleaseNoteSection) -> str:
    comment = source_comment(
        target=target.key,
        format="split-release",
        version=str(section.version),
        source=section.source_id,
    )
    body = re.sub(r"^## ", "# ", section.body.strip(), count=1, flags=re.MULTILINE)
    return (
        f"{page_frontmatter(title=section.title, description=f'{target.title} {section.title} release notes.')}"
        f"{comment}\n\n"
        f"{body}\n"
    )


def write_release_pages(target: ReleaseNoteTarget, sections: Sequence[ReleaseNoteSection]) -> bool:
    desired_files = {
        target.index_path: release_index_page(target, sections),
        **{release_page_path(target, section): release_page(target, section) for section in sections},
    }
    existing_release_files = set(target.release_dir.glob("*.mdx")) if target.release_dir.exists() else set()
    stale_files = existing_release_files - set(desired_files)
    changed = any(path.read_text(encoding="utf-8") != text if path.exists() else True for path, text in desired_files.items())
    changed = changed or bool(stale_files)
    if not changed:
        return False

    target.release_dir.mkdir(parents=True, exist_ok=True)
    for path, text in desired_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for path in stale_files:
        path.unlink()
    return True


def package_release_sections(releases: Sequence[PackageRelease]) -> tuple[ReleaseNoteSection, ...]:
    return tuple(
        ReleaseNoteSection(
            version=release.version,
            title=package_release_title(release),
            body=normalize_package_release_body(release.body),
            source_id=release.tag_name,
            source_url=release.html_url,
        )
        for release in releases
    )


def package_release_title(release: PackageRelease) -> str:
    match = PACKAGE_HEADING_RE.search(release.body)
    if match is None:
        return str(release.version)
    return f"{match.group('version')} — {match.group('date')}"


def rst_inline_to_mdx(text: str, *, source_repo: str, source_ref: str, doc_base_path: str) -> str:
    def doc_role(match: re.Match[str]) -> str:
        label = match.group("label")
        target = match.group("target")
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(doc_base_path), target))
        if not normalized.endswith(".rst"):
            normalized = f"{normalized}.rst"
        url = f"https://github.com/{source_repo}/blob/{source_ref}/{normalized}"
        return f"[{label}]({url})"

    text = re.sub(r":doc:`(?P<label>[^`<]+?)\s*<(?P<target>[^`>]+)>`", doc_role, text)
    text = re.sub(r"`([^`<]+?)\s*<([^`>]+)>`_{1,2}", r"[\1](\2)", text)
    text = re.sub(r":ref:`([^`<]+?)\s*<[^`>]+>`", r"\1", text)
    text = re.sub(r"``([^`]+)``", r"`\1`", text)
    return text.rstrip()


def rst_to_mdx(
    rst: str,
    *,
    source_repo: str,
    source_ref: str,
    doc_base_path: str = WALLET_SDK_SOURCE_PATH,
) -> str:
    lines = rst.replace("\r\n", "\n").splitlines()
    output: list[str] = []
    index = 0
    in_code_fence = False

    while index < len(lines):
        line = lines[index].rstrip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""

        if in_code_fence:
            if line.startswith("    ") or line == "":
                output.append(line[4:] if line.startswith("    ") else "")
                index += 1
                continue
            while output and output[-1] == "":
                output.pop()
            output.append("```")
            output.append("")
            in_code_fence = False
            continue

        if line.strip().startswith(".. code-block::"):
            language = line.split("::", 1)[1].strip() or "text"
            output.append(f"```{language}")
            in_code_fence = True
            index += 1
            if index < len(lines) and lines[index].strip() == "":
                index += 1
            continue

        if line.strip().startswith(".. "):
            index += 1
            continue

        if next_line and set(next_line) <= {"=", "-", "~", "^"} and len(next_line) >= len(line.strip()):
            heading = line.strip()
            underline = next_line[0]
            if underline == "=":
                if output:
                    output.append(f"# {heading}")
            elif VERSION_RE.match(heading):
                output.append(f"## {heading}")
            else:
                level = "###" if underline == "-" else "####"
                output.append(f"{level} {heading}")
            index += 2
            continue

        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("* "):
            output.append(
                f"{indent}- {rst_inline_to_mdx(stripped[2:], source_repo=source_repo, source_ref=source_ref, doc_base_path=doc_base_path)}"
            )
        else:
            output.append(
                rst_inline_to_mdx(
                    line,
                    source_repo=source_repo,
                    source_ref=source_ref,
                    doc_base_path=doc_base_path,
                )
            )
        index += 1

    if in_code_fence:
        while output and output[-1] == "":
            output.pop()
        output.append("```")

    mdx = "\n".join(output)
    mdx = re.sub(r"\n{3,}", "\n\n", mdx)
    return escape_mdx(mdx).strip()


def split_release_sections(markdown: str, *, source_id: str, source_url: str) -> tuple[str, tuple[ReleaseNoteSection, ...]]:
    heading_matches = tuple(re.finditer(r"^## (?P<title>(?P<version>\d+\.\d+\.\d+)(?:\s+—\s+.+)?)\s*$", markdown, re.MULTILINE))
    if not heading_matches:
        raise RuntimeError("Could not find release-note version sections")

    intro = markdown[: heading_matches[0].start()].strip()
    sections: list[ReleaseNoteSection] = []
    for index, match in enumerate(heading_matches):
        next_start = heading_matches[index + 1].start() if index + 1 < len(heading_matches) else len(markdown)
        body = markdown[match.start() : next_start].strip()
        sections.append(
            ReleaseNoteSection(
                version=Version.parse(match.group("version")),
                title=match.group("title"),
                body=body,
                source_id=source_id,
                source_url=source_url,
            )
        )
    return intro, tuple(sections)


def wallet_sdk_sections(source: GithubContent) -> tuple[str, tuple[ReleaseNoteSection, ...]]:
    body = rst_to_mdx(source.text, source_repo=WALLET_SDK_REPO, source_ref=source.sha)
    return split_release_sections(
        body,
        source_id=source.sha,
        source_url=source.html_url,
    )


def release_nav_group(target: ReleaseNoteTarget, sections: Sequence[ReleaseNoteSection]) -> dict[str, object]:
    return {
        "group": target.title,
        "pages": [target.page_ref, *(release_page_ref(target, section) for section in sections)],
    }


def replace_release_nav_entry(
    pages: list[object],
    *,
    target: ReleaseNoteTarget,
    sections: Sequence[ReleaseNoteSection],
) -> None:
    replacement = release_nav_group(target, sections)
    insert_at: int | None = None
    filtered: list[object] = []
    for item in pages:
        if isinstance(item, str) and (item == target.page_ref or item.startswith(f"{target.release_page_root}/")):
            insert_at = len(filtered)
            continue
        if isinstance(item, dict) and item.get("group") == target.title:
            insert_at = len(filtered)
            continue
        filtered.append(item)
    if insert_at is None:
        insert_at = len(filtered)
    filtered.insert(insert_at, replacement)
    pages[:] = filtered


def update_docs_json(
    *,
    target: ReleaseNoteTarget,
    sections: Sequence[ReleaseNoteSection],
    docs_json: Path = DEFAULT_DOCS_JSON,
) -> bool:
    docs = json.loads(docs_json.read_text(encoding="utf-8"))
    products = docs.get("navigation", {}).get("products")
    if not isinstance(products, list):
        raise ValueError(f"{docs_json} must define navigation.products")

    for product in products:
        if not isinstance(product, dict):
            continue
        if product.get("product") == "Integrations":
            groups = product.get("groups")
            if isinstance(groups, list):
                for group in groups:
                    if isinstance(group, dict) and group.get("group") == "Release Notes":
                        pages = group.get("pages")
                        if isinstance(pages, list):
                            replace_release_nav_entry(pages, target=target, sections=sections)
        if product.get("product") == "Release Notes":
            pages = product.get("pages")
            if isinstance(pages, list):
                for group in pages:
                    if isinstance(group, dict) and group.get("group") == "Wallet Integration":
                        group_pages = group.get("pages")
                        if isinstance(group_pages, list):
                            replace_release_nav_entry(group_pages, target=target, sections=sections)

    updated = json.dumps(docs, indent=2) + "\n"
    before = docs_json.read_text(encoding="utf-8")
    if before == updated:
        return False
    docs_json.write_text(updated, encoding="utf-8")
    return True


def update_package_release_notes(target: PackageReleaseTarget) -> bool:
    releases = package_releases(target.package_name)
    sections = package_release_sections(releases)
    release_target = WALLET_RELEASE_TARGETS[target.key]
    changed_pages = write_release_pages(release_target, sections)
    changed_nav = update_docs_json(target=release_target, sections=sections)
    return changed_pages or changed_nav


def update_wallet_sdk_release_notes(*, source_ref: str) -> bool:
    source = github_content(WALLET_SDK_REPO, WALLET_SDK_SOURCE_PATH, source_ref=source_ref)
    release_target = WALLET_RELEASE_TARGETS["wallet-sdk"]
    _intro, sections = wallet_sdk_sections(source)
    changed_pages = write_release_pages(release_target, sections)
    changed_nav = update_docs_json(target=release_target, sections=sections)
    return changed_pages or changed_nav


def targets_to_run(requested: str) -> tuple[str, ...]:
    if requested == "all":
        return ("wallet-gateway", "wallet-sdk", "dapp-sdk")
    return (requested,)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh generated release-note pages from upstream sources."
    )
    parser.add_argument(
        "--target",
        choices=("all", "wallet-gateway", "wallet-sdk", "dapp-sdk"),
        default="all",
        help="Release-note target to refresh.",
    )
    parser.add_argument(
        "--wallet-sdk-ref",
        default="main",
        help="Git ref to use for the Wallet SDK release-note source.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    changed = False
    for target in targets_to_run(args.target):
        if target in PACKAGE_RELEASE_TARGETS:
            changed = update_package_release_notes(PACKAGE_RELEASE_TARGETS[target]) or changed
            continue
        if target == "wallet-sdk":
            changed = update_wallet_sdk_release_notes(source_ref=args.wallet_sdk_ref) or changed
            continue
        raise ValueError(f"Unknown release-note target: {target}")
    print("Release notes updated." if changed else "Release notes already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
