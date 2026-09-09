from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "update_generated_reference_prs.py"
    scripts_dir = str(script_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[script_path.stem] = module
    spec.loader.exec_module(module)
    return module


def load_policy_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "validate_generated_pr_policy.py"
    scripts_dir = str(script_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[script_path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_targets_to_run_accepts_all() -> None:
    module = load_script_module()

    assert module.targets_to_run(["all"]) == module.UPDATE_TARGETS


def test_update_targets_cover_all_generated_doc_surfaces() -> None:
    module = load_script_module()

    assert [target.key for target in module.UPDATE_TARGETS] == [
        "version-dashboard",
        "splice-openapi",
        "splice-token-standard-v2",
        "wallet-gateway-openrpc",
        "json-api-reference",
        "json-api-asyncapi-reference",
        "grpc-ledger-api-reference",
        "canton-protobuf-history",
        "ledger-bindings",
        "daml-standard-library",
        "daml-script",
        "typescript-bindings",
        "canton-console-reference",
        "canton-error-codes-reference",
        "canton-release-protocol-versions",
        "canton-metrics-reference",
        "canton-topology-proto-link",
        "canton-release-notes",
        "wallet-gateway-release-notes",
        "wallet-sdk-release-notes",
        "dapp-sdk-release-notes",
    ]


def test_dashboard_target_runs_network_variable_tabs_after_dashboard_data_generation() -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "version-dashboard")

    assert target.source_update_commands == (
        ("nix-shell", "--run", "npm run generate:version-compatibility-dashboard"),
    )
    assert target.generate_commands == (
        ("nix-shell", "--run", "npm run generate:network-variable-tabs"),
    )
    assert target.source_update_paths == (
        "config/repo-version-config.json",
        "docs-source/snippets/generated/version-dashboard-data.mdx",
    )
    assert target.paths == module.source_and_output_paths((
        "config/repo-version-config.json",
        "docs-source/snippets/generated/version-dashboard-data.mdx",
        *module.NETWORK_VARIABLE_TAB_PAGES,
    ))


def test_java_ledger_bindings_target_does_not_auto_merge() -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "ledger-bindings")

    assert target.auto_merge is False


def test_splice_openapi_target_regenerates_without_a_source_pin() -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "splice-openapi")

    assert target.source_update_commands == ()
    assert target.source_update_paths == ()
    assert target.summary_kind == "static"
    assert target.summary_path is None
    assert target.generate_commands == (
        ("nix-shell", "--run", "npm run generate:splice-mintlify-openapi"),
        module.RENDER_SITE_COMMAND,
    )
    assert "config/mintlify-openapi/splice-openapi/source-artifacts.json" not in target.paths
    assert "docs-main/reference/splice-scan-api" in target.paths


def test_generated_docs_workflow_uses_merger_app_for_pr_mutations() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "update-version-dashboard.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.count("persist-credentials: false") == 2
    assert "GH_TOKEN: ${{ steps.merger-token.outputs.token || github.token }}" in workflow
    assert "GITHUB_TOKEN: ${{ steps.merger-token.outputs.token || github.token }}" in workflow
    assert "GENERATED_DOCS_WORKFLOW_TOKEN: ${{ github.token }}" in workflow
    assert "uses: cachix/install-nix-action@v31" not in workflow
    assert "sudo apt-get" not in workflow
    assert (
        "run: SKIP_NPM_INSTALL=1 direnv allow . && SKIP_NPM_INSTALL=1 direnv exec . true"
        in workflow
    )
    assert "python3 scripts/check_generated_docs_dependencies.py" in workflow
    assert "run: SKIP_NPM_INSTALL=1 nix-shell --run 'gh auth setup-git'" in workflow
    assert 'args=(python3 scripts/update_generated_reference_prs.py --targets "${{ matrix.target }}")' in workflow
    assert "args+=(--dry-run)" in workflow
    assert "SKIP_NPM_INSTALL=1 nix-shell --run \"$generated_docs_command\"" in workflow


def test_generated_docs_workflow_only_sets_up_daml_for_declared_targets() -> None:
    module = load_script_module()
    workflow = (REPO_ROOT / ".github" / "workflows" / "update-version-dashboard.yml").read_text(
        encoding="utf-8"
    )

    daml_targets = [target.key for target in module.UPDATE_TARGETS if target.requires_daml_tooling]

    assert daml_targets == ["splice-token-standard-v2", "daml-standard-library", "daml-script"]
    assert "--print-target-matrix-json" in workflow
    assert "matrix: ${{ fromJSON(needs.select-targets.outputs.target_matrix) }}" in workflow
    assert "if: ${{ matrix.requires_daml_tooling }}" in workflow
    assert "bash scripts/install_daml_tooling.sh" in workflow


def test_target_matrix_includes_daml_requirement() -> None:
    module = load_script_module()
    targets = module.targets_to_run(["splice-token-standard-v2", "canton-release-notes"])

    assert module.target_matrix(targets) == {
        "include": [
            {"target": "splice-token-standard-v2", "requires_daml_tooling": True},
            {"target": "canton-release-notes", "requires_daml_tooling": False},
        ]
    }


def test_daml_script_target_wires_source_pin_and_generated_paths() -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "daml-script")

    assert target.branch == "generated-references/daml-script/update"
    assert target.source_update_commands == (
        ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source daml-script"),
    )
    assert target.source_update_paths == ("config/x2mdx/daml-script/source-artifacts.json",)
    assert target.generate_commands == (
        ("nix-shell", "--run", "npm run generate:daml-script-reference"),
        module.RENDER_SITE_COMMAND,
    )
    assert target.paths == (
        "config/x2mdx/daml-script/source-artifacts.json",
        "docs-source/docs.json",
        "docs-main/docs.json",
        "docs-source/appdev/reference/daml-script",
        "docs-main/appdev/reference/daml-script",
    )
    assert target.validation == (
        "npm run update:generated-reference-sources -- --source daml-script",
        "npm run generate:daml-script-reference",
        "git diff --check",
    )


def test_daml_script_target_skips_generation_when_source_is_unchanged(monkeypatch) -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "daml-script")
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(module, "reset_to_base", lambda base_sha: calls.append(("reset", base_sha)))
    monkeypatch.setattr(module.pr_utils, "write_base_file", lambda base_sha, path: Path("/tmp/before.json"))
    monkeypatch.setattr(module.pr_utils, "has_changes", lambda paths: False)
    monkeypatch.setattr(
        module.pr_utils,
        "close_stale_pull_request",
        lambda **kwargs: calls.append(("close", kwargs["branch"])),
    )
    monkeypatch.setattr(module, "create_or_update_pull_request", lambda **kwargs: calls.append(("pr",)))

    def fake_run(command: tuple[str, ...]) -> None:
        calls.append(command)

    monkeypatch.setattr(module.pr_utils, "run", fake_run)

    module.process_target(
        target=target,
        base_sha="base-sha",
        base_branch="main",
        repository="canton-network/cf-docs",
    )

    assert calls == [
        ("reset", "base-sha"),
        ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source daml-script"),
        ("close", "generated-references/daml-script/update"),
    ]
    assert not any("generate:daml-script-reference" in " ".join(call) for call in calls if isinstance(call, tuple))


def test_source_update_targets_skip_generation_when_source_is_unchanged(monkeypatch) -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "wallet-gateway-openrpc")
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(module, "reset_to_base", lambda base_sha: calls.append(("reset", base_sha)))
    monkeypatch.setattr(module.pr_utils, "write_base_file", lambda base_sha, path: Path("/tmp/before.json"))
    monkeypatch.setattr(module.pr_utils, "has_changes", lambda paths: False)
    monkeypatch.setattr(
        module.pr_utils,
        "close_stale_pull_request",
        lambda **kwargs: calls.append(("close", kwargs["branch"])),
    )
    monkeypatch.setattr(module, "create_or_update_pull_request", lambda **kwargs: calls.append(("pr",)))

    def fake_run(command: tuple[str, ...]) -> None:
        calls.append(command)

    monkeypatch.setattr(module.pr_utils, "run", fake_run)

    module.process_target(
        target=target,
        base_sha="base-sha",
        base_branch="main",
        repository="canton-network/cf-docs",
    )

    assert calls == [
        ("reset", "base-sha"),
        ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source wallet-gateway-openrpc"),
        ("close", "generated-references/wallet-gateway-openrpc/update"),
    ]


def test_version_dashboard_skips_timestamp_only_source_changes(monkeypatch, tmp_path: Path) -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "version-dashboard")
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(module, "reset_to_base", lambda base_sha: calls.append(("reset", base_sha)))
    monkeypatch.setattr(module.pr_utils, "write_base_file", lambda base_sha, path: tmp_path / "before.json")
    monkeypatch.setattr(module.pr_utils, "has_changes", lambda paths: True)
    monkeypatch.setattr(module, "summarize_target_changes", lambda target, before_path: [])
    monkeypatch.setattr(
        module.pr_utils,
        "close_stale_pull_request",
        lambda **kwargs: calls.append(("close", kwargs["branch"])),
    )
    monkeypatch.setattr(module, "create_or_update_pull_request", lambda **kwargs: calls.append(("pr",)))
    monkeypatch.setattr(module.pr_utils, "run", lambda command: calls.append(command))

    module.process_target(
        target=target,
        base_sha="base-sha",
        base_branch="main",
        repository="canton-network/cf-docs",
    )

    assert calls == [
        ("reset", "base-sha"),
        ("nix-shell", "--run", "npm run generate:version-compatibility-dashboard"),
        ("close", "version-dashboard/update"),
    ]
    assert not any("generate:network-variable-tabs" in " ".join(call) for call in calls)


def test_source_update_targets_generate_when_source_changed(monkeypatch, tmp_path: Path) -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "wallet-gateway-openrpc")
    calls: list[tuple[str, ...]] = []
    body_paths: list[Path] = []

    monkeypatch.setattr(module, "reset_to_base", lambda base_sha: calls.append(("reset", base_sha)))
    monkeypatch.setattr(module.pr_utils, "write_base_file", lambda base_sha, path: tmp_path / "before.json")
    monkeypatch.setattr(module.pr_utils, "has_changes", lambda paths: True)
    monkeypatch.setattr(module, "summarize_target_changes", lambda target, before_path: ["- changed"])

    def fake_pr(**kwargs) -> None:
        calls.append(("pr", kwargs["target"].key))
        body_paths.append(kwargs["body_path"])

    monkeypatch.setattr(module, "create_or_update_pull_request", fake_pr)

    def fake_run(command: tuple[str, ...]) -> None:
        calls.append(command)

    monkeypatch.setattr(module.pr_utils, "run", fake_run)

    module.process_target(
        target=target,
        base_sha="base-sha",
        base_branch="main",
        repository="canton-network/cf-docs",
    )

    assert calls == [
        ("reset", "base-sha"),
        ("nix-shell", "--run", "npm run update:generated-reference-sources -- --source wallet-gateway-openrpc"),
        ("nix-shell", "--run", "npm run generate:wallet-gateway-openrpc-reference"),
        module.RENDER_SITE_COMMAND,
        ("pr", "wallet-gateway-openrpc"),
    ]
    assert body_paths


def test_targets_to_run_requires_at_least_one_target() -> None:
    module = load_script_module()

    try:
        module.targets_to_run([])
    except ValueError as error:
        assert str(error) == "No update targets selected"
    else:
        raise AssertionError("Expected targets_to_run to reject an empty target selection")


def test_targets_to_run_rejects_mixed_all_and_target_keys() -> None:
    module = load_script_module()

    try:
        module.targets_to_run(["all", "version-dashboard"])
    except ValueError as error:
        assert str(error) == "'all' cannot be combined with specific update targets"
    else:
        raise AssertionError("Expected targets_to_run to reject mixed all and target keys")


def test_targets_to_run_preserves_declared_target_order_for_target_keys() -> None:
    module = load_script_module()

    targets = module.targets_to_run(["wallet-gateway-openrpc", "version-dashboard"])

    assert [target.key for target in targets] == ["version-dashboard", "wallet-gateway-openrpc"]


def test_generated_clean_paths_include_target_paths_and_internal_output() -> None:
    module = load_script_module()

    clean_paths = module.generated_clean_paths()

    assert ".internal" in clean_paths
    assert "docs-main/openapi/splice" in clean_paths
    assert "docs-main/sdks-tools/api-reference/splice-daml/splice-api-token-holding-v2" in clean_paths
    assert "docs-main/openapi/json-ledger-api" in clean_paths
    assert "docs-main/reference/grpc-ledger-api-reference" in clean_paths
    assert "docs-main/reference/java" in clean_paths
    assert "docs-main/appdev/reference/daml-standard-library" in clean_paths
    assert "docs-main/appdev/reference/daml-script" in clean_paths
    assert "docs-main/reference/wallet-gateway-json-rpc" in clean_paths
    assert "docs-main/reference/typescript" in clean_paths
    assert "docs-main/snippets/generated/version-dashboard-data.mdx" in clean_paths
    assert "docs-main/global-synchronizer/deployment/validator-kubernetes.mdx" in clean_paths
    assert "docs-main/global-synchronizer/reference/canton-console-commands.mdx" in clean_paths
    assert "docs-main/global-synchronizer/reference/error-codes.mdx" in clean_paths
    assert "docs-main/release-notes/releases-and-versioning.mdx" in clean_paths
    assert "docs-main/appdev/deep-dives/external-signing-topology.mdx" in clean_paths
    assert "docs-main/global-synchronizer/reference/canton-metrics.mdx" in clean_paths
    assert "docs-main/global-synchronizer/release-notes" in clean_paths
    assert "docs-main/integrations/release-notes/wallet-gateway.mdx" in clean_paths
    assert "docs-main/integrations/release-notes/wallet-gateway-releases" in clean_paths
    assert "docs-main/integrations/release-notes/wallet-sdk.mdx" in clean_paths
    assert "docs-main/integrations/release-notes/wallet-sdk-releases" in clean_paths
    assert "docs-main/integrations/release-notes/dapp-sdk.mdx" in clean_paths
    assert "docs-main/integrations/release-notes/dapp-sdk-releases" in clean_paths


def test_target_paths_exist_in_base_checkout() -> None:
    module = load_script_module()

    missing_paths = {
        target.key: [path for path in target.paths if not (REPO_ROOT / path).exists()]
        for target in module.UPDATE_TARGETS
    }

    assert {key: paths for key, paths in missing_paths.items() if paths} == {}


def test_network_variable_tab_target_pages_match_generated_blocks() -> None:
    module = load_script_module()

    generated_block_pages = tuple(
        sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "docs-main").rglob("*.mdx")
            if "{/* NETWORKVARS_START" in path.read_text(encoding="utf-8")
        )
    )

    assert tuple(path.replace("docs-source/", "docs-main/", 1) for path in module.NETWORK_VARIABLE_TAB_PAGES) == generated_block_pages


def test_body_markdown_includes_description_changes_and_validation() -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "wallet-gateway-openrpc")

    body = module.body_markdown(
        target=target,
        changes=["- Wallet Gateway OpenRPC publish_version: 0.25.0 -> 1.4.0"],
    )

    assert body.startswith("Updates the Wallet Gateway OpenRPC source pin")
    assert "Version changes:\n- Wallet Gateway OpenRPC publish_version: 0.25.0 -> 1.4.0" in body
    assert "- `npm run generate:wallet-gateway-openrpc-reference`" in body


def test_body_markdown_notes_when_no_versions_changed() -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "version-dashboard")

    body = module.body_markdown(target=target, changes=[])

    assert "Version changes:\n- No version values changed." in body


def test_summarize_target_changes_supports_versioned_source_configs(monkeypatch, tmp_path: Path) -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "json-api-reference")
    before = tmp_path / "before.json"
    before.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    after = tmp_path / target.summary_path
    after.parent.mkdir(parents=True)
    after.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        module.summarize_version_changes,
        "versioned_source_config_changes",
        lambda before_path, after_path, *, label: [f"{label}:{before_path.name}:{after_path.name}"],
    )

    assert module.summarize_target_changes(target, before) == [
        "JSON Ledger API OpenAPI:before.json:source-artifacts.json"
    ]


def test_summarize_target_changes_supports_artifact_source_configs(monkeypatch, tmp_path: Path) -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "ledger-bindings")
    before = tmp_path / "before.json"
    before.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    after = tmp_path / target.summary_path
    after.parent.mkdir(parents=True)
    after.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        module.summarize_version_changes,
        "artifact_source_config_changes",
        lambda before_path, after_path, *, label: [f"{label}:{before_path.name}:{after_path.name}"],
    )

    assert module.summarize_target_changes(target, before) == [
        "Java ledger bindings:before.json:source-artifacts.json"
    ]


def test_summarize_target_changes_supports_release_note_pages(monkeypatch, tmp_path: Path) -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "wallet-gateway-release-notes")
    before = tmp_path / "before.mdx"
    before.write_text("## 1.3.0\n", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    after = tmp_path / target.summary_path
    after.parent.mkdir(parents=True)
    after.write_text("## 1.5.0\n", encoding="utf-8")
    monkeypatch.setattr(
        module.summarize_version_changes,
        "release_note_page_changes",
        lambda before_path, after_path, *, label: [f"{label}:{before_path.name}:{after_path.name}"],
    )

    assert module.summarize_target_changes(target, before) == [
        "Wallet Gateway release notes:before.mdx:wallet-gateway.mdx"
    ]


def test_parse_args_defaults_base_branch_and_repository_from_local_context(monkeypatch) -> None:
    module = load_script_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_generated_reference_prs.py",
            "--targets",
            "all",
        ],
    )
    monkeypatch.setattr(
        module.pr_utils,
        "git",
        lambda *args, capture=False: "wallet-gateway-openrpc-refresh"
        if args == ("branch", "--show-current") and capture
        else "",
    )
    monkeypatch.setattr(module.pr_utils, "current_repository", lambda: "canton-network/cf-docs")

    args = module.parse_args()

    assert args.base_branch == "wallet-gateway-openrpc-refresh"
    assert args.repository == "canton-network/cf-docs"


def test_parse_args_accepts_explicit_base_branch_and_repository(monkeypatch) -> None:
    module = load_script_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_generated_reference_prs.py",
            "--targets",
            "all",
            "--base-branch",
            "main",
            "--repository",
            "canton-network/cf-docs",
        ],
    )

    args = module.parse_args()

    assert args.base_branch == "main"
    assert args.repository == "canton-network/cf-docs"


def test_parse_args_dry_run_does_not_require_repository_context(monkeypatch) -> None:
    module = load_script_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_generated_reference_prs.py",
            "--targets",
            "all",
            "--dry-run",
        ],
    )
    monkeypatch.setattr(
        module.pr_utils,
        "current_repository",
        lambda: (_ for _ in ()).throw(AssertionError("repository should not be resolved")),
    )

    args = module.parse_args()

    assert args.dry_run is True
    assert args.repository == ""


def test_main_dry_run_lists_targets_without_git_or_gh(monkeypatch, capsys) -> None:
    module = load_script_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_generated_reference_prs.py",
            "--targets",
            "version-dashboard",
            "--dry-run",
        ],
    )
    monkeypatch.setattr(
        module.pr_utils,
        "git",
        lambda *args, capture=False: (_ for _ in ()).throw(AssertionError("git should not run")),
    )

    assert module.main() == 0
    output = capsys.readouterr().out
    assert "version-dashboard: Update generated docs" in output
    assert "source $ nix-shell --run npm run generate:version-compatibility-dashboard" in output
    assert "npm run generate:network-variable-tabs" in output


def test_current_base_branch_uses_github_ref_name_for_detached_checkout(monkeypatch) -> None:
    module = load_script_module()
    monkeypatch.setattr(
        module.pr_utils,
        "git",
        lambda *args, capture=False: "" if args == ("branch", "--show-current") and capture else "",
    )
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    assert module.current_base_branch() == "main"


def test_create_or_update_pull_request_signs_generated_commit(monkeypatch, tmp_path: Path) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    git_calls: list[tuple[str, ...]] = []
    gh_calls: list[tuple[str, ...]] = []
    pr_list_calls = 0

    def fake_git(*args: str, capture: bool = False) -> str:
        git_calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return " M generated.mdx"
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        return ""

    def fake_gh(*args: str, capture: bool = False) -> str:
        nonlocal pr_list_calls
        gh_calls.append(args)
        if args[:2] == ("pr", "list"):
            pr_list_calls += 1
            return "123" if pr_list_calls > 1 else ""
        return ""

    monkeypatch.setattr(pr_utils, "git", fake_git)
    monkeypatch.setattr(pr_utils, "gh", fake_gh)
    monkeypatch.setattr(pr_utils, "push_branch", lambda branch: None)
    monkeypatch.setattr(pr_utils, "mark_pull_request_ready", lambda **kwargs: None)
    body_path = tmp_path / "body.md"
    body_path.write_text("body", encoding="utf-8")

    pr_number = pr_utils.create_or_update_pull_request(
        title="Update generated docs",
        branch="generated/update",
        paths=("generated.mdx",),
        body_path=body_path,
        base_branch="main",
        repository="canton-network/cf-docs",
    )

    assert ("commit", "--signoff", "-m", "Update generated docs") in git_calls
    assert not any(call[:1] == ("switch",) for call in git_calls)
    assert any(call[:2] == ("pr", "create") for call in gh_calls)
    assert not any("--draft" in call for call in gh_calls)
    assert pr_number == "123"


def test_create_or_update_pull_request_marks_existing_pr_ready(monkeypatch, tmp_path: Path) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    gh_calls: list[tuple[str, ...]] = []
    ready_calls: list[dict[str, str]] = []

    def fake_git(*args: str, capture: bool = False) -> str:
        if args[:2] == ("status", "--porcelain"):
            return " M generated.mdx"
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        return ""

    def fake_gh(*args: str, capture: bool = False) -> str:
        gh_calls.append(args)
        if args[:2] == ("pr", "list"):
            return "932"
        return ""

    monkeypatch.setattr(pr_utils, "git", fake_git)
    monkeypatch.setattr(pr_utils, "gh", fake_gh)
    monkeypatch.setattr(pr_utils, "push_branch", lambda branch: None)
    monkeypatch.setattr(pr_utils, "mark_pull_request_ready", lambda **kwargs: ready_calls.append(kwargs))
    body_path = tmp_path / "body.md"
    body_path.write_text("body", encoding="utf-8")

    pr_number = pr_utils.create_or_update_pull_request(
        title="Update generated docs",
        branch="version-dashboard/update",
        paths=("docs-main/snippets/generated/version-dashboard-data.mdx",),
        body_path=body_path,
        base_branch="main",
        repository="canton-network/cf-docs",
    )

    assert pr_number == "932"
    assert ready_calls == [{"pr_number": "932", "repository": "canton-network/cf-docs"}]
    assert not any("--undo" in call for call in gh_calls)
    assert any(call[:2] == ("pr", "edit") and call[2] == "932" for call in gh_calls)


def test_create_or_update_pull_request_keeps_matching_existing_branch(
    monkeypatch, tmp_path: Path
) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    git_calls: list[tuple[str, ...]] = []
    gh_calls: list[tuple[str, ...]] = []
    auto_merge_calls: list[dict[str, object]] = []

    def fake_git(*args: str, capture: bool = False) -> str:
        git_calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            return " M generated.mdx"
        return ""

    def fake_gh(*args: str, capture: bool = False) -> str:
        gh_calls.append(args)
        if args[:2] == ("pr", "list"):
            return "932"
        return ""

    monkeypatch.setattr(pr_utils, "git", fake_git)
    monkeypatch.setattr(pr_utils, "gh", fake_gh)
    monkeypatch.setattr(
        pr_utils,
        "matching_remote_branch_sha",
        lambda **kwargs: "existing123",
    )
    monkeypatch.setattr(
        pr_utils,
        "push_branch",
        lambda branch: (_ for _ in ()).throw(AssertionError("branch must not be pushed")),
    )
    monkeypatch.setattr(pr_utils, "mark_pull_request_ready", lambda **kwargs: None)
    monkeypatch.setattr(
        pr_utils,
        "maybe_merge_generated_pr",
        lambda **kwargs: auto_merge_calls.append(kwargs),
    )
    body_path = tmp_path / "body.md"
    body_path.write_text("body", encoding="utf-8")

    pr_number = pr_utils.create_or_update_pull_request(
        title="Update Java ledger bindings reference",
        branch="generated-references/ledger-bindings/update",
        paths=("generated.mdx",),
        body_path=body_path,
        base_branch="main",
        repository="canton-network/cf-docs",
        auto_merge=False,
    )

    assert pr_number == "932"
    assert not any(call[:1] == ("commit",) for call in git_calls)
    assert gh_calls == [
        (
            "pr",
            "list",
            "--repo",
            "canton-network/cf-docs",
            "--head",
            "generated-references/ledger-bindings/update",
            "--base",
            "main",
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[0].number // empty",
        )
    ]
    assert auto_merge_calls[0]["head_sha"] == "existing123"


def test_matching_remote_branch_sha_compares_staged_target_paths(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    git_calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, capture: bool = False) -> str:
        git_calls.append(args)
        if args[:3] == ("ls-remote", "--heads", "origin"):
            return "abc123\trefs/heads/generated/update"
        if args[:3] == ("diff", "--cached", "--name-only"):
            return ""
        return ""

    monkeypatch.setattr(pr_utils, "git", fake_git)

    assert (
        pr_utils.matching_remote_branch_sha(
            branch="generated/update",
            paths=("generated.mdx", "generated/"),
        )
        == "abc123"
    )
    assert ("fetch", "--no-tags", "origin", "refs/heads/generated/update") in git_calls
    assert (
        "diff",
        "--cached",
        "--name-only",
        "abc123",
        "--",
        "generated.mdx",
        "generated/",
    ) in git_calls


def test_create_or_update_pull_request_can_disable_auto_merge(
    monkeypatch, tmp_path: Path
) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    auto_merge_calls: list[dict[str, object]] = []

    def fake_git(*args: str, capture: bool = False) -> str:
        if args[:2] == ("status", "--porcelain"):
            return " M generated.mdx"
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        return ""

    def fake_gh(*args: str, capture: bool = False) -> str:
        if args[:2] == ("pr", "list"):
            return "977"
        return ""

    monkeypatch.setattr(pr_utils, "git", fake_git)
    monkeypatch.setattr(pr_utils, "gh", fake_gh)
    monkeypatch.setattr(pr_utils, "push_branch", lambda branch: None)
    monkeypatch.setattr(pr_utils, "mark_pull_request_ready", lambda **kwargs: None)
    monkeypatch.setattr(
        pr_utils,
        "maybe_merge_generated_pr",
        lambda **kwargs: auto_merge_calls.append(kwargs),
    )
    body_path = tmp_path / "body.md"
    body_path.write_text("body", encoding="utf-8")

    pr_utils.create_or_update_pull_request(
        title="Update Java ledger bindings reference",
        branch="generated-references/ledger-bindings/update",
        paths=("docs-main/reference/java-bindings.mdx",),
        body_path=body_path,
        base_branch="main",
        repository="canton-network/cf-docs",
        auto_merge=False,
    )

    assert auto_merge_calls == [
        {
            "pr_number": "977",
            "repository": "canton-network/cf-docs",
            "base_branch": "main",
            "branch": "generated-references/ledger-bindings/update",
            "head_sha": "abc123",
            "enabled": False,
        }
    ]


def test_create_or_update_pull_request_closes_stale_pr_when_no_changes(
    monkeypatch, tmp_path: Path
) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    gh_calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(pr_utils, "has_changes", lambda paths: False)

    def fake_gh(*args: str, capture: bool = False) -> str:
        gh_calls.append(args)
        if args[:2] == ("pr", "list"):
            return "825"
        return ""

    monkeypatch.setattr(pr_utils, "gh", fake_gh)
    body_path = tmp_path / "body.md"
    body_path.write_text("body", encoding="utf-8")

    pr_utils.create_or_update_pull_request(
        title="Update generated docs",
        branch="version-dashboard/update",
        paths=("docs-main/snippets/generated/version-dashboard-data.mdx",),
        body_path=body_path,
        base_branch="remaining-generated-reference-pr-targets",
        repository="canton-network/cf-docs",
    )

    assert any(call[:2] == ("pr", "close") and call[2] == "825" for call in gh_calls)
    assert any("--delete-branch" in call for call in gh_calls)


def test_push_branch_uses_full_ref_for_detached_head(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    git_calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, capture: bool = False) -> str:
        git_calls.append(args)
        if args[:3] == ("ls-remote", "--heads", "origin"):
            return ""
        return ""

    monkeypatch.setattr(pr_utils, "git", fake_git)

    pr_utils.push_branch("version-dashboard/update")

    assert (
        "push",
        "origin",
        "HEAD:refs/heads/version-dashboard/update",
    ) in git_calls


def test_maybe_merge_generated_pr_skips_without_merger_token(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    monkeypatch.delenv("GENERATED_DOCS_MERGER_TOKEN", raising=False)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(pr_utils, "run", lambda command, **kwargs: calls.append(tuple(command)))

    pr_utils.maybe_merge_generated_pr(
        pr_number="932",
        repository="canton-network/cf-docs",
        base_branch="main",
        branch="version-dashboard/update",
        head_sha="abc123",
    )

    assert calls == []


def test_maybe_merge_generated_pr_skips_when_disabled(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    monkeypatch.setenv("GENERATED_DOCS_MERGER_TOKEN", "token")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(pr_utils, "run", lambda command, **kwargs: calls.append(tuple(command)))

    pr_utils.maybe_merge_generated_pr(
        pr_number="977",
        repository="canton-network/cf-docs",
        base_branch="main",
        branch="generated-references/ledger-bindings/update",
        head_sha="abc123",
        enabled=False,
    )

    assert calls == []


def test_dispatch_mintlify_validation_runs_workflow_on_generated_branch(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    monkeypatch.setenv("GENERATED_DOCS_WORKFLOW_TOKEN", "workflow-token")
    monkeypatch.setattr(
        pr_utils,
        "run",
        lambda command, **kwargs: calls.append((tuple(command), kwargs.get("env"))) or "",
    )

    pr_utils.dispatch_mintlify_validation(
        repository="canton-network/cf-docs",
        branch="version-dashboard/update",
    )

    assert calls == [
        (
            (
                "gh",
                "workflow",
                "run",
                "mintlify-validate.yml",
                "--repo",
                "canton-network/cf-docs",
                "--ref",
                "version-dashboard/update",
            ),
            {"GH_TOKEN": "workflow-token", "GITHUB_TOKEN": "workflow-token"},
        )
    ]


def test_wait_for_check_success_accepts_successful_check(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    monkeypatch.setattr(
        pr_utils,
        "check_runs_for_sha",
        lambda **kwargs: [{"name": "mintlify validate", "status": "completed", "conclusion": "success"}],
    )
    monkeypatch.setattr(
        pr_utils.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(AssertionError("sleep should not run")),
    )

    pr_utils.wait_for_check_success(
        repository="canton-network/cf-docs",
        head_sha="abc123",
        check_name="mintlify validate",
    )


def test_wait_for_check_success_rejects_failed_check(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    monkeypatch.setattr(
        pr_utils,
        "check_runs_for_sha",
        lambda **kwargs: [{"name": "mintlify validate", "status": "completed", "conclusion": "failure"}],
    )

    try:
        pr_utils.wait_for_check_success(
            repository="canton-network/cf-docs",
            head_sha="abc123",
            check_name="mintlify validate",
        )
    except RuntimeError as error:
        assert "Required check 'mintlify validate' failed for abc123: failure" == str(error)
    else:
        raise AssertionError("Expected failed required check to raise")


def test_maybe_merge_generated_pr_validates_waits_then_direct_merges(monkeypatch) -> None:
    load_script_module()
    import generated_reference_pr_utils as pr_utils

    monkeypatch.setenv("GENERATED_DOCS_MERGER_TOKEN", "token")
    events: list[tuple[object, ...]] = []

    def fake_run(command, **kwargs):
        events.append(("run", tuple(command), kwargs["env"]))
        return ""

    monkeypatch.setattr(pr_utils, "run", fake_run)
    monkeypatch.setattr(
        pr_utils,
        "dispatch_mintlify_validation",
        lambda **kwargs: events.append(("dispatch", kwargs)),
    )
    monkeypatch.setattr(
        pr_utils,
        "wait_for_check_success",
        lambda **kwargs: events.append(("wait", kwargs)),
    )

    pr_utils.maybe_merge_generated_pr(
        pr_number="932",
        repository="canton-network/cf-docs",
        base_branch="main",
        branch="version-dashboard/update",
        head_sha="abc123",
    )

    assert events == [
        (
            "run",
            (
                "python3",
                "scripts/validate_generated_pr_policy.py",
                "932",
                "--repository",
                "canton-network/cf-docs",
                "--base-branch",
                "main",
                "--head-branch",
                "version-dashboard/update",
                "--head-sha",
                "abc123",
            ),
            {"GH_TOKEN": "token", "GITHUB_TOKEN": "token"},
        ),
        (
            "dispatch",
            {"repository": "canton-network/cf-docs", "branch": "version-dashboard/update"},
        ),
        (
            "wait",
            {
                "repository": "canton-network/cf-docs",
                "head_sha": "abc123",
                "check_name": "mintlify validate",
            },
        ),
        (
            "run",
            (
                "gh",
                "pr",
                "merge",
                "932",
                "--repo",
                "canton-network/cf-docs",
                "--admin",
                "--squash",
                "--delete-branch",
                "--match-head-commit",
                "abc123",
            ),
            {"GH_TOKEN": "token", "GITHUB_TOKEN": "token"},
        ),
    ]


def test_generated_pr_policy_accepts_configured_generated_paths() -> None:
    policy = load_policy_module()

    errors = policy.validate_policy(
        policy_input=policy.PolicyInput(
            pr_number="932",
            repository="canton-network/cf-docs",
            base_branch="main",
            head_branch="version-dashboard/update",
            head_sha="abc123",
        ),
        pr_metadata={
            "author": {"login": "app/cf-docs-generated-docs-merger"},
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "main",
            "headRefName": "version-dashboard/update",
            "headRefOid": "abc123",
        },
        changed_files=(
            "config/repo-version-config.json",
            "docs-main/snippets/generated/version-dashboard-data.mdx",
        ),
        branch_paths={
            "version-dashboard/update": (
                "config/repo-version-config.json",
                "docs-main/snippets/generated/version-dashboard-data.mdx",
            )
        },
    )

    assert errors == []


def test_generated_pr_policy_rejects_unexpected_author_and_paths() -> None:
    policy = load_policy_module()

    errors = policy.validate_policy(
        policy_input=policy.PolicyInput(
            pr_number="932",
            repository="canton-network/cf-docs",
            base_branch="main",
            head_branch="version-dashboard/update",
            head_sha="abc123",
        ),
        pr_metadata={
            "author": {"login": "danielporterda"},
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "main",
            "headRefName": "version-dashboard/update",
            "headRefOid": "abc123",
        },
        changed_files=(".github/workflows/update-version-dashboard.yml",),
        branch_paths={
            "version-dashboard/update": (
                "config/repo-version-config.json",
                "docs-main/snippets/generated/version-dashboard-data.mdx",
            )
        },
    )

    assert "expected PR author 'app/cf-docs-generated-docs-merger', found 'danielporterda'" in errors
    assert (
        "changed files outside configured generated paths: .github/workflows/update-version-dashboard.yml"
        in errors
    )


def test_generated_pr_policy_rejects_legacy_github_actions_author() -> None:
    policy = load_policy_module()

    errors = policy.validate_policy(
        policy_input=policy.PolicyInput(
            pr_number="932",
            repository="canton-network/cf-docs",
            base_branch="main",
            head_branch="version-dashboard/update",
            head_sha="abc123",
        ),
        pr_metadata={
            "author": {"login": "app/github-actions"},
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "main",
            "headRefName": "version-dashboard/update",
            "headRefOid": "abc123",
        },
        changed_files=("config/repo-version-config.json",),
        branch_paths={
            "version-dashboard/update": ("config/repo-version-config.json",),
        },
    )

    assert errors == [
        "expected PR author 'app/cf-docs-generated-docs-merger', found 'app/github-actions'"
    ]


def test_all_automated_producers_render_and_commit_both_trees() -> None:
    module = load_script_module()
    for target in module.UPDATE_TARGETS:
        assert target.generate_commands[-1] == module.RENDER_SITE_COMMAND, target.key
        for path in target.paths:
            if path.startswith("docs-source/"):
                assert path.replace("docs-source/", "docs-main/", 1) in target.paths
            if path.startswith("docs-main/"):
                assert path.replace("docs-main/", "docs-source/", 1) in target.paths
        assert not any(path.startswith("docs-main/") for path in target.source_update_paths)


def test_topology_target_renders_table_and_stages_source_and_output() -> None:
    module = load_script_module()
    target = next(target for target in module.UPDATE_TARGETS if target.key == "canton-topology-proto-link")

    assert target.generate_commands == (
        ("nix-shell", "--run", "npm run generate:canton-topology-proto-link"),
        ("nix-shell", "--run", "npm run generate:canton-topology-transaction-versions"),
        module.RENDER_SITE_COMMAND,
    )
    assert target.paths == (
        "docs-source/snippets/generated/canton-topology-proto-link.mdx",
        "docs-main/snippets/generated/canton-topology-proto-link.mdx",
        "docs-source/appdev/deep-dives/external-signing-topology.mdx",
        "docs-main/appdev/deep-dives/external-signing-topology.mdx",
    )
