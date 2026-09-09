from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
from typing import Any
import urllib.parse
import urllib.request


DEFAULT_RELEASE_REPO = "digital-asset/canton"
SIMPLE_TOPOLOGY_CONFIG = Path("examples/01-simple-topology/simple-topology.conf")
STABLE_TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")
USER_AGENT = "cf-docs-canton-release-reference/1.0"


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    version: str
    name: str
    url: str
    size: int
    digest: str


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


def resolve_release_asset(
    *, release_repo: str = DEFAULT_RELEASE_REPO, tag: str | None = None
) -> ReleaseAsset:
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
        raise ValueError(f"GitHub release asset {asset_name} is missing its URL")
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
    temporary_path = archive_path.with_name(f"{archive_path.name}.{os.getpid()}.tmp")
    request = urllib.request.Request(asset.url, headers={"User-Agent": USER_AGENT})
    try:
        with (
            urllib.request.urlopen(request, timeout=300) as response,
            temporary_path.open("wb") as handle,
        ):
            shutil.copyfileobj(response, handle)
        verify_archive(temporary_path, asset)
        temporary_path.replace(archive_path)
    finally:
        temporary_path.unlink(missing_ok=True)
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
        and json.loads(manifest_path.read_text(encoding="utf-8")) == expected_manifest
    ):
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


def run_reference_script(
    *,
    distribution_root: Path,
    script_path: Path,
    cache_dir: Path,
    cache_namespace: str,
    asset: ReleaseAsset,
    force_refresh: bool,
) -> Any:
    script_digest = hashlib.sha256(script_path.read_bytes()).hexdigest()
    output_path = (
        cache_dir
        / "reference-json"
        / asset.tag
        / cache_namespace
        / f"{script_digest}.json"
    )
    if output_path.is_file() and not force_refresh:
        return json.loads(output_path.read_text(encoding="utf-8"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(distribution_root / "bin" / "canton"),
        "run",
        str(script_path),
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
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
