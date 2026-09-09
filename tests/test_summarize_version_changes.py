from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "summarize_version_changes.py"
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[script_path.stem] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def dashboard_payload(*, splice_version: str, dar_version: str) -> dict:
    return {
        "repositories": {
            "splice": {
                "versionMapping": {
                    "mainnet": {"externalVersion": splice_version},
                    "testnet": {"externalVersion": splice_version},
                }
            },
            "walletGateway": {
                "versionMapping": {
                    "mainnet": {"externalVersion": "1.4.0"},
                }
            },
        },
        "versions": {
            "mainnet": {
                "advanced": {
                    "darVersions": [
                        {"name": "splice-wallet", "version": dar_version},
                    ]
                }
            }
        },
    }


def test_dashboard_changes_summarizes_component_and_dar_versions(tmp_path: Path) -> None:
    module = load_script_module()
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_json(before, dashboard_payload(splice_version="0.6.5", dar_version="0.1.18"))
    write_json(after, dashboard_payload(splice_version="0.6.7", dar_version="0.1.19"))

    assert module.dashboard_changes(before, after) == [
        "- MainNet Splice: 0.6.5 -> 0.6.7",
        "- TestNet Splice: 0.6.5 -> 0.6.7",
        "- MainNet splice-wallet DAR: 0.1.18 -> 0.1.19",
    ]


def test_dashboard_changes_reports_no_changes(tmp_path: Path) -> None:
    module = load_script_module()
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    payload = dashboard_payload(splice_version="0.6.5", dar_version="0.1.18")
    write_json(before, payload)
    write_json(after, payload)

    assert module.dashboard_changes(before, after) == []


def test_source_config_changes_summarizes_publish_version(tmp_path: Path) -> None:
    module = load_script_module()
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_json(before, {"publish_version": "0.25.0", "min_version": "0.24.0"})
    write_json(after, {"publish_version": "1.4.0", "min_version": "0.24.0"})

    assert module.source_config_changes(before, after, label="Wallet Gateway OpenRPC") == [
        "- Wallet Gateway OpenRPC publish_version: 0.25.0 -> 1.4.0"
    ]


def test_package_source_config_changes_summarizes_package_publish_versions(tmp_path: Path) -> None:
    module = load_script_module()
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_json(
        before,
        {
            "packages": [
                {"package_name": "@daml/types", "publish_version": "3.4.11"},
                {"package_name": "@canton-network/dapp-sdk", "publish_version": "1.1.0"},
            ]
        },
    )
    write_json(
        after,
        {
            "packages": [
                {"package_name": "@daml/types", "publish_version": "3.5.2"},
                {"package_name": "@canton-network/dapp-sdk", "publish_version": "1.2.0"},
            ]
        },
    )

    assert module.package_source_config_changes(before, after, label="TypeScript bindings") == [
        "- TypeScript bindings @daml/types publish_version: 3.4.11 -> 3.5.2",
        "- TypeScript bindings @canton-network/dapp-sdk publish_version: 1.1.0 -> 1.2.0",
    ]


def test_versioned_source_config_changes_summarizes_canton_release_versions(tmp_path: Path) -> None:
    module = load_script_module()
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_json(
        before,
        {
            "versions": [
                {"version": "3.4", "canton_version": "3.4.11"},
                {"version": "3.5", "canton_version": "3.5.0-snapshot.20260405.18555.0.vbee160e5"},
            ]
        },
    )
    write_json(
        after,
        {
            "versions": [
                {"version": "3.4", "canton_version": "3.4.11"},
                {"version": "3.5", "canton_version": "3.5.5"},
            ]
        },
    )

    assert module.versioned_source_config_changes(before, after, label="JSON Ledger API OpenAPI") == [
        "- JSON Ledger API OpenAPI 3.5 canton_version: "
        "3.5.0-snapshot.20260405.18555.0.vbee160e5 -> 3.5.5"
    ]


def test_artifact_source_config_changes_summarizes_added_versions(tmp_path: Path) -> None:
    module = load_script_module()
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_json(
        before,
        {
            "artifacts": [
                {"group": "com.daml", "artifact": "bindings-java", "versions": ["3.4.11"]},
            ]
        },
    )
    write_json(
        after,
        {
            "artifacts": [
                {"group": "com.daml", "artifact": "bindings-java", "versions": ["3.4.11", "3.5.5"]},
            ]
        },
    )

    assert module.artifact_source_config_changes(before, after, label="Java ledger bindings") == [
        "- Java ledger bindings com.daml:bindings-java versions: added 3.5.5"
    ]


def test_dashboard_changes_includes_vote_only_updates_and_unavailable_state(tmp_path: Path) -> None:
    module = load_script_module()
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    payload = dashboard_payload(splice_version="0.7.4", dar_version="0.1.1")
    payload["versions"]["mainnet"]["advanced"]["darGovernance"] = {"status": "current", "proposals": []}
    write_json(before, payload)
    payload["versions"]["mainnet"]["advanced"]["darGovernance"]["proposals"] = [{"status": "open", "versions": [{"name": "splice-wallet", "version": "0.1.2"}]}]
    write_json(after, payload)
    assert module.dashboard_changes(before, after) == ["- MainNet DAR governance: current; 1 scheduled/proposed package changes."]
    write_json(before, payload)
    payload["versions"]["mainnet"]["advanced"]["darGovernance"]["status"] = "stale"
    write_json(after, payload)
    assert module.dashboard_changes(before, after) == ["- MainNet DAR governance: stale; 1 scheduled/proposed package changes."]
