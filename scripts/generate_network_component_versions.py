#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO_CONFIG_OUTPUT = REPO_ROOT / "config" / "repo-version-config.json"
HELPER_SCRIPT = REPO_ROOT / "scripts" / "helpers" / "updateVersionDashboardData.js"

NETWORK_ORDER = ["mainnet", "testnet", "devnet"]
RENDERED_REPOSITORY_ORDER = [
    "splice",
    "canton",
    "damlSdk",
    "dpm",
    "pqs",
    "tokenStandard",
    "walletSdk",
    "dappSdk",
    "walletGateway",
]
NETWORKS = {
    "mainnet": {
        "display_name": "MainNet",
        "info_url": "https://docs.global.canton.network.sync.global/info",
        "index_url": "https://docs.global.canton.network.sync.global/index.html",
        "endpoint": "scan.sv-1.global.canton.network.sync.global",
    },
    "testnet": {
        "display_name": "TestNet",
        "info_url": "https://docs.test.global.canton.network.sync.global/info",
        "index_url": "https://docs.test.global.canton.network.sync.global/index.html",
        "endpoint": "scan.sv-1.test.global.canton.network.sync.global",
    },
    "devnet": {
        "display_name": "DevNet",
        "info_url": "https://docs.dev.global.canton.network.sync.global/info",
        "index_url": "https://docs.dev.global.canton.network.sync.global/index.html",
        "endpoint": "scan.sv-1.dev.global.canton.network.sync.global",
    },
}
NPM_PACKAGE_NAMES = {
    "tokenStandard": "@canton-network/core-token-standard",
    "walletSdk": "@canton-network/wallet-sdk",
    "dappSdk": "@canton-network/dapp-sdk",
}
NPM_PACKAGE_URLS = {
    key: f"https://www.npmjs.com/package/{package_name}"
    for key, package_name in NPM_PACKAGE_NAMES.items()
}
DPM_RELEASE_REPO = "digital-asset/dpm"
DPM_LATEST_RELEASE_URL = f"https://api.github.com/repos/{DPM_RELEASE_REPO}/releases/latest"
DPM_RELEASES_PAGE_URL = f"https://github.com/{DPM_RELEASE_REPO}/releases"
DAML_SDK_MANIFEST_REPOSITORY = (
    "europe-docker.pkg.dev/da-images/public/sdk-manifests/open-source"
)
DAML_SDK_MANIFEST_BASE_URL = (
    "https://europe-docker.pkg.dev/v2/da-images/public/"
    "sdk-manifests/open-source/manifests"
)
DAML_SDK_VERSION_ANNOTATION = "org.opencontainers.image.version"
DAML_SDK_VENDOR_VERSION_ANNOTATION = "com.digitalasset.version"
WALLET_GATEWAY_PACKAGE_URL = (
    "https://github.com/digital-asset/wallet-gateway/pkgs/container/"
    "wallet-gateway%2Fdocker%2Fwallet-gateway"
)
SPLICE_REPOSITORY_URL = "https://github.com/canton-network/splice"
# Canton dashboard versions are pinned in nix/canton-sources.json on Splice release-line branches.
CANTON_VERSION_SOURCE_REPO_URL = SPLICE_REPOSITORY_URL
SPLICE_RAW_BASE_URL = "https://raw.githubusercontent.com/canton-network/splice"
CANTON_SOURCES_PATH = "nix/canton-sources.json"
WALLET_GATEWAY_RELEASE_REPO = "canton-network/wallet"
WALLET_GATEWAY_RELEASE_TAG_PREFIX = "@canton-network/wallet-gateway-remote@"
WALLET_GATEWAY_RELEASES_URL = (
    f"https://api.github.com/repos/{WALLET_GATEWAY_RELEASE_REPO}/releases"
)
WALLET_GATEWAY_RELEASES_PAGE_URL = (
    f"https://github.com/{WALLET_GATEWAY_RELEASE_REPO}/releases"
    "?q=wallet-gateway-remote"
)
PQS_IMAGE_REPOSITORY = (
    "europe-docker.pkg.dev/da-images/public/docker/participant-query-store"
)
PQS_SCRIBE_COMPONENT_REPOSITORY = (
    "europe-docker.pkg.dev/da-images/public/components/scribe"
)
PQS_SCRIBE_RELEASE_LINE_TAG = "3.5"
PQS_SCRIBE_MANIFEST_URL = (
    "https://europe-docker.pkg.dev/v2/da-images/public/components/"
    f"scribe/manifests/{PQS_SCRIBE_RELEASE_LINE_TAG}"
)
OCI_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
STABLE_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")
USER_AGENT = "cf-docs-version-dashboard-generator"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect public source data for the Canton Network version dashboard, "
            "update config/repo-version-config.json, and regenerate the dashboard snippet."
        )
    )
    parser.add_argument(
        "--repo-config-out",
        type=Path,
        default=DEFAULT_REPO_CONFIG_OUTPUT,
        help=f"Where to write the dashboard config. Default: {DEFAULT_REPO_CONFIG_OUTPUT}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Timeout in seconds for each HTTP request.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated config and do not write files.",
    )
    parser.add_argument(
        "--skip-helper",
        action="store_true",
        help="Write repo-version-config.json but do not regenerate the MDX snippet.",
    )
    return parser.parse_args()


def request_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and urlparse(url).netloc == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def request_url(url: str, timeout: float):
    request = Request(url, headers=request_headers(url))
    return urlopen(request, timeout=timeout)


def fetch_json(url: str, timeout: float) -> dict:
    with request_url(url, timeout) as response:
        return json.load(response)


def fetch_manifest_json(url: str, timeout: float) -> dict:
    headers = request_headers(url)
    headers["Accept"] = OCI_MANIFEST_ACCEPT
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_text(url: str, timeout: float) -> str:
    with request_url(url, timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_npm_latest(package_name: str, timeout: float) -> str:
    encoded_name = package_name.replace("/", "%2F")
    data = fetch_json(f"https://registry.npmjs.org/{encoded_name}", timeout)
    return str(data["dist-tags"]["latest"])


def daml_sdk_manifest_url(network_key: str) -> str:
    if network_key not in NETWORKS:
        raise ValueError(f"Expected Daml SDK network tag, got {network_key!r}")
    return f"{DAML_SDK_MANIFEST_BASE_URL}/{network_key}"


def fetch_daml_sdk_manifest_version(network_key: str, timeout: float) -> str:
    url = daml_sdk_manifest_url(network_key)
    data = fetch_manifest_json(url, timeout)
    if data.get("mediaType") != "application/vnd.oci.image.index.v1+json":
        raise RuntimeError(f"Expected OCI image index from {url}")

    annotations = data.get("annotations", {})
    if not isinstance(annotations, dict):
        raise RuntimeError(f"Expected manifest annotations from {url}")
    version = str(annotations.get(DAML_SDK_VERSION_ANNOTATION) or "")
    vendor_version = str(annotations.get(DAML_SDK_VENDOR_VERSION_ANNOTATION) or "")
    if not STABLE_SEMVER_RE.fullmatch(version):
        raise RuntimeError(
            f"Expected stable {DAML_SDK_VERSION_ANNOTATION} annotation from {url}, "
            f"got {version!r}"
        )
    if vendor_version and vendor_version != version:
        raise RuntimeError(
            f"Daml SDK version annotation mismatch in {url}: "
            f"{DAML_SDK_VERSION_ANNOTATION}={version} "
            f"{DAML_SDK_VENDOR_VERSION_ANNOTATION}={vendor_version}"
        )

    manifests = data.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise RuntimeError(f"Expected platform manifests from {url}")
    for manifest in manifests:
        if not isinstance(manifest, dict):
            raise RuntimeError(f"Expected platform manifest object from {url}")
        manifest_annotations = manifest.get("annotations", {})
        if not isinstance(manifest_annotations, dict):
            raise RuntimeError(f"Expected platform manifest annotations from {url}")
        platform_version = str(
            manifest_annotations.get(DAML_SDK_VERSION_ANNOTATION) or ""
        )
        platform_vendor_version = str(
            manifest_annotations.get(DAML_SDK_VENDOR_VERSION_ANNOTATION) or ""
        )
        if platform_version != version:
            raise RuntimeError(
                f"Daml SDK platform version mismatch in {url}: "
                f"index={version} platform={platform_version!r}"
            )
        if platform_vendor_version and platform_vendor_version != version:
            raise RuntimeError(
                f"Daml SDK platform vendor version mismatch in {url}: "
                f"index={version} platform={platform_vendor_version!r}"
            )

    return version


def collect_daml_sdk_versions(timeout: float, existing_config: dict) -> dict[str, str]:
    versions: dict[str, str] = {}
    for network_key in NETWORK_ORDER:
        try:
            versions[network_key] = fetch_daml_sdk_manifest_version(network_key, timeout)
        except Exception as exc:
            previous_version = existing_repo_version(
                existing_config,
                "damlSdk",
                network_key,
            )
            if not STABLE_SEMVER_RE.fullmatch(previous_version):
                raise RuntimeError(
                    f"{network_key}: failed to collect Daml SDK version and no previous "
                    f"stable dashboard value is available to preserve: {exc}"
                ) from exc
            print(
                f"WARNING: {network_key}: failed to collect Daml SDK version ({exc}); "
                f"preserving previous dashboard value {previous_version}",
                file=sys.stderr,
            )
            versions[network_key] = previous_version
    return versions


def version_key(version: str) -> tuple[int, int, int]:
    if not STABLE_SEMVER_RE.fullmatch(version):
        raise ValueError(f"Expected stable semantic version, got {version!r}")
    return tuple(int(part) for part in version.split("."))


def latest_stable_version(versions: list[str], source: str) -> str:
    stable_versions = sorted(
        {version for version in versions if STABLE_SEMVER_RE.fullmatch(version)},
        key=version_key,
    )
    if not stable_versions:
        raise RuntimeError(f"No stable semantic versions found in {source}")
    return stable_versions[-1]


def splice_release_line_branch(release_version: str) -> str:
    if not STABLE_SEMVER_RE.fullmatch(release_version):
        raise RuntimeError(
            f"Cannot derive Splice release-line branch from version {release_version!r}"
        )
    return f"release-line-{release_version}"


def splice_raw_file_url(branch: str, path: str) -> str:
    return f"{SPLICE_RAW_BASE_URL}/{branch}/{path}"


def splice_blob_file_url(branch: str, path: str) -> str:
    return f"{SPLICE_REPOSITORY_URL}/blob/{branch}/{path}"


def fetch_canton_version_from_splice_release_line(
    splice_version: str,
    timeout: float,
) -> tuple[str, str, str]:
    branch = splice_release_line_branch(splice_version)
    url = splice_raw_file_url(branch, CANTON_SOURCES_PATH)
    data = fetch_json(url, timeout)
    canton_version = str(data.get("version") or "")
    if not canton_version:
        raise RuntimeError(f"Missing version in {url}")
    return canton_version, branch, splice_blob_file_url(branch, CANTON_SOURCES_PATH)


def fetch_latest_dpm_version(timeout: float) -> str:
    data = fetch_json(DPM_LATEST_RELEASE_URL, timeout)
    if data.get("prerelease"):
        raise RuntimeError(f"Latest GitHub release at {DPM_LATEST_RELEASE_URL} is a prerelease")
    tag_name = data.get("tag_name")
    if not isinstance(tag_name, str) or not STABLE_SEMVER_RE.fullmatch(tag_name):
        raise RuntimeError(f"Expected stable tag_name from {DPM_LATEST_RELEASE_URL}")
    return tag_name


def fetch_latest_wallet_gateway_version(timeout: float) -> str:
    versions: list[str] = []
    tag_re = re.compile(
        rf"^{re.escape(WALLET_GATEWAY_RELEASE_TAG_PREFIX)}(?P<version>\d+\.\d+\.\d+)$"
    )
    for page in range(1, 11):
        data = fetch_json(f"{WALLET_GATEWAY_RELEASES_URL}?per_page=100&page={page}", timeout)
        if not isinstance(data, list):
            raise RuntimeError(f"Expected release list from {WALLET_GATEWAY_RELEASES_URL}")
        if not data:
            break
        for release in data:
            if not isinstance(release, dict):
                continue
            tag_name = release.get("tag_name")
            if not isinstance(tag_name, str):
                continue
            match = tag_re.fullmatch(tag_name)
            if match:
                versions.append(match.group("version"))
    return latest_stable_version(versions, WALLET_GATEWAY_RELEASES_URL)


def previous_stable_pqs_version(existing_config: dict) -> str:
    versions = [
        existing_repo_version(existing_config, "pqs", network_key)
        for network_key in NETWORK_ORDER
    ]
    return latest_stable_version(versions, "existing PQS dashboard config")


def fetch_pqs_version_from_scribe_component(
    timeout: float,
    *,
    previous_stable_version: str,
) -> str:
    data = fetch_manifest_json(PQS_SCRIBE_MANIFEST_URL, timeout)
    annotations = data.get("annotations", {})
    if not isinstance(annotations, dict):
        raise RuntimeError(f"Expected manifest annotations from {PQS_SCRIBE_MANIFEST_URL}")
    version = str(
        annotations.get("org.opencontainers.image.version")
        or annotations.get("com.digitalasset.version")
        or ""
    )
    if not version:
        raise RuntimeError(f"Missing Scribe image version annotation in {PQS_SCRIBE_MANIFEST_URL}")
    if STABLE_SEMVER_RE.fullmatch(version):
        return version
    return previous_stable_version


def clean_html_text(value: str) -> str:
    value = re.sub(r"<.*?>", "", value)
    return " ".join(html.unescape(value).split())


def parse_table_pairs(page_html: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in re.findall(r'<tr class="row-(?:odd|even)">(.*?)</tr>', page_html, re.S):
        columns = re.findall(r"<td><p>(.*?)</p></td>", row, re.S)
        if len(columns) < 2:
            continue
        key = clean_html_text(columns[0])
        value = clean_html_text(columns[1])
        if key:
            pairs[key] = value
    return pairs


def require_value(pairs: dict[str, str], label: str, url: str) -> str:
    try:
        return pairs[label]
    except KeyError as exc:
        raise RuntimeError(f"Could not find table row {label!r} in {url}") from exc


def choose_observed_release(
    info_payload: dict,
    info_url: str,
    *,
    index_version: str | None = None,
) -> str:
    sv = info_payload.get("sv", {})
    synchronizers = info_payload.get("synchronizer", {})
    synchronizer_label = "active"
    synchronizer = synchronizers.get(synchronizer_label, {})
    if not synchronizer:
        synchronizer_label = "current"
        synchronizer = synchronizers.get(synchronizer_label, {})
    sv_version = sv.get("version")
    sync_version = synchronizer.get("version")
    sv_migration_id = sv.get("migration_id")
    sync_migration_id = synchronizer.get("migration_id")

    if not sv_version or not sync_version:
        raise RuntimeError(f"Missing release version in {info_url}")
    if sv_version != sync_version:
        observed_version = resolve_mismatched_info_version(
            sv_version=str(sv_version),
            sync_version=str(sync_version),
            synchronizer_label=synchronizer_label,
            info_url=info_url,
            index_version=index_version,
        )
    else:
        observed_version = str(sv_version)
    if sv_migration_id is None:
        raise RuntimeError(f"Missing sv.migration_id in {info_url}")
    if sync_migration_id is None and synchronizer_label == "active":
        raise RuntimeError(f"Missing synchronizer.active.migration_id in {info_url}")
    if sync_migration_id is not None and str(sv_migration_id) != str(sync_migration_id):
        raise RuntimeError(
            f"Migration mismatch in {info_url}: "
            f"sv.migration_id={sv_migration_id} "
            f"synchronizer.{synchronizer_label}.migration_id={sync_migration_id}"
        )
    return observed_version


def resolve_mismatched_info_version(
    *,
    sv_version: str,
    sync_version: str,
    synchronizer_label: str,
    info_url: str,
    index_version: str | None,
) -> str:
    """Pick an /info version during upgrade windows using the docs index as authority."""
    mismatch = (
        f"Version mismatch in {info_url}: "
        f"sv.version={sv_version} synchronizer.{synchronizer_label}.version={sync_version}"
    )
    if index_version is None:
        raise RuntimeError(mismatch)
    if sv_version == index_version:
        print(
            f"WARNING: {mismatch}; using sv.version={sv_version} because it matches "
            f"docs index version {index_version}",
            file=sys.stderr,
        )
        return sv_version
    if sync_version == index_version:
        print(
            f"WARNING: {mismatch}; using synchronizer.{synchronizer_label}.version="
            f"{sync_version} because it matches docs index version {index_version}",
            file=sys.stderr,
        )
        return sync_version
    raise RuntimeError(
        f"{mismatch}; neither matches docs index version {index_version}"
    )


def read_existing_config(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"versions": {}, "repositories": {}}


def existing_network(existing_config: dict, network_key: str) -> dict:
    return dict(existing_config.get("versions", {}).get(network_key, {}))


def existing_repo_version(existing_config: dict, repository_key: str, network_key: str) -> str:
    repository = existing_config.get("repositories", {}).get(repository_key, {})
    mapping = repository.get("versionMapping", {}).get(network_key, {})
    return str(mapping.get("externalVersion") or "")


def updated_release_url(release_version: str) -> str:
    return f"https://github.com/canton-network/splice/releases/tag/{release_version}"


def update_substitutions(existing: dict, release_version: str) -> dict:
    substitutions = dict(existing)
    substitutions.update(
        {
            "version": release_version,
            "version_literal": release_version,
            "chart_version_literal": release_version,
            "chart_version_set": f"export CHART_VERSION={release_version}",
            "image_tag_set": f"export IMAGE_TAG={release_version}",
            "image_tag_set_plain": f"export IMAGE_TAG={release_version}",
        }
    )
    substitutions["bundle_download_link"] = {
        "label": "Download Bundle",
        "href": (
            "https://github.com/digital-asset/decentralized-canton-sync/releases/download/"
            f"v{release_version}/{release_version}_splice-node.tar.gz"
        ),
    }
    substitutions["openapi_download_link"] = {
        "label": "Download OpenAPI specs",
        "href": (
            "https://github.com/digital-asset/decentralized-canton-sync/releases/download/"
            f"v{release_version}/{release_version}_openapi.tar.gz"
        ),
    }
    return substitutions


def collect_network_snapshot(network_key: str, timeout: float) -> dict:
    urls = NETWORKS[network_key]
    index_pairs = parse_table_pairs(fetch_text(urls["index_url"], timeout))
    docker_image_tag = require_value(index_pairs, "Docker Image Tag:", urls["index_url"])
    helm_chart_version = require_value(index_pairs, "Helm Chart Version:", urls["index_url"])
    if docker_image_tag != helm_chart_version:
        raise RuntimeError(
            f"{network_key}: index page mismatch docker_image_tag={docker_image_tag} "
            f"helm_chart_version={helm_chart_version}"
        )

    info_payload = fetch_json(urls["info_url"], timeout)
    observed_release = choose_observed_release(
        info_payload,
        urls["info_url"],
        index_version=docker_image_tag,
    )
    if docker_image_tag != observed_release:
        raise RuntimeError(
            f"{network_key}: /info version {observed_release} does not match "
            f"docs index version {docker_image_tag}"
        )
    canton_version, canton_release_line_branch, canton_sources_url = (
        fetch_canton_version_from_splice_release_line(observed_release, timeout)
    )

    return {
        "displayName": urls["display_name"],
        "endpoint": urls["endpoint"],
        "spliceVersion": observed_release,
        "cantonVersion": canton_version,
        "cantonReleaseLineBranch": canton_release_line_branch,
        "sources": {
            "infoUrl": urls["info_url"],
            "indexUrl": urls["index_url"],
            "cantonSourcesUrl": canton_sources_url,
        },
        "checks": {
            "dockerImageTag": docker_image_tag,
            "helmChartVersion": helm_chart_version,
        },
    }


def network_snapshot_from_existing(existing_config: dict, network_key: str) -> dict | None:
    """Rebuild a network snapshot from the previous dashboard config, if available."""
    existing = existing_network(existing_config, network_key)
    if not existing:
        return None

    urls = NETWORKS[network_key]
    substitutions = existing.get("substitutions", {})
    splice_version = str(
        substitutions.get("version")
        or existing_repo_version(existing_config, "splice", network_key)
        or ""
    )
    canton_mapping = (
        existing_config.get("repositories", {})
        .get("canton", {})
        .get("versionMapping", {})
        .get(network_key, {})
    )
    canton_version = str(canton_mapping.get("externalVersion") or "")
    canton_release_line_branch = str(canton_mapping.get("branch") or "")
    if not splice_version:
        return None

    existing_sources = (
        existing_config.get("_generated", {}).get("networkSources", {}).get(network_key, {})
    )
    sources = {
        "infoUrl": existing_sources.get("infoUrl", urls["info_url"]),
        "indexUrl": existing_sources.get("indexUrl", urls["index_url"]),
        "cantonSourcesUrl": existing_sources.get("cantonSourcesUrl", ""),
        "preservedFromPrevious": True,
    }
    return {
        "displayName": existing.get("name") or urls["display_name"],
        "endpoint": existing.get("endpoint") or urls["endpoint"],
        "spliceVersion": splice_version,
        "cantonVersion": canton_version,
        "cantonReleaseLineBranch": canton_release_line_branch,
        "sources": sources,
        "checks": {
            "dockerImageTag": splice_version,
            "helmChartVersion": splice_version,
        },
        "preservedFromPrevious": True,
    }


def collect_snapshot(timeout: float, existing_config: dict) -> dict:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    previous_pqs = previous_stable_pqs_version(existing_config)
    networks: dict[str, dict] = {}
    for network_key in NETWORK_ORDER:
        try:
            networks[network_key] = collect_network_snapshot(network_key, timeout)
        except Exception as exc:
            preserved = network_snapshot_from_existing(existing_config, network_key)
            if preserved is None:
                raise RuntimeError(
                    f"{network_key}: failed to collect network snapshot and no previous "
                    f"dashboard config is available to preserve: {exc}"
                ) from exc
            print(
                f"WARNING: {network_key}: failed to collect network snapshot "
                f"({exc}); preserving previous dashboard values "
                f"(splice={preserved['spliceVersion']})",
                file=sys.stderr,
            )
            preserved["sources"]["preserveReason"] = str(exc)
            networks[network_key] = preserved
    return {
        "generatedAt": generated_at,
        "generatorMode": "public_source_collection_with_manual_fallbacks",
        "networks": networks,
        "damlSdkVersions": collect_daml_sdk_versions(timeout, existing_config),
        "latestDpm": fetch_latest_dpm_version(timeout),
        "latestPqs": fetch_pqs_version_from_scribe_component(
            timeout,
            previous_stable_version=previous_pqs,
        ),
        "latestWalletGateway": fetch_latest_wallet_gateway_version(timeout),
        "npmVersions": {
            key: fetch_npm_latest(package_name, timeout)
            for key, package_name in NPM_PACKAGE_NAMES.items()
        },
    }


def build_versions(existing_config: dict, snapshot: dict) -> dict:
    versions: dict[str, dict] = {}
    for network_key in NETWORK_ORDER:
        existing = existing_network(existing_config, network_key)
        existing_substitutions = existing.get("substitutions", {})
        network = snapshot["networks"][network_key]

        versions[network_key] = {
            "name": network["displayName"],
            "advanced": {
                "releaseUrl": updated_release_url(network["spliceVersion"]),
            },
            "endpoint": network["endpoint"],
            "substitutions": update_substitutions(
                existing_substitutions,
                network["spliceVersion"],
            ),
        }
    return versions


def repository_url(repository_key: str, existing_config: dict) -> str:
    existing = existing_config.get("repositories", {}).get(repository_key, {})
    if repository_key == "splice":
        return "https://github.com/canton-network/splice/releases"
    if repository_key == "canton":
        return CANTON_VERSION_SOURCE_REPO_URL
    if repository_key == "damlSdk":
        return f"https://{DAML_SDK_MANIFEST_REPOSITORY}"
    if repository_key == "dpm":
        return DPM_RELEASES_PAGE_URL
    if repository_key == "pqs":
        return f"https://{PQS_SCRIBE_COMPONENT_REPOSITORY}"
    if repository_key == "walletGateway":
        return WALLET_GATEWAY_RELEASES_PAGE_URL
    if repository_key in NPM_PACKAGE_URLS:
        return NPM_PACKAGE_URLS[repository_key]
    return str(existing.get("url") or "")


def build_repository_mapping(
    repository_key: str,
    existing_config: dict,
    snapshot: dict,
) -> dict[str, dict[str, str]]:
    version_mapping: dict[str, dict[str, str]] = {}
    for network_key in NETWORK_ORDER:
        network = snapshot["networks"][network_key]
        if repository_key == "splice":
            external_version = network["spliceVersion"]
            branch = "main"
            folder_path_repo = "splice-wallet-kernel"
        elif repository_key == "canton":
            external_version = network["cantonVersion"]
            branch = network["cantonReleaseLineBranch"]
            folder_path_repo = CANTON_SOURCES_PATH
        elif repository_key == "damlSdk":
            external_version = snapshot["damlSdkVersions"][network_key]
            branch = ""
            folder_path_repo = ""
        elif repository_key == "dpm":
            external_version = snapshot["latestDpm"]
            branch = ""
            folder_path_repo = ""
        elif repository_key == "pqs":
            external_version = snapshot["latestPqs"]
            branch = ""
            folder_path_repo = f"{PQS_SCRIBE_COMPONENT_REPOSITORY}:{PQS_SCRIBE_RELEASE_LINE_TAG}"
        elif repository_key in NPM_PACKAGE_NAMES:
            external_version = snapshot["npmVersions"][repository_key]
            branch = ""
            folder_path_repo = ""
        elif repository_key == "walletGateway":
            external_version = snapshot["latestWalletGateway"]
            branch = ""
            folder_path_repo = WALLET_GATEWAY_RELEASE_TAG_PREFIX.rstrip("@")
        else:
            external_version = existing_repo_version(existing_config, repository_key, network_key)
            branch = ""
            folder_path_repo = ""

        version_mapping[network_key] = {
            "branch": branch,
            "externalVersion": external_version,
            "folderPathRepo": folder_path_repo,
        }
    return version_mapping


def build_repositories(existing_config: dict, snapshot: dict) -> dict:
    repositories: dict[str, dict] = {}
    existing_repositories = existing_config.get("repositories", {})
    repository_order = list(RENDERED_REPOSITORY_ORDER)
    for key in existing_repositories:
        if key not in repository_order:
            repository_order.append(key)

    for repository_key in repository_order:
        repositories[repository_key] = {
            "url": repository_url(repository_key, existing_config),
            "versionMapping": build_repository_mapping(
                repository_key,
                existing_config,
                snapshot,
            ),
        }
    return repositories


def build_source_contract(snapshot: dict) -> dict:
    return {
        "splice": (
            "Network /info endpoint: MainNet "
            "https://docs.global.canton.network.sync.global/info, TestNet "
            "https://docs.test.global.canton.network.sync.global/info, DevNet "
            "https://docs.dev.global.canton.network.sync.global/info. Cross-check against "
            "the same network's /index.html Docker image tag and Helm chart version."
        ),
        "canton": (
            "Use the observed Splice version from the network /info endpoint, derive the "
            "matching canton-network/splice release-line branch, then read version from "
            "nix/canton-sources.json."
        ),
        "damlSdk": (
            "For each network, read its moving mainnet, testnet, or devnet tag from "
            f"{DAML_SDK_MANIFEST_BASE_URL}. Read {DAML_SDK_VERSION_ANNOTATION} from that "
            "public Artifact Registry OCI index and cross-check the Digital Asset and "
            "per-platform version annotations. Preserve only that network's previous stable "
            "dashboard value if its registry manifest is temporarily unavailable."
        ),
        "dpm": (
            f"Latest stable dpm CLI release tag from {DPM_LATEST_RELEASE_URL} "
            f"(currently {snapshot['latestDpm']})."
        ),
        "tokenStandard": f"npm latest dist-tag for {NPM_PACKAGE_NAMES['tokenStandard']}.",
        "walletSdk": f"npm latest dist-tag for {NPM_PACKAGE_NAMES['walletSdk']}.",
        "dappSdk": f"npm latest dist-tag for {NPM_PACKAGE_NAMES['dappSdk']}.",
        "walletGateway": (
            "Latest stable @canton-network/wallet-gateway-remote GitHub release from "
            f"{WALLET_GATEWAY_RELEASE_REPO}."
        ),
        "pqs": (
            "Read org.opencontainers.image.version from the public Artifact Registry "
            f"Scribe component image {PQS_SCRIBE_COMPONENT_REPOSITORY}:"
            f"{PQS_SCRIBE_RELEASE_LINE_TAG}. If the floating release-line tag resolves "
            "to a prerelease, retain the previous stable dashboard value."
        ),
    }


def build_config(existing_config: dict, snapshot: dict) -> dict:
    config = {
        "_generated": {
            "generatedAt": snapshot["generatedAt"],
            "generatorMode": snapshot["generatorMode"],
            "sourceContract": build_source_contract(snapshot),
            "networkSources": {
                key: network["sources"] for key, network in snapshot["networks"].items()
            },
        },
        "versions": build_versions(existing_config, snapshot),
        "repositories": build_repositories(existing_config, snapshot),
    }
    existing_generated = existing_config.get("_generated", {})
    if (
        isinstance(existing_generated, dict)
        and existing_generated
        and dashboard_content(config) == dashboard_content(existing_config)
    ):
        config["_generated"] = json.loads(json.dumps(existing_generated))
    return config


def dashboard_content(config: dict) -> dict:
    return {
        "versions": config.get("versions"),
        "repositories": config.get("repositories"),
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_helper() -> None:
    subprocess.run(["node", str(HELPER_SCRIPT)], cwd=REPO_ROOT, check=True)


def main() -> int:
    args = parse_args()
    existing_config = read_existing_config(DEFAULT_REPO_CONFIG_OUTPUT)
    snapshot = collect_snapshot(args.timeout, existing_config)
    repo_version_config = build_config(existing_config, snapshot)

    if args.dry_run:
        json.dump(repo_version_config, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    write_json(args.repo_config_out, repo_version_config)
    if not args.skip_helper:
        run_helper()

    print(f"Wrote dashboard config to {args.repo_config_out}")
    if args.skip_helper:
        print("Skipped dashboard snippet regeneration.")
    else:
        print(f"Regenerated snippet with {HELPER_SCRIPT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
