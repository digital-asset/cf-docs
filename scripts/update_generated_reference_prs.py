#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import generated_reference_pr_utils as pr_utils
import summarize_version_changes

REPO_ROOT = Path(__file__).resolve().parents[1]
NETWORK_VARIABLE_TAB_PAGES = (
    "docs-main/appdev/deep-dives/token-standard.mdx",
    "docs-main/global-synchronizer/deployment/kubernetes-deployment.mdx",
    "docs-main/global-synchronizer/deployment/onboarding-process.mdx",
    "docs-main/global-synchronizer/deployment/required-network-parameters.mdx",
    "docs-main/global-synchronizer/deployment/sv-network-resets.mdx",
    "docs-main/global-synchronizer/deployment/synchronizer-traffic.mdx",
    "docs-main/global-synchronizer/deployment/validator-docker-compose.mdx",
    "docs-main/global-synchronizer/deployment/validator-kubernetes.mdx",
    "docs-main/global-synchronizer/production-operations/validator-disaster-recovery.mdx",
    "docs-main/global-synchronizer/reference/canton-console-reference.mdx",
    "docs-main/sdks-tools/api-reference/splice-daml-apis.mdx",
    "docs-main/sdks-tools/api-reference/splice-http-apis.mdx",
    "docs-main/sdks-tools/api-reference/splice-scan-bulk-data-api.mdx",
    "docs-main/sdks-tools/api-reference/splice-scan-gs-connectivity-api.mdx",
)


@dataclass(frozen=True)
class UpdateTarget:
    key: str
    title: str
    branch: str
    description: str
    generate_commands: tuple[tuple[str, ...], ...]
    paths: tuple[str, ...]
    summary_kind: str
    summary_path: str | None
    summary_label: str | None
    validation: tuple[str, ...]
    source_update_commands: tuple[tuple[str, ...], ...] = ()
    source_update_paths: tuple[str, ...] = ()
    require_summary_changes: bool = False
    auto_merge: bool = True
    requires_daml_tooling: bool = False


UPDATE_TARGETS = (
    UpdateTarget(
        key="version-dashboard",
        title="Update generated docs",
        branch="version-dashboard/update",
        description=(
            "Updates the committed Canton Network version dashboard data from public network, "
            "package, and installer sources, then refreshes generated pages that render "
            "network-specific values from that data."
        ),
        generate_commands=(("nix-shell", "--run", "npm run generate:network-variable-tabs"),),
        paths=(
            "config/repo-version-config.json",
            "docs-main/snippets/generated/version-dashboard-data.mdx",
            *NETWORK_VARIABLE_TAB_PAGES,
        ),
        summary_kind="dashboard",
        summary_path="config/repo-version-config.json",
        summary_label=None,
        validation=(
            "npm run generate:version-compatibility-dashboard",
            "npm run generate:network-variable-tabs",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run generate:version-compatibility-dashboard"),
        ),
        source_update_paths=(
            "config/repo-version-config.json",
            "docs-main/snippets/generated/version-dashboard-data.mdx",
        ),
        require_summary_changes=True,
    ),
    UpdateTarget(
        key="splice-openapi",
        title="Update Splice OpenAPI reference",
        branch="generated-references/splice-openapi/update",
        description=(
            "Discovers every eligible stable decentralized-canton-sync release, "
            "publishes the latest Splice OpenAPI bundle, and regenerates the checked-in "
            "specifications, operation pages, history, and navigation."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:splice-mintlify-openapi"),
        ),
        paths=(
            "docs-main/docs.json",
            "docs-main/openapi/splice",
            "docs-main/reference/splice-allocation-api",
            "docs-main/reference/splice-allocation-instruction-api",
            "docs-main/reference/splice-allocation-instruction-v2-api",
            "docs-main/reference/splice-allocation-v2-api",
            "docs-main/reference/splice-ans-api",
            "docs-main/reference/splice-scan-api",
            "docs-main/reference/splice-scan-proxy-api",
            "docs-main/reference/splice-scan-streaming-api",
            "docs-main/reference/splice-token-metadata-service",
            "docs-main/reference/splice-transfer-instruction-api",
            "docs-main/reference/splice-transfer-instruction-v2-api",
            "docs-main/reference/splice-wallet-api-external",
        ),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run generate:splice-mintlify-openapi",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="splice-token-standard-v2",
        title="Update Token Standard v2 Daml reference",
        branch="generated-references/splice-token-standard-v2/update",
        description=(
            "Regenerates the checked-in Canton Network Token Standard v2 Daml package "
            "reference pages and normalized history from every eligible stable "
            "canton-network/splice release tag."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:splice-token-standard-v2-reference"),
        ),
        paths=(
            "config/x2mdx/splice-token-standard-v2/source-artifacts.json",
            "config/x2mdx/splice-token-standard-v2/lifecycle.json",
            "docs-main/docs.json",
            "docs-main/sdks-tools/api-reference/splice-daml/token-standard-v2-history-report.json",
            "docs-main/sdks-tools/api-reference/splice-daml/splice-api-token-allocation-instruction-v2",
            "docs-main/sdks-tools/api-reference/splice-daml/splice-api-token-allocation-request-v2",
            "docs-main/sdks-tools/api-reference/splice-daml/splice-api-token-allocation-v2",
            "docs-main/sdks-tools/api-reference/splice-daml/splice-api-token-holding-v2",
            "docs-main/sdks-tools/api-reference/splice-daml/splice-api-token-transfer-events-v2",
            "docs-main/sdks-tools/api-reference/splice-daml/splice-api-token-transfer-instruction-v2",
        ),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run generate:splice-token-standard-v2-reference",
            "git diff --check",
        ),
        requires_daml_tooling=True,
    ),
    UpdateTarget(
        key="wallet-gateway-openrpc",
        title="Update Wallet Gateway OpenRPC reference",
        branch="generated-references/wallet-gateway-openrpc/update",
        description=(
            "Updates the Wallet Gateway OpenRPC source pin to the latest stable "
            "wallet-gateway-remote release and regenerates the checked-in Wallet Gateway "
            "OpenRPC reference pages."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:wallet-gateway-openrpc-reference"),
        ),
        paths=(
            "config/x2mdx/wallet-gateway-openrpc/source-artifacts.json",
            "docs-main/docs.json",
            "docs-main/reference/wallet-gateway-json-rpc",
        ),
        summary_kind="source-config",
        summary_path="config/x2mdx/wallet-gateway-openrpc/source-artifacts.json",
        summary_label="Wallet Gateway OpenRPC",
        validation=(
            "npm run update:generated-reference-sources -- --source wallet-gateway-openrpc",
            "npm run generate:wallet-gateway-openrpc-reference",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source wallet-gateway-openrpc"),
        ),
        source_update_paths=("config/x2mdx/wallet-gateway-openrpc/source-artifacts.json",),
    ),
    UpdateTarget(
        key="json-api-reference",
        title="Update JSON Ledger API OpenAPI reference",
        branch="generated-references/json-api-reference/update",
        description=(
            "Updates the JSON Ledger API OpenAPI source pin to the latest public "
            "Canton release bundle for the published docs version and regenerates "
            "the checked-in OpenAPI reference."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:json-api-reference"),
        ),
        paths=(
            "config/x2mdx/ledger-api/source-artifacts.json",
            "docs-main/docs.json",
            "docs-main/openapi/json-ledger-api",
            "docs-main/reference/json-api-reference",
        ),
        summary_kind="versioned-source-config",
        summary_path="config/x2mdx/ledger-api/source-artifacts.json",
        summary_label="JSON Ledger API OpenAPI",
        validation=(
            "npm run update:generated-reference-sources -- --source ledger-api",
            "npm run generate:json-api-reference",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source ledger-api"),
        ),
        source_update_paths=("config/x2mdx/ledger-api/source-artifacts.json",),
    ),
    UpdateTarget(
        key="json-api-asyncapi-reference",
        title="Update JSON Ledger API AsyncAPI reference",
        branch="generated-references/json-api-asyncapi-reference/update",
        description=(
            "Updates the JSON Ledger API AsyncAPI source pin to the latest public "
            "Canton release bundle for the published docs version and regenerates "
            "the checked-in AsyncAPI reference."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:json-api-asyncapi-reference"),
        ),
        paths=(
            "config/x2mdx/ledger-api-asyncapi/source-artifacts.json",
            "docs-main/docs.json",
            "docs-main/reference/json-api-asyncapi-reference",
        ),
        summary_kind="versioned-source-config",
        summary_path="config/x2mdx/ledger-api-asyncapi/source-artifacts.json",
        summary_label="JSON Ledger API AsyncAPI",
        validation=(
            "npm run update:generated-reference-sources -- --source ledger-api-asyncapi",
            "npm run generate:json-api-asyncapi-reference",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source ledger-api-asyncapi"),
        ),
        source_update_paths=("config/x2mdx/ledger-api-asyncapi/source-artifacts.json",),
    ),
    UpdateTarget(
        key="grpc-ledger-api-reference",
        title="Update gRPC Ledger API reference",
        branch="generated-references/grpc-ledger-api-reference/update",
        description=(
            "Regenerates the checked-in gRPC Ledger API reference from the latest "
            "stable Canton protobuf release bundles selected by the existing source config."
        ),
        generate_commands=(("nix-shell", "--run", "npm run generate:grpc-ledger-api-reference"),),
        paths=(
            "docs-main/docs.json",
            "docs-main/reference/grpc-ledger-api-reference",
        ),
        summary_kind="source-config",
        summary_path="config/x2mdx/grpc-ledger-api-reference/source-artifacts.json",
        summary_label="gRPC Ledger API",
        validation=(
            "npm run generate:grpc-ledger-api-reference",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="canton-protobuf-history",
        title="Update Canton protobuf history reference",
        branch="generated-references/canton-protobuf-history/update",
        description=(
            "Regenerates the checked-in Canton protobuf history references from the "
            "latest stable Canton protobuf release bundles selected by the existing source config."
        ),
        generate_commands=(("nix-shell", "--run", "npm run generate:canton-protobuf-history"),),
        paths=(
            "config/x2mdx/protobuf-history/metadata.json",
            "docs-main/docs.json",
            "docs-main/appdev/reference/protobuf-history",
            "docs-main/reference/admin-api/protobuf",
            "docs-main/reference/protobuf",
        ),
        summary_kind="source-config",
        summary_path="config/x2mdx/protobuf-history/source-artifacts.json",
        summary_label="Canton protobuf history",
        validation=(
            "npm run generate:canton-protobuf-history",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="ledger-bindings",
        title="Update Java ledger bindings reference",
        branch="generated-references/ledger-bindings/update",
        description=(
            "Updates the Java ledger bindings source pins to the latest stable "
            "Maven artifacts and regenerates the checked-in Java bindings reference pages."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:ledger-bindings-api-reference"),
        ),
        paths=(
            "config/x2mdx/ledger-bindings/source-artifacts.json",
            "docs-main/docs.json",
            "docs-main/reference/java-bindings.mdx",
            "docs-main/reference/java",
        ),
        summary_kind="artifact-source-config",
        summary_path="config/x2mdx/ledger-bindings/source-artifacts.json",
        summary_label="Java ledger bindings",
        validation=(
            "npm run update:generated-reference-sources -- --source ledger-bindings",
            "npm run generate:ledger-bindings-api-reference",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source ledger-bindings"),
        ),
        source_update_paths=("config/x2mdx/ledger-bindings/source-artifacts.json",),
        auto_merge=False,
    ),
    UpdateTarget(
        key="daml-standard-library",
        title="Update Daml Standard Library reference",
        branch="generated-references/daml-standard-library/update",
        description=(
            "Updates the Daml Standard Library source pin to the latest DPM SDK "
            "version and regenerates the checked-in Daml Standard Library reference pages."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:daml-standard-library-reference"),
        ),
        paths=(
            "config/x2mdx/daml-standard-library/source-artifacts.json",
            "docs-main/docs.json",
            "docs-main/appdev/reference/daml-standard-library",
        ),
        summary_kind="source-config",
        summary_path="config/x2mdx/daml-standard-library/source-artifacts.json",
        summary_label="Daml Standard Library",
        validation=(
            "npm run update:generated-reference-sources -- --source daml-standard-library",
            "npm run generate:daml-standard-library-reference",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source daml-standard-library"),
        ),
        source_update_paths=("config/x2mdx/daml-standard-library/source-artifacts.json",),
        requires_daml_tooling=True,
    ),
    UpdateTarget(
        key="daml-script",
        title="Update Daml Script reference",
        branch="generated-references/daml-script/update",
        description=(
            "Updates the Daml Script source pin to the latest DPM SDK version and "
            "regenerates the checked-in Daml Script reference pages."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:daml-script-reference"),
        ),
        paths=(
            "config/x2mdx/daml-script/source-artifacts.json",
            "docs-main/docs.json",
            "docs-main/appdev/reference/daml-script",
        ),
        summary_kind="source-config",
        summary_path="config/x2mdx/daml-script/source-artifacts.json",
        summary_label="Daml Script",
        validation=(
            "npm run update:generated-reference-sources -- --source daml-script",
            "npm run generate:daml-script-reference",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source daml-script"),
        ),
        source_update_paths=("config/x2mdx/daml-script/source-artifacts.json",),
        requires_daml_tooling=True,
    ),
    UpdateTarget(
        key="typescript-bindings",
        title="Update TypeScript bindings reference",
        branch="generated-references/typescript-bindings/update",
        description=(
            "Updates the TypeScript bindings source pins to the latest stable npm "
            "releases and regenerates the checked-in TypeScript bindings reference pages."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:typescript-bindings-reference"),
        ),
        paths=(
            "config/x2mdx/typescript-bindings/source-artifacts.json",
            "docs-main/docs.json",
            "docs-main/reference/typescript.mdx",
            "docs-main/reference/typescript",
        ),
        summary_kind="package-source-config",
        summary_path="config/x2mdx/typescript-bindings/source-artifacts.json",
        summary_label="TypeScript bindings",
        validation=(
            "npm run update:generated-reference-sources -- --source typescript-bindings",
            "npm run generate:typescript-bindings-reference",
            "git diff --check",
        ),
        source_update_commands=(
            ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source typescript-bindings"),
        ),
        source_update_paths=("config/x2mdx/typescript-bindings/source-artifacts.json",),
    ),
    UpdateTarget(
        key="canton-console-reference",
        title="Update Canton console command reference",
        branch="generated-docs/canton-console-reference/update",
        description=(
            "Regenerates the checked-in Canton console command reference from runtime "
            "help metadata in the latest public Canton release binary."
        ),
        generate_commands=(("nix-shell", "--run", "npm run generate:canton-console-reference"),),
        paths=("docs-main/global-synchronizer/reference/canton-console-commands.mdx",),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run generate:canton-console-reference",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="canton-error-codes-reference",
        title="Update Canton error-code reference",
        branch="generated-docs/canton-error-codes-reference/update",
        description=(
            "Regenerates the checked-in Canton error-code inventory from runtime "
            "metadata in the latest public Canton release binary."
        ),
        generate_commands=(("nix-shell", "--run", "npm run generate:canton-error-codes-reference"),),
        paths=("docs-main/global-synchronizer/reference/error-codes.mdx",),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run generate:canton-error-codes-reference",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="canton-release-protocol-versions",
        title="Update Canton release/protocol compatibility",
        branch="generated-docs/canton-release-protocol-versions/update",
        description=(
            "Regenerates the Canton release-to-protocol compatibility table from "
            "runtime metadata in the latest public Canton release binary."
        ),
        generate_commands=(("nix-shell", "--run", "npm run generate:canton-release-protocol-versions"),),
        paths=("docs-main/release-notes/releases-and-versioning.mdx",),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run generate:canton-release-protocol-versions",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="canton-metrics-reference",
        title="Update Canton metrics reference",
        branch="generated-docs/canton-metrics-reference/update",
        description=(
            "Regenerates the checked-in Canton Metrics reference page from the latest "
            "Canton release documentation source."
        ),
        generate_commands=(("nix-shell", "--run", "npm run generate:canton-metrics-reference"),),
        paths=("docs-main/global-synchronizer/reference/canton-metrics.mdx",),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run generate:canton-metrics-reference",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="canton-topology-proto-link",
        title="Update Canton topology.proto link",
        branch="generated-docs/canton-topology-proto-link/update",
        description=(
            "Resolves the latest stable digital-asset/canton release, derives the matching "
            "release-line branch URL for topology.proto, verifies the URL is reachable, and "
            "updates the generated MDX export used by the topology reference page."
        ),
        generate_commands=(
            ("nix-shell", "--run", "npm run generate:canton-topology-proto-link"),
        ),
        paths=("docs-main/snippets/generated/canton-topology-proto-link.mdx",),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run generate:canton-topology-proto-link",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="canton-release-notes",
        title="Update Canton release notes",
        branch="release-notes/canton/update",
        description=(
            "Updates the published Canton release-note pages from the `RELEASE-NOTES.md` "
            "files in stable public Canton release bundles."
        ),
        generate_commands=(("nix-shell", "--run", "npm run update:canton-release-notes"),),
        paths=(
            "docs-main/docs.json",
            "docs-main/global-synchronizer/release-notes",
        ),
        summary_kind="static",
        summary_path=None,
        summary_label=None,
        validation=(
            "npm run update:canton-release-notes",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="wallet-gateway-release-notes",
        title="Update Wallet Gateway release notes",
        branch="release-notes/wallet-gateway/update",
        description=(
            "Updates the published Wallet Gateway release-note page from the latest "
            "`@canton-network/wallet-gateway-remote` GitHub releases in "
            "`canton-network/wallet`."
        ),
        generate_commands=(("nix-shell", "--run", "npm run update:release-notes -- --target wallet-gateway"),),
        paths=(
            "docs-main/docs.json",
            "docs-main/integrations/release-notes/wallet-gateway.mdx",
            "docs-main/integrations/release-notes/wallet-gateway-releases",
        ),
        summary_kind="release-notes-page",
        summary_path="docs-main/integrations/release-notes/wallet-gateway.mdx",
        summary_label="Wallet Gateway release notes",
        validation=(
            "npm run update:release-notes -- --target wallet-gateway",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="wallet-sdk-release-notes",
        title="Update Wallet SDK release notes",
        branch="release-notes/wallet-sdk/update",
        description=(
            "Updates the published Wallet SDK release-note page from "
            "`docs/wallet-integration-guide/src/release-notes/index.rst` in "
            "`canton-network/wallet`."
        ),
        generate_commands=(("nix-shell", "--run", "npm run update:release-notes -- --target wallet-sdk"),),
        paths=(
            "docs-main/docs.json",
            "docs-main/integrations/release-notes/wallet-sdk.mdx",
            "docs-main/integrations/release-notes/wallet-sdk-releases",
        ),
        summary_kind="release-notes-page",
        summary_path="docs-main/integrations/release-notes/wallet-sdk.mdx",
        summary_label="Wallet SDK release notes",
        validation=(
            "npm run update:release-notes -- --target wallet-sdk",
            "git diff --check",
        ),
    ),
    UpdateTarget(
        key="dapp-sdk-release-notes",
        title="Update dApp SDK release notes",
        branch="release-notes/dapp-sdk/update",
        description=(
            "Updates the published dApp SDK release-note page from the latest "
            "`@canton-network/dapp-sdk` GitHub releases in "
            "`canton-network/wallet`."
        ),
        generate_commands=(("nix-shell", "--run", "npm run update:release-notes -- --target dapp-sdk"),),
        paths=(
            "docs-main/docs.json",
            "docs-main/integrations/release-notes/dapp-sdk.mdx",
            "docs-main/integrations/release-notes/dapp-sdk-releases",
        ),
        summary_kind="release-notes-page",
        summary_path="docs-main/integrations/release-notes/dapp-sdk.mdx",
        summary_label="dApp SDK release notes",
        validation=(
            "npm run update:release-notes -- --target dapp-sdk",
            "git diff --check",
        ),
    ),
)


def generated_clean_paths() -> tuple[str, ...]:
    paths = {".internal"}
    for target in UPDATE_TARGETS:
        paths.update(target.paths)
    return tuple(sorted(paths))


def current_base_branch() -> str:
    branch = pr_utils.git("branch", "--show-current", capture=True)
    if branch:
        return branch
    ref_name = os.environ.get("GITHUB_REF_NAME")
    if ref_name:
        return ref_name
    raise RuntimeError("Could not determine base branch; pass --base-branch")


def body_markdown(*, target: UpdateTarget, changes: list[str]) -> str:
    change_text = "\n".join(changes) if changes else "- No version values changed."
    validation = "\n".join(f"- `{command}`" for command in target.validation)
    return (
        f"{target.description}\n\n"
        f"Version changes:\n"
        f"{change_text}\n\n"
        f"Validation run by the workflow:\n"
        f"{validation}\n"
    )


def summarize_target_changes(target: UpdateTarget, before_path: Path) -> list[str]:
    if target.summary_path is None:
        return []
    after_path = REPO_ROOT / target.summary_path
    if target.summary_kind == "dashboard":
        return summarize_version_changes.dashboard_changes(before_path, after_path)
    if target.summary_kind == "source-config":
        if target.summary_label is None:
            raise ValueError(f"Update target {target.key} must define summary_label")
        return summarize_version_changes.source_config_changes(
            before_path,
            after_path,
            label=target.summary_label,
        )
    if target.summary_kind == "package-source-config":
        if target.summary_label is None:
            raise ValueError(f"Update target {target.key} must define summary_label")
        return summarize_version_changes.package_source_config_changes(
            before_path,
            after_path,
            label=target.summary_label,
        )
    if target.summary_kind == "versioned-source-config":
        if target.summary_label is None:
            raise ValueError(f"Update target {target.key} must define summary_label")
        return summarize_version_changes.versioned_source_config_changes(
            before_path,
            after_path,
            label=target.summary_label,
        )
    if target.summary_kind == "artifact-source-config":
        if target.summary_label is None:
            raise ValueError(f"Update target {target.key} must define summary_label")
        return summarize_version_changes.artifact_source_config_changes(
            before_path,
            after_path,
            label=target.summary_label,
        )
    if target.summary_kind == "static":
        return []
    if target.summary_kind == "canton-release-notes":
        if target.summary_label is None:
            raise ValueError(f"Update target {target.key} must define summary_label")
        return summarize_version_changes.canton_release_note_changes(
            before_path,
            after_path,
            label=target.summary_label,
        )
    if target.summary_kind == "release-notes-page":
        if target.summary_label is None:
            raise ValueError(f"Update target {target.key} must define summary_label")
        return summarize_version_changes.release_note_page_changes(
            before_path,
            after_path,
            label=target.summary_label,
        )
    raise ValueError(f"Unknown summary kind for {target.key}: {target.summary_kind}")


def reset_to_base(base_sha: str) -> None:
    pr_utils.reset_to_base(base_sha=base_sha, clean_paths=generated_clean_paths())


def create_or_update_pull_request(
    *,
    target: UpdateTarget,
    body_path: Path,
    base_branch: str,
    repository: str,
) -> None:
    pr_utils.create_or_update_pull_request(
        title=target.title,
        branch=target.branch,
        paths=target.paths,
        body_path=body_path,
        base_branch=base_branch,
        repository=repository,
        auto_merge=target.auto_merge,
    )


def process_target(*, target: UpdateTarget, base_sha: str, base_branch: str, repository: str) -> None:
    reset_to_base(base_sha)
    before_path = pr_utils.write_base_file(base_sha, target.summary_path) if target.summary_path is not None else None

    for command in target.source_update_commands:
        pr_utils.run(command)

    if target.source_update_commands and not pr_utils.has_changes(target.source_update_paths):
        print(f"Source unchanged for {target.title}; skipping generation")
        pr_utils.close_stale_pull_request(
            title=target.title,
            branch=target.branch,
            base_branch=base_branch,
            repository=repository,
        )
        return

    changes: list[str] | None = None
    if target.require_summary_changes:
        if before_path is None:
            raise ValueError(f"Update target {target.key} requires a summary path")
        changes = summarize_target_changes(target, before_path)
        if not changes:
            print(f"No material changes for {target.title}; skipping generation")
            pr_utils.close_stale_pull_request(
                title=target.title,
                branch=target.branch,
                base_branch=base_branch,
                repository=repository,
            )
            return

    for command in target.generate_commands:
        pr_utils.run(command)

    if changes is None:
        changes = summarize_target_changes(target, before_path) if before_path is not None else []
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as body_file:
        body_path = Path(body_file.name)
    body_path.write_text(body_markdown(target=target, changes=changes), encoding="utf-8")
    create_or_update_pull_request(
        target=target,
        body_path=body_path,
        base_branch=base_branch,
        repository=repository,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate separate update PRs for configured update targets.")
    target_keys = tuple(target.key for target in UPDATE_TARGETS)
    parser.add_argument(
        "--targets",
        nargs="+",
        required=True,
        metavar="TARGET",
        choices=("all", *target_keys),
        help=f"Targets to run. Use 'all' by itself, or list one or more of: {', '.join(target_keys)}.",
    )
    parser.add_argument(
        "--base-branch",
        help="Base branch for generated PRs. Defaults to the current checkout branch.",
    )
    parser.add_argument(
        "--repository",
        help="GitHub repository for generated PRs. Defaults to the current gh repository.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List selected generated-doc targets and commands without changing files or opening PRs.",
    )
    json_output = parser.add_mutually_exclusive_group()
    json_output.add_argument(
        "--print-targets-json",
        action="store_true",
        help="Print the selected target keys as JSON without running them.",
    )
    json_output.add_argument(
        "--print-target-matrix-json",
        action="store_true",
        help="Print the selected targets as a GitHub Actions matrix without running them.",
    )
    args = parser.parse_args()
    if "all" in args.targets and len(args.targets) > 1:
        parser.error("pass --targets all by itself, or list specific target keys")
    if args.dry_run or args.print_targets_json or args.print_target_matrix_json:
        args.base_branch = args.base_branch or ""
        args.repository = args.repository or ""
    else:
        args.base_branch = args.base_branch or current_base_branch()
        args.repository = args.repository or pr_utils.current_repository()
    return args


def targets_to_run(target_keys: Sequence[str]) -> tuple[UpdateTarget, ...]:
    if not target_keys:
        raise ValueError("No update targets selected")
    if len(target_keys) == 1 and target_keys[0] == "all":
        return UPDATE_TARGETS
    if "all" in target_keys:
        raise ValueError("'all' cannot be combined with specific update targets")
    requested = tuple(dict.fromkeys(target_keys))
    return tuple(target for target in UPDATE_TARGETS if target.key in requested)


def target_matrix(targets: Sequence[UpdateTarget]) -> dict[str, list[dict[str, str | bool]]]:
    return {
        "include": [
            {
                "target": target.key,
                "requires_daml_tooling": target.requires_daml_tooling,
            }
            for target in targets
        ]
    }


def main() -> int:
    args = parse_args()
    selected_targets = targets_to_run(args.targets)
    if args.print_targets_json:
        print(json.dumps([target.key for target in selected_targets]))
        return 0
    if args.print_target_matrix_json:
        print(json.dumps(target_matrix(selected_targets)))
        return 0
    if args.dry_run:
        for target in selected_targets:
            print(f"{target.key}: {target.title}")
            for command in target.source_update_commands:
                print("  source $ " + " ".join(command))
            for command in target.generate_commands:
                print("  generate $ " + " ".join(command))
        return 0

    pr_utils.git("config", "user.name", "github-actions[bot]")
    pr_utils.git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    base_sha = pr_utils.git("rev-parse", "HEAD", capture=True)

    for target in selected_targets:
        process_target(
            target=target,
            base_sha=base_sha,
            base_branch=args.base_branch,
            repository=args.repository,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
