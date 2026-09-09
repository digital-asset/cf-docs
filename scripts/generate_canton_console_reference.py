#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import textwrap
from typing import Any, TypedDict, cast
import urllib.parse
import urllib.request

from docs_env import ensure_repo_direnv
from canton_console_rst import (
    convert_generated_console_rst,
    parse_generated_console_rst,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = REPO_ROOT / ".internal" / "cache" / "canton-release-reference"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "docs-source"
    / "global-synchronizer"
    / "reference"
    / "canton-console-commands.mdx"
)
DEFAULT_RELEASE_REPO = "digital-asset/canton"
CONSOLE_TEMPLATE_PATH = "docs-open/src/main/resources/console.rst.template"
REFERENCE_SCRIPT = REPO_ROOT / "scripts" / "canton_console_reference.canton"
SIMPLE_TOPOLOGY_CONFIG = Path("examples/01-simple-topology/simple-topology.conf")
USER_AGENT = "cf-docs-canton-console-reference/1.0"
STABLE_TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")


class ConsoleItem(TypedDict):
    name: str
    arguments: list[list[str]]
    return_type: str
    summary: str
    description: str
    topic: list[str]
    scope: str


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    version: str
    name: str
    url: str
    size: int
    digest: str


@dataclass(frozen=True)
class PublicSourceArtifact:
    repo: str
    ref: str
    commit: str
    path: str
    blob: str
    content: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Canton console command reference from a public Canton release binary."
    )
    parser.add_argument("--release-repo", default=DEFAULT_RELEASE_REPO)
    parser.add_argument(
        "--canton-tag",
        help="Public Canton release tag. Defaults to the latest GitHub release.",
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--reference-json",
        type=Path,
        help="Use previously generated reference JSON instead of downloading and running a Canton release.",
    )
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument(
        "--docs-json",
        help="Accepted for compatibility with the aggregate reference generator; this page does not alter navigation.",
    )
    return parser.parse_args()


def github_api_json(path: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/{path.lstrip('/')}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def resolve_release_asset(*, release_repo: str, tag: str | None) -> ReleaseAsset:
    api_path = (
        f"repos/{release_repo}/releases/tags/{urllib.parse.quote(tag, safe='')}"
        if tag
        else f"repos/{release_repo}/releases/latest"
    )
    payload = github_api_json(api_path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected GitHub release object for {release_repo}")

    resolved_tag = payload.get("tag_name")
    if not isinstance(resolved_tag, str):
        raise ValueError(f"GitHub release is missing tag_name for {release_repo}")
    tag_match = STABLE_TAG_RE.fullmatch(resolved_tag)
    if tag_match is None:
        raise ValueError(f"Expected a stable Canton release tag, got {resolved_tag!r}")
    version = tag_match.group("version")
    asset_name = f"canton-open-source-{version}.tar.gz"

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError(f"GitHub release {resolved_tag} is missing assets")
    asset = next(
        (
            item
            for item in assets
            if isinstance(item, dict) and item.get("name") == asset_name
        ),
        None,
    )
    if asset is None:
        raise ValueError(f"GitHub release {resolved_tag} does not contain {asset_name}")

    url = asset.get("browser_download_url")
    size = asset.get("size")
    digest = asset.get("digest")
    if not isinstance(url, str) or not url:
        raise ValueError(
            f"GitHub release asset {asset_name} is missing its download URL"
        )
    if not isinstance(size, int) or size <= 0:
        raise ValueError(f"GitHub release asset {asset_name} is missing its size")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(
            f"GitHub release asset {asset_name} is missing its SHA-256 digest"
        )

    return ReleaseAsset(
        tag=resolved_tag,
        version=version,
        name=asset_name,
        url=url,
        size=size,
        digest=digest,
    )


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def resolve_public_source_artifact(
    *, repo: str, ref: str, path: str
) -> PublicSourceArtifact:
    encoded_ref = urllib.parse.quote(ref, safe="")
    commit_payload = github_api_json(f"repos/{repo}/commits/{encoded_ref}")
    if not isinstance(commit_payload, dict) or not isinstance(
        commit_payload.get("sha"), str
    ):
        raise ValueError(f"Could not resolve public source commit {repo}@{ref}")
    commit = commit_payload["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"Unexpected public source commit SHA for {repo}@{ref}")

    encoded_path = urllib.parse.quote(path, safe="/")
    source_payload = github_api_json(
        f"repos/{repo}/contents/{encoded_path}?ref={encoded_ref}"
    )
    if not isinstance(source_payload, dict) or source_payload.get("type") != "file":
        raise ValueError(f"Expected a public source file at {repo}@{ref}:{path}")
    if source_payload.get("encoding") != "base64" or not isinstance(
        source_payload.get("content"), str
    ):
        raise ValueError(
            f"GitHub did not return base64 content for {repo}@{ref}:{path}"
        )
    blob = source_payload.get("sha")
    if not isinstance(blob, str) or not re.fullmatch(r"[0-9a-f]{40}", blob):
        raise ValueError(f"Unexpected Git blob SHA for {repo}@{ref}:{path}")
    content = base64.b64decode(source_payload["content"], validate=False)
    actual_blob = git_blob_sha(content)
    if actual_blob != blob:
        raise ValueError(
            f"Git blob mismatch for {repo}@{ref}:{path}: expected {blob}, got {actual_blob}"
        )
    return PublicSourceArtifact(
        repo=repo,
        ref=ref,
        commit=commit,
        path=path,
        blob=blob,
        content=content.decode("utf-8"),
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path, asset: ReleaseAsset) -> None:
    if path.stat().st_size != asset.size:
        raise ValueError(f"Release archive size mismatch for {path}")
    expected_digest = asset.digest.removeprefix("sha256:")
    actual_digest = sha256(path)
    if actual_digest != expected_digest:
        raise ValueError(
            f"Release archive SHA-256 mismatch for {path}: expected {expected_digest}, got {actual_digest}"
        )


def ensure_release_archive(
    *, asset: ReleaseAsset, cache_dir: Path, force_refresh: bool
) -> Path:
    archive_path = cache_dir / "release-assets" / asset.tag / asset.name
    if archive_path.exists() and not force_refresh:
        verify_archive(archive_path, asset)
        return archive_path

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = archive_path.with_name(f"{archive_path.name}.{os.getpid()}.tmp")
    request = urllib.request.Request(asset.url, headers={"User-Agent": USER_AGENT})
    try:
        with (
            urllib.request.urlopen(request, timeout=300) as response,
            temp_path.open("wb") as handle,
        ):
            shutil.copyfileobj(response, handle)
        verify_archive(temp_path, asset)
        temp_path.replace(archive_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return archive_path


def extract_release(
    *, archive_path: Path, asset: ReleaseAsset, cache_dir: Path, force_refresh: bool
) -> Path:
    extract_root = cache_dir / "release-distributions" / asset.tag
    distribution_root = extract_root / f"canton-open-source-{asset.version}"
    manifest_path = extract_root / ".asset.json"
    expected_manifest = {
        "asset": asset.name,
        "digest": asset.digest,
        "size": asset.size,
        "tag": asset.tag,
        "url": asset.url,
    }
    required_paths = (
        distribution_root / "bin" / "canton",
        distribution_root / "lib" / f"canton-open-source-{asset.version}.jar",
        distribution_root / SIMPLE_TOPOLOGY_CONFIG,
    )
    if (
        not force_refresh
        and manifest_path.is_file()
        and all(path.is_file() for path in required_paths)
    ):
        if json.loads(manifest_path.read_text(encoding="utf-8")) == expected_manifest:
            return distribution_root

    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)
    required_members = {
        path.relative_to(extract_root).as_posix() for path in required_paths
    }
    extracted_members: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            if member.name not in required_members:
                continue
            archive.extract(member, extract_root, filter="data")
            extracted_members.add(member.name)
    missing_members = sorted(required_members - extracted_members)
    if missing_members:
        raise FileNotFoundError(
            f"Release archive is missing required files: {', '.join(missing_members)}"
        )

    canton_binary = distribution_root / "bin" / "canton"
    canton_binary.chmod(canton_binary.stat().st_mode | stat.S_IXUSR)
    manifest_path.write_text(
        json.dumps(expected_manifest, indent=2) + "\n", encoding="utf-8"
    )
    return distribution_root


def load_console_items(payload: object) -> list[ConsoleItem]:
    if not isinstance(payload, dict) or not isinstance(payload.get("console"), list):
        raise ValueError("Canton reference JSON must contain a console list")

    items: list[ConsoleItem] = []
    for index, item in enumerate(payload["console"]):
        if not isinstance(item, dict):
            raise ValueError(f"Console item {index} must be an object")
        required_strings = ("name", "return_type", "summary", "description", "scope")
        if not all(isinstance(item.get(key), str) for key in required_strings):
            raise ValueError(f"Console item {index} has invalid string fields")
        topics = item.get("topic")
        arguments = item.get("arguments")
        if (
            not isinstance(topics, list)
            or not topics
            or not all(isinstance(value, str) and value for value in topics)
        ):
            raise ValueError(f"Console item {index} has invalid topics")
        if not isinstance(arguments, list) or not all(
            isinstance(argument, list)
            and len(argument) == 2
            and all(isinstance(value, str) for value in argument)
            for argument in arguments
        ):
            raise ValueError(f"Console item {index} has invalid arguments")
        items.append(cast(ConsoleItem, item))
    return items


def generate_reference_json(
    *,
    distribution_root: Path,
    cache_dir: Path,
    asset: ReleaseAsset,
    force_refresh: bool,
) -> dict[str, Any]:
    script_digest = hashlib.sha256(REFERENCE_SCRIPT.read_bytes()).hexdigest()
    output_path = cache_dir / "reference-json" / asset.tag / f"{script_digest}.json"
    if output_path.is_file() and not force_refresh:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        load_console_items(payload)
        return payload

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(distribution_root / "bin" / "canton"),
        "run",
        str(REFERENCE_SCRIPT),
        "-c",
        str(distribution_root / SIMPLE_TOPOLOGY_CONFIG),
        "--log-level-stdout=error",
    ]
    environment = os.environ.copy()
    environment.pop("CI", None)
    completed = subprocess.run(
        command,
        cwd=distribution_root,
        env=environment,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    load_console_items(payload)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _rst_description_block(content: str) -> str:
    dedented = textwrap.dedent(content)
    block = textwrap.indent(dedented, "            ")
    return f"\n        .. code-block:: none\n\n{block}\n"


def _render_upstream_command(item: ConsoleItem, *, seen_names: dict[str, int]) -> str:
    name = item["name"]
    if name in seen_names:
        seen_names[name] += 1
        anchor = f"{name}_{seen_names[name]}"
    else:
        seen_names[name] = 0
        anchor = name

    scope = f" ({item['scope']})" if item["scope"] != "Stable" else ""
    lines = [
        f".. _{anchor.lower()}:",
        "",
        "",
        f":ref:`{name}{scope} <{anchor}>`",
        f"\t* **Summary**: {item['summary']}",
    ]
    if item["arguments"]:
        lines.append("\t* **Arguments**: ")
        lines.extend(
            f"\t\t* ``{argument_name}``: {argument_type}"
            for argument_name, argument_type in item["arguments"]
        )
    if item["return_type"]:
        lines.extend(("\t* **Return type**: ", f"\t\t* {item['return_type']}"))
    if item["description"]:
        lines.append(
            f"\t* **Description**:\n{_rst_description_block(item['description'])}"
        )
    lines.append("")
    return "\n".join(lines)


def render_upstream_console_rst(items: list[ConsoleItem], *, template: str) -> str:
    by_topic: dict[str, list[str]] = defaultdict(list)
    seen_names: dict[str, int] = {}
    for item in sorted(items, key=lambda candidate: candidate["name"]):
        topic = ", ".join(item["topic"])
        by_topic[topic].append(_render_upstream_command(item, seen_names=seen_names))

    rendered = template
    for topic, commands in by_topic.items():
        rendered = rendered.replace(
            f"<console-topic-marker: {topic}>", "\n".join(commands)
        )
    if "console-topic-marker" in rendered:
        marker_index = rendered.index("console-topic-marker")
        context = rendered[marker_index : marker_index + 200]
        raise ValueError(f"Public console template has an unmatched marker: {context}")
    return rendered


def render_console_reference(
    items: list[ConsoleItem],
    *,
    asset: ReleaseAsset,
    source: PublicSourceArtifact,
) -> tuple[str, int]:
    rst = render_upstream_console_rst(items, template=source.content)
    rendered_commands, _static_lines = parse_generated_console_rst(rst)
    header = "\n".join(
        (
            "---",
            'title: "Canton Console Commands"',
            'description: "Canton admin console command reference: participant, mediator, sequencer, and topology commands."',
            "---",
            "",
            (
                "{/* GENERATED_FROM "
                f'source="{asset.name}" ref="{asset.tag}" digest="{asset.digest}" '
                f'template_source="{source.repo}:{source.path}" template_commit="{source.commit}" '
                f'template_blob="{source.blob}" raw_command_count="{len(items)}" '
                f'rendered_command_count="{len(rendered_commands)}" */}}'
            ),
        )
    )
    source_version = ".".join(asset.version.split(".")[:2])
    mdx = convert_generated_console_rst(
        rst,
        source_version=source_version,
        header=header,
        apply_current_content_edits=True,
        apply_legacy_snapshot_edits=False,
        escape_description_mdx=True,
    )
    return mdx, len(rendered_commands)


def main() -> int:
    ensure_repo_direnv(
        repo_root=REPO_ROOT, script_path=Path(__file__).resolve(), argv=sys.argv[1:]
    )
    args = parse_args()
    cache_dir = args.cache_dir.resolve()
    output_path = args.output.resolve()
    asset = resolve_release_asset(release_repo=args.release_repo, tag=args.canton_tag)
    source = resolve_public_source_artifact(
        repo=args.release_repo,
        ref=asset.tag,
        path=CONSOLE_TEMPLATE_PATH,
    )
    if args.reference_json is not None:
        payload = json.loads(args.reference_json.resolve().read_text(encoding="utf-8"))
    else:
        archive_path = ensure_release_archive(
            asset=asset, cache_dir=cache_dir, force_refresh=args.force_refresh
        )
        distribution_root = extract_release(
            archive_path=archive_path,
            asset=asset,
            cache_dir=cache_dir,
            force_refresh=args.force_refresh,
        )
        payload = generate_reference_json(
            distribution_root=distribution_root,
            cache_dir=cache_dir,
            asset=asset,
            force_refresh=args.force_refresh,
        )

    items = load_console_items(payload)
    output, rendered_count = render_console_reference(items, asset=asset, source=source)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")
    print(
        f"Generated {output_path} from {asset.tag} "
        f"({rendered_count} of {len(items)} console commands selected by the public template)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
