#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from validate_splice_mintlify_openapi_nav import validate_splice_nav
from x2mdx.history import (
    SourceArtifact,
    SurfaceHistoryReport,
    VersionSelectionPolicy,
    history_events_for_item,
    validate_history_report,
    write_history_report,
)
from x2mdx.openapi import (
    ManualOpenAPIRenderOptions,
    OpenAPIHistoryScope,
    build_openapi_history_report,
    render_manual_openapi_operation,
)
from x2mdx.render import write_page

REPO_ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "digital-asset-docs-mintlify-openapi/1.0"
DEFAULT_SOURCE_CONFIG = (
    REPO_ROOT
    / "config"
    / "mintlify-openapi"
    / "splice-openapi"
    / "source-artifacts.json"
)
DEFAULT_CACHE_DIR = (
    REPO_ROOT / ".internal" / "cache" / "mintlify-openapi" / "splice-openapi"
)
DEFAULT_DOCS_JSON = REPO_ROOT / "docs-source" / "docs.json"
DEFAULT_HISTORY_REPORT = (
    REPO_ROOT / "docs-source" / "openapi" / "splice" / "history-report.json"
)
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
SCAN_OPENAPI_PLACEHOLDER_SERVER = "https://example.com/api/scan"
SCAN_OPENAPI_PUBLIC_SERVER = (
    "https://scan.sv-1.global.canton.network.sync.global/api/scan"
)
SCAN_OPENAPI_SERVER_REPLACEMENT_SPECS = {"scan.yaml", "scan-stream-server.yaml"}
UNPUBLISHED_SECURITY_SCHEME_LINK_RE = re.compile(
    r"as described in \[spliceAppBearerAuth\]\(\"?(?:\.\./)+common/src/main/openapi/"
    r"common-external\.yaml#/components/securitySchemes/spliceAppBearerAuth\"?\)"
)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def request_headers(url: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def github_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers=request_headers(url),
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def selected_releases(
    *,
    source_config: dict[str, Any],
    include_versions: set[str] | None,
) -> list[dict[str, str]]:
    release_repo = source_config.get("release_repo")
    tag_regex = source_config.get("tag_regex")
    asset_template = source_config.get("asset_template")
    min_version = source_config.get("min_version") or "0.0.0"
    if not isinstance(release_repo, str) or not release_repo:
        raise ValueError("Source config must define release_repo")
    if not isinstance(tag_regex, str) or not tag_regex:
        raise ValueError("Source config must define tag_regex")
    if not isinstance(asset_template, str) or not asset_template:
        raise ValueError("Source config must define asset_template")
    if not isinstance(min_version, str):
        raise ValueError("min_version must be a string")

    matcher = re.compile(tag_regex)
    minimum_key = version_key(min_version)
    releases: list[dict[str, str]] = []
    page = 1
    while True:
        payload = github_json(
            f"https://api.github.com/repos/{release_repo}/releases?per_page=100&page={page}"
        )
        if not isinstance(payload, list):
            raise ValueError(
                f"Expected list payload from GitHub releases API for {release_repo}"
            )
        if not payload:
            break
        for release in payload:
            if not isinstance(release, dict):
                continue
            if release.get("draft") or release.get("prerelease"):
                continue
            tag_name = release.get("tag_name")
            if not isinstance(tag_name, str):
                continue
            match = matcher.fullmatch(tag_name)
            if not match:
                continue
            version = match.groupdict().get("version") or tag_name.removeprefix("v")
            if version_key(version) < minimum_key:
                continue
            if include_versions is not None and version not in include_versions:
                continue
            asset_name = asset_template.format(version=version)
            assets = release.get("assets")
            if not isinstance(assets, list):
                continue
            asset = next(
                (
                    item
                    for item in assets
                    if isinstance(item, dict) and item.get("name") == asset_name
                ),
                None,
            )
            if asset is None:
                continue
            download_url = asset.get("browser_download_url")
            if not isinstance(download_url, str) or not download_url:
                continue
            releases.append(
                {
                    "version": version,
                    "tag": tag_name,
                    "asset_name": asset_name,
                    "download_url": download_url,
                }
            )
        if len(payload) < 100:
            break
        page += 1

    releases.sort(key=lambda entry: version_key(entry["version"]))
    if not releases:
        raise ValueError(
            f"No published releases matched the configured Splice OpenAPI selection for {release_repo}"
        )
    return releases


def resolve_publish_release(
    *,
    source_config: dict[str, Any],
    releases: list[dict[str, str]],
    requested_version: str | None,
) -> dict[str, str]:
    publish_version = requested_version
    if publish_version is None:
        configured = source_config.get("publish_version")
        if isinstance(configured, str) and configured.strip():
            publish_version = configured.strip()

    if publish_version is None:
        return releases[-1]

    selected = next(
        (entry for entry in releases if entry["version"] == publish_version), None
    )
    if selected is None:
        available = ", ".join(entry["version"] for entry in releases)
        raise ValueError(
            f"Publish version '{publish_version}' not found in selected releases: {available}"
        )
    return selected


def comparison_releases_through_publish(
    *, releases: list[dict[str, str]], publish_version: str
) -> list[dict[str, str]]:
    publish_index = next(
        (
            index
            for index, release in enumerate(releases)
            if release["version"] == publish_version
        ),
        None,
    )
    if publish_index is None:
        raise ValueError(
            f"Publish version '{publish_version}' is absent from selected releases"
        )
    return releases[: publish_index + 1]


def archive_path(cache_dir: Path, *, release: dict[str, str]) -> Path:
    return cache_dir / "archives" / release["version"] / release["asset_name"]


def ensure_archive(
    *,
    cache_dir: Path,
    release: dict[str, str],
    force_refresh: bool,
) -> Path:
    output_path = archive_path(cache_dir, release=release)
    if output_path.exists() and not force_refresh:
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.{os.getpid()}.tmp")
    request = urllib.request.Request(
        release["download_url"],
        headers={"User-Agent": USER_AGENT},
    )
    with (
        urllib.request.urlopen(request, timeout=180) as response,
        temp_path.open("wb") as handle,
    ):
        shutil.copyfileobj(response, handle)
    temp_path.replace(output_path)
    return output_path


def normalized_families(source_config: dict[str, Any]) -> list[dict[str, Any]]:
    families = source_config.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("Source config must define a non-empty families list")

    normalized: list[dict[str, Any]] = []
    for family in families:
        if not isinstance(family, dict):
            raise ValueError("Each family entry must be an object")
        group = family.get("group")
        specs = family.get("specs")
        if not isinstance(group, str) or not group:
            raise ValueError("Each family must define a non-empty group")
        if not isinstance(specs, list) or not specs:
            raise ValueError(f"Family '{group}' must define a non-empty specs list")
        normalized_specs: list[dict[str, Any]] = []
        for spec in specs:
            if not isinstance(spec, dict):
                raise ValueError(f"Spec entries for family '{group}' must be objects")
            filename = spec.get("filename")
            nav_label = spec.get("nav_label")
            source_ref = spec.get("source")
            directory = spec.get("directory")
            if not all(
                isinstance(item, str) and item
                for item in (filename, nav_label, source_ref, directory)
            ):
                raise ValueError(
                    f"Specs for family '{group}' must define non-empty filename, nav_label, source, and directory"
                )
            normalized_specs.append(
                {
                    "filename": filename,
                    "nav_label": nav_label,
                    "source": source_ref,
                    "directory": directory,
                }
            )
        normalized.append({"group": group, "specs": normalized_specs})
    return normalized


def extract_spec_bytes(
    *,
    archive: Path,
    spec_filenames: set[str],
) -> dict[str, bytes]:
    extracted: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            if not member.isfile():
                continue
            filename = Path(member.name).name
            if filename not in spec_filenames:
                continue
            raw_handle = handle.extractfile(member)
            if raw_handle is None:
                raise FileNotFoundError(
                    f"Failed to extract '{member.name}' from {archive}"
                )
            extracted[filename] = raw_handle.read()

    missing = sorted(spec_filenames - extracted.keys())
    if missing:
        joined = ", ".join(missing)
        raise FileNotFoundError(
            f"Archive {archive} did not contain expected OpenAPI specs: {joined}"
        )
    return extracted


def extract_available_spec_bytes(
    *,
    archive: Path,
    spec_filenames: set[str],
) -> dict[str, bytes]:
    extracted: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            if not member.isfile():
                continue
            filename = Path(member.name).name
            if filename not in spec_filenames:
                continue
            if filename in extracted:
                raise ValueError(
                    f"Duplicate OpenAPI spec '{filename}' found in {archive}"
                )
            raw_handle = handle.extractfile(member)
            if raw_handle is None:
                raise FileNotFoundError(
                    f"Failed to extract '{member.name}' from {archive}"
                )
            extracted[filename] = raw_handle.read()
    return extracted


def render_output_bytes(
    *, spec_filename: str, spec_bytes: bytes, output_path: Path
) -> bytes:
    if output_path.suffix not in {".yaml", ".yml"}:
        return spec_bytes

    text = spec_bytes.decode("utf-8")
    if spec_filename in SCAN_OPENAPI_SERVER_REPLACEMENT_SPECS:
        text = text.replace(SCAN_OPENAPI_PLACEHOLDER_SERVER, SCAN_OPENAPI_PUBLIC_SERVER)
    text = UNPUBLISHED_SECURITY_SCHEME_LINK_RE.sub(
        "as described by the `spliceAppBearerAuth` security scheme",
        text,
    )
    filtered_lines = [
        line
        for line in text.splitlines()
        if not re.fullmatch(r"\s*required:\s*\[\s*\]\s*", line)
    ]
    normalized_text = "\n".join(filtered_lines).rstrip() + "\n"
    normalized_text = add_missing_operation_summaries(normalized_text)
    return normalized_text.encode("utf-8")


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


def mintlify_operation_path(path: str) -> str:
    return re.sub(r"\{([^{}]+)\}", r":\1", path)


def generated_operation_summary(path: str, method: str) -> str:
    return f"{method.upper()} {mintlify_operation_path(path)}"


def path_only_operation_summary(path: str, method: str, summary: str) -> bool:
    normalized = summary.strip()
    if normalized == generated_operation_summary(path, method):
        return False
    return normalized in {
        path,
        mintlify_operation_path(path),
        f"{method.upper()} {path}",
    }


def operation_summary_rewrites(spec: dict[str, Any]) -> dict[tuple[str, str], str]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return {}

    rewrites: dict[tuple[str, str], str] = {}
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            summary = operation.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                rewrites[(path, method.lower())] = generated_operation_summary(
                    path, method
                )
            elif path_only_operation_summary(path, method, summary):
                rewrites[(path, method.lower())] = generated_operation_summary(
                    path, method
                )
    return rewrites


def add_missing_operation_summaries(text: str) -> str:
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError("Expected generated OpenAPI YAML to parse as an object")

    rewrites = operation_summary_rewrites(spec)
    if not rewrites:
        return text
    missing = missing_operation_summaries(spec)

    lines = text.splitlines()
    output_lines: list[str] = []
    in_paths = False
    current_path: str | None = None
    current_method: str | None = None

    for line in lines:
        if current_path is not None and current_method is not None:
            summary_match = re.fullmatch(r"      summary:\s*.*", line)
            if summary_match and (current_path, current_method) in rewrites:
                output_lines.append(
                    f'      summary: "{rewrites[(current_path, current_method)]}"'
                )
                current_method = None
                continue

        output_lines.append(line)

        if re.fullmatch(r"paths:\s*", line):
            in_paths = True
            current_path = None
            current_method = None
            continue

        if not in_paths:
            continue

        if line and not line.startswith(" "):
            in_paths = False
            current_path = None
            current_method = None
            continue

        path_match = re.fullmatch(r"  (?P<path>/.*):\s*", line)
        if path_match:
            current_path = path_match.group("path")
            current_method = None
            continue

        method_match = re.fullmatch(
            r"    (?P<method>get|put|post|delete|options|head|patch|trace):\s*", line
        )
        if current_path is None or method_match is None:
            continue

        method = method_match.group("method")
        current_method = method
        if (current_path, method) in missing:
            output_lines.append(f'      summary: "{rewrites[(current_path, method)]}"')
            current_method = None

    rendered = "\n".join(output_lines).rstrip() + "\n"
    parsed = yaml.safe_load(rendered)
    if not isinstance(parsed, dict):
        raise ValueError(
            "Generated OpenAPI YAML stopped parsing after summary insertion"
        )
    remaining = operation_summary_rewrites(parsed)
    if remaining:
        details = ", ".join(
            f"{method.upper()} {path}" for path, method in sorted(remaining)
        )
        raise ValueError(
            f"Failed to normalize generated summaries for OpenAPI operations: {details}"
        )
    return rendered


def materialize_release_specs(
    *,
    cache_dir: Path,
    release: dict[str, str],
    spec_filenames: set[str],
    force_refresh: bool,
) -> dict[str, dict[str, Any]]:
    archive = ensure_archive(
        cache_dir=cache_dir,
        release=release,
        force_refresh=force_refresh,
    )
    extracted = extract_available_spec_bytes(
        archive=archive,
        spec_filenames=spec_filenames,
    )
    fixture_dir = cache_dir / "fixtures" / release["version"]
    if fixture_dir.exists():
        shutil.rmtree(fixture_dir)
    fixture_dir.mkdir(parents=True, exist_ok=True)

    parsed: dict[str, dict[str, Any]] = {}
    for filename, raw_bytes in sorted(extracted.items()):
        normalized = render_output_bytes(
            spec_filename=filename,
            spec_bytes=raw_bytes,
            output_path=Path(filename),
        )
        fixture_path = fixture_dir / filename
        fixture_path.write_bytes(normalized)
        payload = yaml.safe_load(normalized.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(
                f"Expected {filename} from Splice {release['version']} to parse as an object"
            )
        parsed[filename] = payload
    return parsed


def versioned_enabled_specs(
    *,
    cache_dir: Path,
    releases: list[dict[str, str]],
    spec_filenames: set[str],
    force_refresh: bool,
) -> dict[str, dict[str, dict[str, Any]]]:
    snapshots: dict[str, dict[str, dict[str, Any]]] = {
        filename: {} for filename in spec_filenames
    }
    for release in releases:
        release_specs = materialize_release_specs(
            cache_dir=cache_dir,
            release=release,
            spec_filenames=spec_filenames,
            force_refresh=force_refresh,
        )
        for filename, payload in release_specs.items():
            snapshots[filename][release["version"]] = payload

    missing = sorted(
        filename for filename, versions in snapshots.items() if not versions
    )
    if missing:
        raise ValueError(
            "Enabled Splice OpenAPI specs were absent from every selected release: "
            + ", ".join(missing)
        )
    return snapshots


def write_managed_specs(
    *,
    docs_root: Path,
    source_config: dict[str, Any],
    families: list[dict[str, Any]],
    spec_bytes: dict[str, bytes],
) -> list[Path]:
    managed_root = source_config.get("managed_openapi_root")
    if not isinstance(managed_root, str) or not managed_root:
        raise ValueError("Source config must define managed_openapi_root")

    output_root = docs_root / managed_root
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    written_paths: list[Path] = []
    for family in families:
        for spec in family["specs"]:
            output_path = docs_root / spec["source"]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(
                render_output_bytes(
                    spec_filename=spec["filename"],
                    spec_bytes=spec_bytes[spec["filename"]],
                    output_path=output_path,
                )
            )
            written_paths.append(output_path)
            print(f"Published Mintlify OpenAPI source: {output_path}")
    return written_paths


def cleanup_legacy_outputs(*, docs_root: Path, source_config: dict[str, Any]) -> None:
    legacy_paths = source_config.get("legacy_cleanup_paths")
    if not isinstance(legacy_paths, list):
        return
    for relative_path in legacy_paths:
        if not isinstance(relative_path, str) or not relative_path:
            continue
        absolute_path = docs_root / relative_path
        if absolute_path.is_dir():
            shutil.rmtree(absolute_path, ignore_errors=True)
        elif absolute_path.exists():
            absolute_path.unlink()


def enabled_nav_specs(source_config: dict[str, Any]) -> set[str] | None:
    enabled = source_config.get("enabled_nav_specs")
    if enabled is None:
        return None
    if not isinstance(enabled, list):
        raise ValueError("enabled_nav_specs must be a list when set")

    normalized: set[str] = set()
    for item in enabled:
        if not isinstance(item, str) or not item:
            raise ValueError("enabled_nav_specs entries must be non-empty strings")
        normalized.add(item)
    return normalized


def filtered_families_for_navigation(
    *,
    families: list[dict[str, Any]],
    enabled_specs: set[str] | None,
) -> list[dict[str, Any]]:
    if enabled_specs is None:
        return families

    filtered: list[dict[str, Any]] = []
    for family in families:
        specs = [spec for spec in family["specs"] if spec["filename"] in enabled_specs]
        if specs:
            filtered.append({"group": family["group"], "specs": specs})
    return filtered


def validate_excluded_specs(
    *,
    source_config: dict[str, Any],
    families: list[dict[str, Any]],
    enabled_specs: set[str] | None,
) -> None:
    if enabled_specs is None:
        return
    all_specs = {spec["filename"] for family in families for spec in family["specs"]}
    disabled_specs = all_specs - enabled_specs
    excluded = source_config.get("excluded_specs")
    if not isinstance(excluded, list):
        raise ValueError(
            "source config must record every disabled family spec in excluded_specs"
        )
    recorded: set[str] = set()
    for index, item in enumerate(excluded):
        if not isinstance(item, dict):
            raise ValueError(f"excluded_specs[{index}] must be an object")
        filename = item.get("filename")
        reason = item.get("reason")
        if not isinstance(filename, str) or not filename:
            raise ValueError(
                f"excluded_specs[{index}].filename must be a non-empty string"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"excluded_specs[{index}].reason must be a non-empty string"
            )
        recorded.add(filename)
    if recorded != disabled_specs:
        raise ValueError(
            "excluded_specs must exactly match disabled family specs: "
            f"expected={sorted(disabled_specs)} recorded={sorted(recorded)}"
        )


def operation_items(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI specification must define paths")
    operations: list[tuple[str, str, dict[str, Any]]] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS and isinstance(operation, dict):
                operations.append((method.upper(), path, operation))
    return operations


def manual_operation_page_ref(*, directory: str, method: str, path: str) -> str:
    mintlify_path = mintlify_operation_path(path)
    slug = mintlify_path.removeprefix("/").replace("/", "").lower()
    return f"{directory.rstrip('/')}/{method.lower()}-{slug}"


def manual_operation_page_refs(*, spec: dict[str, Any], directory: str) -> list[str]:
    return [
        manual_operation_page_ref(directory=directory, method=method, path=path)
        for method, path, _operation in operation_items(spec)
    ]


def build_splice_history_report(
    *,
    source_config: dict[str, Any],
    families: list[dict[str, Any]],
    snapshots: dict[str, dict[str, dict[str, Any]]],
    releases: list[dict[str, str]],
    publish_version: str,
) -> SurfaceHistoryReport:
    comparison_versions = tuple(release["version"] for release in releases)
    scopes: list[OpenAPIHistoryScope] = []
    for family in families:
        for spec_config in family["specs"]:
            filename = spec_config["filename"]
            specs_by_version = snapshots[filename]
            published = specs_by_version.get(publish_version)
            if published is None:
                raise ValueError(
                    f"Enabled spec {filename} is absent from publish version {publish_version}"
                )
            current_routes = {
                (method.lower(), path): (
                    "/"
                    + manual_operation_page_ref(
                        directory=spec_config["directory"],
                        method=method,
                        path=path,
                    )
                )
                for method, path, _operation in operation_items(published)
            }
            scopes.append(
                OpenAPIHistoryScope(
                    id=filename,
                    specs_by_version=specs_by_version,
                    current_routes=current_routes,
                )
            )

    source_artifacts = tuple(
        SourceArtifact(
            version=release["version"],
            source=release["download_url"],
            revision=release["tag"],
            path=release["asset_name"],
        )
        for release in releases
    )
    limitations = tuple(
        f"{item['filename']} is excluded: {item['reason']}"
        for item in source_config.get("excluded_specs", [])
        if isinstance(item, dict)
        and isinstance(item.get("filename"), str)
        and isinstance(item.get("reason"), str)
    )
    return build_openapi_history_report(
        surface_id="splice-openapi",
        title="Splice OpenAPI",
        configured_scope=(
            f"Reader-facing operations from {len(scopes)} enabled Splice OpenAPI "
            "specifications."
        ),
        scopes=tuple(scopes),
        comparison_versions=comparison_versions,
        publish_version=publish_version,
        source_artifacts=source_artifacts,
        version_policy=VersionSelectionPolicy.LATEST_SELECTED_RELEASE,
        limitations=limitations,
    )


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


def manual_api_server(spec: dict[str, Any]) -> str:
    servers = spec.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, dict):
                url = server.get("url")
                if isinstance(url, str) and url.strip():
                    return url.strip()
    return "https://example.com"


def operation_authentication(
    *,
    spec: dict[str, Any],
    operation: dict[str, Any],
) -> tuple[str | None, str | None]:
    security = operation.get("security", spec.get("security"))
    if security is None or security == []:
        return None, None
    if not isinstance(security, list):
        raise ValueError("OpenAPI operation security must be a list")
    if any(requirement == {} for requirement in security):
        return None, None

    components = spec.get("components")
    schemes = (
        components.get("securitySchemes") if isinstance(components, dict) else None
    )
    if not isinstance(schemes, dict):
        raise ValueError("Secured OpenAPI operation does not define securitySchemes")
    for requirement in security:
        if not isinstance(requirement, dict):
            continue
        for scheme_name in requirement:
            scheme = schemes.get(scheme_name)
            if not isinstance(scheme, dict):
                continue
            if (
                scheme.get("type") == "http"
                and str(scheme.get("scheme")).lower() == "bearer"
            ):
                return "bearer", "Bearer token"
    raise ValueError(
        "Manual Splice OpenAPI rendering currently supports public or HTTP bearer operations"
    )


def prepare_manual_output_directories(
    *, docs_root: Path, families: list[dict[str, Any]]
) -> None:
    for family in families:
        for spec_config in family["specs"]:
            output_dir = docs_root / spec_config["directory"]
            if output_dir.exists():
                shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)


def write_manual_operation_pages(
    *,
    docs_json_path: Path,
    families: list[dict[str, Any]],
    snapshots: dict[str, dict[str, dict[str, Any]]],
    publish_version: str,
    history_report: SurfaceHistoryReport,
) -> set[Path]:
    docs_root = docs_json_path.parent
    prepare_manual_output_directories(docs_root=docs_root, families=families)
    written: set[Path] = set()
    history_items_by_route = {
        item.route: item for item in history_report.current_items() if item.route
    }
    for family in families:
        for spec_config in family["specs"]:
            filename = spec_config["filename"]
            specs_by_version = snapshots[filename]
            if publish_version not in specs_by_version:
                raise ValueError(
                    f"Enabled spec {filename} is absent from publish version {publish_version}"
                )
            published = specs_by_version[publish_version]
            server = manual_api_server(published)
            raw_spec_href = f"/{spec_config['source']}"
            for method, path, operation in operation_items(published):
                page_ref = manual_operation_page_ref(
                    directory=spec_config["directory"],
                    method=method,
                    path=path,
                )
                auth_method, authentication_label = operation_authentication(
                    spec=published,
                    operation=operation,
                )
                history_item = history_items_by_route.get(f"/{page_ref}")
                if history_item is None or not history_item.current_present:
                    raise ValueError(
                        f"Normalized Splice history is missing current operation: "
                        f"{filename} {method} {path}"
                    )
                history_events = list(
                    history_events_for_item(
                        history_item,
                        comparison_versions=history_report.comparison_versions,
                    )
                )
                page = render_manual_openapi_operation(
                    spec=published,
                    options=ManualOpenAPIRenderOptions(
                        method=method,
                        path=path,
                        output_path=f"{page_ref}.mdx",
                        server=server,
                        surface_label=spec_config["nav_label"],
                        auth_method=auth_method,
                        authentication_label=authentication_label,
                        raw_spec_href=raw_spec_href,
                    ),
                    history_events=history_events,
                    publish_version=publish_version,
                )
                output_path = docs_root / f"{page_ref}.mdx"
                write_page(page, output_path)
                written.add(output_path.resolve())
                print(f"Generated manual Splice OpenAPI page: {output_path}")
    return written


def build_splice_openapi_nav_entry(
    *, docs_root: Path, spec: dict[str, Any]
) -> dict[str, Any]:
    openapi_path = docs_root / spec["source"]
    payload = yaml.safe_load(openapi_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected OpenAPI spec to parse as an object: {openapi_path}")
    entry: dict[str, Any] = {
        "group": spec["nav_label"],
        "pages": manual_operation_page_refs(
            spec=payload,
            directory=spec["directory"],
        ),
    }
    return entry


def build_splice_group_pages(
    *, docs_root: Path, families: list[dict[str, Any]]
) -> list[Any]:
    pages: list[Any] = []
    for family in families:
        family_pages: list[dict[str, Any]] = []
        for spec in family["specs"]:
            family_pages.append(
                build_splice_openapi_nav_entry(docs_root=docs_root, spec=spec)
            )
        pages.append({"group": family["group"], "pages": family_pages})
    return pages


def navigation_pages(
    payload: dict[str, Any], dropdown_label: str, docs_json_path: Path
) -> list[Any]:
    navigation = payload.get("navigation")
    if not isinstance(navigation, dict):
        raise ValueError(f"docs.json missing navigation object: {docs_json_path}")

    dropdowns = navigation.get("dropdowns")
    if isinstance(dropdowns, list):
        dropdown = next(
            (
                item
                for item in dropdowns
                if isinstance(item, dict) and item.get("dropdown") == dropdown_label
            ),
            None,
        )
        if dropdown is None:
            raise ValueError(f"Dropdown not found in docs.json: {dropdown_label}")
        pages = dropdown.get("pages")
        if not isinstance(pages, list):
            raise ValueError(f"Dropdown does not expose a pages list: {dropdown_label}")
        return pages

    products = navigation.get("products")
    if isinstance(products, list):
        product = next(
            (
                item
                for item in products
                if isinstance(item, dict) and item.get("product") == dropdown_label
            ),
            None,
        )
        if product is None:
            raise ValueError(f"Product not found in docs.json: {dropdown_label}")
        pages = product.get("pages")
        if not isinstance(pages, list):
            raise ValueError(f"Product does not expose a pages list: {dropdown_label}")
        return pages

    raise ValueError(
        f"docs.json navigation must define dropdowns or products: {docs_json_path}"
    )


def merge_splice_group_pages(
    *, existing_pages: list[Any], generated_pages: list[Any]
) -> list[Any]:
    generated_group_labels = {
        item["group"]
        for item in generated_pages
        if isinstance(item, dict) and isinstance(item.get("group"), str)
    }
    preserved_pages = [
        item
        for item in existing_pages
        if not (isinstance(item, dict) and item.get("group") in generated_group_labels)
    ]
    return preserved_pages + generated_pages


def update_docs_navigation(
    *,
    docs_json_path: Path,
    source_config: dict[str, Any],
    families: list[dict[str, Any]],
) -> None:
    payload = load_json(docs_json_path)
    dropdown_label = source_config.get("nav_dropdown") or "API Reference"
    if not isinstance(dropdown_label, str):
        raise ValueError("nav_dropdown must be a string")
    top_level_group_label = source_config.get("top_level_group_label") or "Splice APIs"
    if not isinstance(top_level_group_label, str):
        raise ValueError("top_level_group_label must be a string")
    insert_after_group = source_config.get("insert_after_group")
    if insert_after_group is not None and not isinstance(insert_after_group, str):
        raise ValueError("insert_after_group must be a string when set")
    enabled_specs = enabled_nav_specs(source_config)
    navigation_families = filtered_families_for_navigation(
        families=families, enabled_specs=enabled_specs
    )

    pages = navigation_pages(payload, dropdown_label, docs_json_path)

    deduped_pages: list[Any] = []
    existing_top_group_pages: list[Any] | None = None
    for item in pages:
        if isinstance(item, dict) and item.get("group") == top_level_group_label:
            group_pages = item.get("pages")
            if isinstance(group_pages, list):
                existing_top_group_pages = group_pages
            continue
        deduped_pages.append(item)

    if not navigation_families:
        return

    insert_at = len(deduped_pages)
    if insert_after_group is not None:
        for index, item in enumerate(deduped_pages):
            if isinstance(item, dict) and item.get("group") == insert_after_group:
                insert_at = index + 1
                break

    generated_pages = build_splice_group_pages(
        docs_root=docs_json_path.parent, families=navigation_families
    )
    if existing_top_group_pages is not None:
        generated_pages = merge_splice_group_pages(
            existing_pages=existing_top_group_pages,
            generated_pages=generated_pages,
        )

    deduped_pages.insert(
        insert_at,
        {"group": top_level_group_label, "pages": generated_pages},
    )
    pages[:] = deduped_pages
    docs_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish configured Splice OpenAPI specs into docs-source/openapi, generate "
            "checked-in manual operation pages with release history, and wire enabled "
            "spec groups into docs.json."
        )
    )
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--docs-json", default=str(DEFAULT_DOCS_JSON))
    parser.add_argument(
        "--history-report",
        default=str(DEFAULT_HISTORY_REPORT),
        help="Write the validated normalized Splice history report to this JSON path.",
    )
    parser.add_argument(
        "--publish-version",
        help="Explicit decentralized-canton-sync release version whose OpenAPI bundle should drive the Mintlify view.",
    )
    parser.add_argument(
        "--version",
        action="append",
        help="Restrict candidate versions before selecting the publish version. Repeat to filter the set.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Refresh cached decentralized-canton-sync release bundles before publishing specs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_config = load_json(Path(args.source_config).resolve())
    include_versions = set(args.version) if args.version else None
    releases = selected_releases(
        source_config=source_config, include_versions=include_versions
    )
    publish_release = resolve_publish_release(
        source_config=source_config,
        releases=releases,
        requested_version=args.publish_version,
    )
    comparison_releases = comparison_releases_through_publish(
        releases=releases,
        publish_version=publish_release["version"],
    )
    cache_dir = Path(args.cache_dir).resolve()
    archive = ensure_archive(
        cache_dir=cache_dir,
        release=publish_release,
        force_refresh=args.force_refresh,
    )
    families = normalized_families(source_config)
    enabled_specs = enabled_nav_specs(source_config)
    validate_excluded_specs(
        source_config=source_config,
        families=families,
        enabled_specs=enabled_specs,
    )
    navigation_families = filtered_families_for_navigation(
        families=families,
        enabled_specs=enabled_specs,
    )
    spec_filenames = {
        spec["filename"] for family in families for spec in family["specs"]
    }
    spec_bytes = extract_spec_bytes(archive=archive, spec_filenames=spec_filenames)

    docs_json_path = Path(args.docs_json).resolve()
    docs_root = docs_json_path.parent
    write_managed_specs(
        docs_root=docs_root,
        source_config=source_config,
        families=families,
        spec_bytes=spec_bytes,
    )
    enabled_filenames = {
        spec["filename"] for family in navigation_families for spec in family["specs"]
    }
    snapshots = versioned_enabled_specs(
        cache_dir=cache_dir,
        releases=comparison_releases,
        spec_filenames=enabled_filenames,
        force_refresh=args.force_refresh,
    )
    history_report = build_splice_history_report(
        source_config=source_config,
        families=navigation_families,
        snapshots=snapshots,
        releases=comparison_releases,
        publish_version=publish_release["version"],
    )
    validate_history_report(history_report)
    history_report_path = Path(args.history_report).resolve()
    write_history_report(history_report_path, history_report)
    print(f"Generated normalized Splice history report: {history_report_path}")
    write_manual_operation_pages(
        docs_json_path=docs_json_path,
        families=navigation_families,
        snapshots=snapshots,
        publish_version=publish_release["version"],
        history_report=history_report,
    )
    cleanup_legacy_outputs(docs_root=docs_root, source_config=source_config)
    update_docs_navigation(
        docs_json_path=docs_json_path,
        source_config=source_config,
        families=families,
    )
    validate_splice_nav(
        source_config_path=Path(args.source_config).resolve(),
        docs_json_path=docs_json_path,
    )
    print(f"Updated docs navigation: {docs_json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
