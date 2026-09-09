from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlparse

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "generate_network_component_versions.py"
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


def dashboard_snapshot(*, generated_at: str, splice_version: str) -> dict:
    networks = {}
    for network_key, display_name in [
        ("mainnet", "MainNet"),
        ("testnet", "TestNet"),
        ("devnet", "DevNet"),
    ]:
        networks[network_key] = {
            "sources": {"infoUrl": f"https://example.com/{network_key}/info"},
            "displayName": display_name,
            "migrationId": "1",
            "spliceVersion": splice_version,
            "endpoint": f"scan.{network_key}.example",
            "cantonVersion": "3.5.1",
            "cantonReleaseLineBranch": "release-line-0.6",
            "darVersions": [],
            "darGovernance": {"status": "unavailable", "proposals": []},
        }

    return {
        "generatedAt": generated_at,
        "generatorMode": "public_source_collection_with_manual_fallbacks",
        "networks": networks,
        "damlSdkVersions": {
            "mainnet": "3.5.1",
            "testnet": "3.5.2",
            "devnet": "3.5.3",
        },
        "latestDpm": "1.0.21",
        "latestPqs": "3.5.1",
        "latestWalletGateway": "1.4.0",
        "npmVersions": {
            "tokenStandard": "1.4.0",
            "walletSdk": "1.4.0",
            "dappSdk": "1.1.0",
        },
    }


def daml_sdk_manifest(version: str) -> dict:
    annotations = {
        "org.opencontainers.image.version": version,
        "com.digitalasset.version": version,
    }
    return {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "annotations": annotations,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "annotations": annotations,
                "platform": {"architecture": "amd64", "os": "linux"},
            }
        ],
    }


def test_build_config_preserves_generated_at_when_only_timestamp_changes() -> None:
    module = load_script_module()
    existing_config = module.build_config(
        {"versions": {}, "repositories": {}},
        dashboard_snapshot(
            generated_at="2026-06-01T00:00:00+00:00",
            splice_version="0.6.3",
        ),
    )

    result = module.build_config(
        existing_config,
        dashboard_snapshot(
            generated_at="2026-06-03T12:00:00+00:00",
            splice_version="0.6.3",
        ),
    )

    assert result["_generated"]["generatedAt"] == "2026-06-01T00:00:00+00:00"


def test_build_config_preserves_generated_metadata_when_dashboard_values_do_not_change() -> None:
    module = load_script_module()
    existing_snapshot = dashboard_snapshot(
        generated_at="2026-06-01T00:00:00+00:00",
        splice_version="0.6.3",
    )
    existing_config = module.build_config(
        {"versions": {}, "repositories": {}},
        existing_snapshot,
    )
    candidate_snapshot = dashboard_snapshot(
        generated_at="2026-06-03T12:00:00+00:00",
        splice_version="0.6.3",
    )
    candidate_snapshot["unpublishedProbe"] = "changed"

    result = module.build_config(existing_config, candidate_snapshot)

    assert result == existing_config


def test_build_config_keeps_new_generated_at_when_dashboard_data_changes() -> None:
    module = load_script_module()
    existing_config = module.build_config(
        {"versions": {}, "repositories": {}},
        dashboard_snapshot(generated_at="2026-06-01T00:00:00+00:00", splice_version="0.6.2"),
    )

    result = module.build_config(
        existing_config,
        dashboard_snapshot(generated_at="2026-06-03T12:00:00+00:00", splice_version="0.6.3"),
    )

    assert result["_generated"]["generatedAt"] == "2026-06-03T12:00:00+00:00"


def test_choose_observed_release_accepts_active_synchronizer_payload() -> None:
    module = load_script_module()

    assert module.choose_observed_release(
        {
            "sv": {"migration_id": 4, "version": "0.6.5"},
            "synchronizer": {
                "active": {
                    "migration_id": 4,
                    "version": "0.6.5",
                }
            },
        },
        "https://example.com/info",
    ) == ("0.6.5", "4")


def test_choose_observed_release_accepts_current_synchronizer_payload() -> None:
    module = load_script_module()

    assert module.choose_observed_release(
        {
            "sv": {"migration_id": 1, "serial_id": 2, "version": "0.6.7"},
            "synchronizer": {
                "current": {
                    "serial_id": 2,
                    "version": "0.6.7",
                },
                "legacy": {
                    "serial_id": 1,
                    "version": "0.6.6",
                },
            },
        },
        "https://example.com/info",
    ) == ("0.6.7", "1")


def test_choose_observed_release_prefers_sv_version_matching_index() -> None:
    module = load_script_module()

    assert module.choose_observed_release(
        {
            "sv": {"migration_id": 1, "version": "0.7.0"},
            "synchronizer": {
                "current": {
                    "version": "0.6.14",
                }
            },
        },
        "https://example.com/info",
        index_version="0.7.0",
    ) == ("0.7.0", "1")


def test_choose_observed_release_prefers_sync_version_matching_index() -> None:
    module = load_script_module()

    assert module.choose_observed_release(
        {
            "sv": {"migration_id": 1, "version": "0.7.0"},
            "synchronizer": {
                "current": {
                    "version": "0.6.14",
                }
            },
        },
        "https://example.com/info",
        index_version="0.6.14",
    ) == ("0.6.14", "1")


def test_choose_observed_release_rejects_mismatch_when_neither_matches_index() -> None:
    module = load_script_module()

    with pytest.raises(RuntimeError, match="neither matches docs index version"):
        module.choose_observed_release(
            {
                "sv": {"migration_id": 1, "version": "0.7.0"},
                "synchronizer": {
                    "current": {
                        "version": "0.6.14",
                    }
                },
            },
            "https://example.com/info",
            index_version="0.6.13",
        )


def test_choose_observed_release_rejects_mismatch_without_index_version() -> None:
    module = load_script_module()

    with pytest.raises(RuntimeError, match="Version mismatch"):
        module.choose_observed_release(
            {
                "sv": {"migration_id": 1, "version": "0.7.0"},
                "synchronizer": {
                    "current": {
                        "version": "0.6.14",
                    }
                },
            },
            "https://example.com/info",
        )


def test_network_snapshot_from_existing_rebuilds_required_fields() -> None:
    module = load_script_module()
    existing_config = {
        "_generated": {
            "networkSources": {
                "devnet": {
                    "infoUrl": "https://docs.dev.example/info",
                    "indexUrl": "https://docs.dev.example/index.html",
                    "cantonSourcesUrl": "https://github.com/example/canton-sources",
                    "darVersionsUrl": "https://github.com/example/dars.lock",
                }
            }
        },
        "versions": {
            "devnet": {
                "name": "DevNet",
                "endpoint": "scan.dev.example",
                "advanced": {
                    "migrationId": "1",
                    "darVersions": [{"name": "splice-amulet", "version": "0.1.22"}],
                },
                "substitutions": {"version": "0.6.14"},
            }
        },
        "repositories": {
            "splice": {
                "versionMapping": {
                    "devnet": {"branch": "main", "externalVersion": "0.6.14", "folderPathRepo": ""}
                }
            },
            "canton": {
                "versionMapping": {
                    "devnet": {
                        "branch": "release-line-0.6.14",
                        "externalVersion": "3.5.10",
                        "folderPathRepo": "nix/canton-sources.json",
                    }
                }
            },
        },
    }

    snapshot = module.network_snapshot_from_existing(existing_config, "devnet")
    assert snapshot is not None
    assert snapshot["spliceVersion"] == "0.6.14"
    assert snapshot["cantonVersion"] == "3.5.10"
    assert snapshot["cantonReleaseLineBranch"] == "release-line-0.6.14"
    assert snapshot["migrationId"] == "1"
    assert "chainIdSuffix" not in snapshot
    assert snapshot["preservedFromPrevious"] is True
    assert snapshot["sources"]["preservedFromPrevious"] is True


def test_collect_snapshot_preserves_previous_network_on_failure(monkeypatch) -> None:
    module = load_script_module()
    existing_config = {
        "_generated": {"networkSources": {}},
        "versions": {
            "mainnet": {
                "name": "MainNet",
                "endpoint": "scan.main.example",
                "advanced": {"migrationId": "4", "darVersions": []},
                "substitutions": {"version": "0.6.12"},
            },
            "testnet": {
                "name": "TestNet",
                "endpoint": "scan.test.example",
                "advanced": {"migrationId": "1", "darVersions": []},
                "substitutions": {"version": "0.6.13"},
            },
            "devnet": {
                "name": "DevNet",
                "endpoint": "scan.dev.example",
                "advanced": {"migrationId": "1", "darVersions": []},
                "substitutions": {"version": "0.6.14"},
            },
        },
        "repositories": {
            "splice": {
                "versionMapping": {
                    network: {"branch": "main", "externalVersion": version, "folderPathRepo": ""}
                    for network, version in [
                        ("mainnet", "0.6.12"),
                        ("testnet", "0.6.13"),
                        ("devnet", "0.6.14"),
                    ]
                }
            },
            "canton": {
                "versionMapping": {
                    network: {
                        "branch": f"release-line-{version}",
                        "externalVersion": canton,
                        "folderPathRepo": "nix/canton-sources.json",
                    }
                    for network, version, canton in [
                        ("mainnet", "0.6.12", "3.5.8"),
                        ("testnet", "0.6.13", "3.5.9"),
                        ("devnet", "0.6.14", "3.5.10"),
                    ]
                }
            },
        },
    }

    def fake_collect_network_snapshot(network_key: str, timeout: float) -> dict:
        if network_key == "devnet":
            raise RuntimeError("Version mismatch in https://docs.dev.example/info")
        return {
            "displayName": network_key,
            "endpoint": f"scan.{network_key}.example",
            "spliceVersion": existing_config["versions"][network_key]["substitutions"]["version"],
            "cantonVersion": "3.5.1",
            "cantonReleaseLineBranch": "release-line-x",
            "darVersions": [],
            "darGovernance": {"status": "unavailable", "proposals": []},
            "migrationId": existing_config["versions"][network_key]["advanced"]["migrationId"],
            "sources": {
                "infoUrl": f"https://docs.{network_key}.example/info",
                "indexUrl": f"https://docs.{network_key}.example/index.html",
                "cantonSourcesUrl": "",
                "darVersionsUrl": "",
            },
            "checks": {
                "dockerImageTag": existing_config["versions"][network_key]["substitutions"][
                    "version"
                ],
                "helmChartVersion": existing_config["versions"][network_key]["substitutions"][
                    "version"
                ],
            },
        }

    monkeypatch.setattr(module, "collect_network_snapshot", fake_collect_network_snapshot)
    monkeypatch.setattr(module, "previous_stable_pqs_version", lambda existing_config: "3.5.1")
    monkeypatch.setattr(
        module,
        "collect_daml_sdk_versions",
        lambda timeout, existing_config: {
            "mainnet": "3.5.1",
            "testnet": "3.5.1",
            "devnet": "3.5.1",
        },
    )
    monkeypatch.setattr(module, "fetch_latest_dpm_version", lambda timeout: "1.0.21")
    monkeypatch.setattr(
        module,
        "fetch_pqs_version_from_scribe_component",
        lambda timeout, previous_stable_version=None: "3.5.1",
    )
    monkeypatch.setattr(module, "fetch_latest_wallet_gateway_version", lambda timeout: "1.4.0")
    monkeypatch.setattr(module, "fetch_npm_latest", lambda package_name, timeout: "1.0.0")

    monkeypatch.setattr(module, "collect_dar_governance", lambda *args: (
        [], {"status": "current", "sourceUrl": "https://scan.example/dso", "votesUrl": "https://scan.example/votes", "proposals": []}
    ))

    snapshot = module.collect_snapshot(timeout=1.0, existing_config=existing_config)

    assert snapshot["networks"]["mainnet"]["spliceVersion"] == "0.6.12"
    assert snapshot["networks"]["testnet"]["spliceVersion"] == "0.6.13"
    assert snapshot["networks"]["devnet"]["spliceVersion"] == "0.6.14"
    assert snapshot["networks"]["devnet"]["preservedFromPrevious"] is True
    assert "Version mismatch" in snapshot["networks"]["devnet"]["sources"]["preserveReason"]


def test_collect_snapshot_raises_when_failed_network_has_no_previous(monkeypatch) -> None:
    module = load_script_module()

    def fake_collect_network_snapshot(network_key: str, timeout: float) -> dict:
        raise RuntimeError(f"{network_key} boom")

    monkeypatch.setattr(module, "collect_network_snapshot", fake_collect_network_snapshot)
    monkeypatch.setattr(module, "previous_stable_pqs_version", lambda existing_config: "3.5.1")

    with pytest.raises(RuntimeError, match="no previous dashboard config"):
        module.collect_snapshot(timeout=1.0, existing_config={"versions": {}, "repositories": {}})


def test_latest_stable_version_ignores_prerelease_and_debug_tags() -> None:
    module = load_script_module()

    assert (
        module.latest_stable_version(
            [
                "3.4.6",
                "3.5.1-rc7",
                "3.5.1-rc7-debug",
                "3.5.1",
                "3.5.1-debug",
            ],
            "test",
        )
        == "3.5.1"
    )


def test_collect_daml_sdk_versions_uses_each_network_tag(monkeypatch) -> None:
    module = load_script_module()
    expected_versions = {
        "mainnet": "3.5.5",
        "testnet": "3.5.6",
        "devnet": "3.5.7",
    }
    seen_urls: list[str] = []

    def fake_fetch_manifest_json(url: str, timeout: float) -> dict:
        seen_urls.append(url)
        network_key = url.rsplit("/", 1)[-1]
        assert network_key in expected_versions
        return daml_sdk_manifest(expected_versions[network_key])

    monkeypatch.setattr(module, "fetch_manifest_json", fake_fetch_manifest_json)

    assert module.collect_daml_sdk_versions(
        timeout=1.0,
        existing_config={"repositories": {}},
    ) == expected_versions
    assert seen_urls == [
        f"{module.DAML_SDK_MANIFEST_BASE_URL}/{network_key}"
        for network_key in module.NETWORK_ORDER
    ]


def test_fetch_daml_sdk_manifest_version_rejects_annotation_mismatch(monkeypatch) -> None:
    module = load_script_module()
    manifest = daml_sdk_manifest("3.5.3")
    manifest["annotations"]["com.digitalasset.version"] = "3.5.2"
    monkeypatch.setattr(module, "fetch_manifest_json", lambda url, timeout: manifest)

    with pytest.raises(RuntimeError, match="version annotation mismatch"):
        module.fetch_daml_sdk_manifest_version("mainnet", timeout=1.0)


def test_daml_sdk_manifest_url_rejects_unknown_tag() -> None:
    module = load_script_module()

    with pytest.raises(ValueError, match="Expected Daml SDK network tag"):
        module.daml_sdk_manifest_url("3.5.4-rc1")


def test_collect_daml_sdk_versions_preserves_only_failed_network_value(monkeypatch) -> None:
    module = load_script_module()
    existing_config = {
        "repositories": {
            "damlSdk": {
                "versionMapping": {
                    "mainnet": {"externalVersion": "3.5.5"},
                    "testnet": {"externalVersion": "3.5.6"},
                    "devnet": {"externalVersion": "3.5.7"},
                }
            }
        }
    }

    def fetch_daml_sdk_manifest_version(network_key: str, timeout: float) -> str:
        if network_key == "testnet":
            raise RuntimeError("registry unavailable")
        return {"mainnet": "3.5.8", "devnet": "3.5.10"}[network_key]

    monkeypatch.setattr(
        module,
        "fetch_daml_sdk_manifest_version",
        fetch_daml_sdk_manifest_version,
    )

    assert module.collect_daml_sdk_versions(1.0, existing_config) == {
        "mainnet": "3.5.8",
        "testnet": "3.5.6",
        "devnet": "3.5.10",
    }


def test_build_config_records_network_daml_sdk_manifest_sources() -> None:
    module = load_script_module()

    config = module.build_config(
        {"versions": {}, "repositories": {}},
        dashboard_snapshot(
            generated_at="2026-08-05T12:00:00+00:00",
            splice_version="0.7.0",
        ),
    )

    assert config["repositories"]["damlSdk"]["url"] == (
        f"https://{module.DAML_SDK_MANIFEST_REPOSITORY}"
    )
    assert config["repositories"]["damlSdk"]["versionMapping"]["mainnet"] == {
        "branch": "",
        "externalVersion": "3.5.1",
        "folderPathRepo": "",
    }
    assert config["repositories"]["damlSdk"]["versionMapping"]["testnet"][
        "externalVersion"
    ] == "3.5.2"
    assert config["repositories"]["damlSdk"]["versionMapping"]["devnet"][
        "externalVersion"
    ] == "3.5.3"
    assert (
        module.DAML_SDK_VERSION_ANNOTATION
        in config["_generated"]["sourceContract"]["damlSdk"]
    )
    assert "moving mainnet, testnet, or devnet tag" in config["_generated"]["sourceContract"][
        "damlSdk"
    ]


def test_previous_stable_pqs_version_uses_existing_dashboard_config() -> None:
    module = load_script_module()

    assert (
        module.previous_stable_pqs_version(
            {
                "repositories": {
                    "pqs": {
                        "versionMapping": {
                            "mainnet": {"externalVersion": "3.5.1"},
                            "testnet": {"externalVersion": "3.5.2"},
                            "devnet": {"externalVersion": "3.5.2-rc1"},
                        }
                    }
                }
            }
        )
        == "3.5.2"
    )


def test_fetch_pqs_version_from_scribe_component_uses_stable_annotation(monkeypatch) -> None:
    module = load_script_module()

    def fake_fetch_manifest_json(url: str, timeout: float) -> dict:
        assert url == module.PQS_SCRIBE_MANIFEST_URL
        return {
            "annotations": {
                "org.opencontainers.image.version": "3.5.3",
                "com.digitalasset.version": "3.5.3",
            }
        }

    monkeypatch.setattr(module, "fetch_manifest_json", fake_fetch_manifest_json)

    assert (
        module.fetch_pqs_version_from_scribe_component(
            timeout=1.0,
            previous_stable_version="3.5.2",
        )
        == "3.5.3"
    )


def test_fetch_pqs_version_from_scribe_component_keeps_previous_stable_for_prerelease(
    monkeypatch,
) -> None:
    module = load_script_module()

    def fake_fetch_manifest_json(url: str, timeout: float) -> dict:
        assert url == module.PQS_SCRIBE_MANIFEST_URL
        return {
            "annotations": {
                "org.opencontainers.image.version": "3.5.3-rc2",
                "com.digitalasset.version": "3.5.3-rc2",
            }
        }

    monkeypatch.setattr(module, "fetch_manifest_json", fake_fetch_manifest_json)

    assert (
        module.fetch_pqs_version_from_scribe_component(
            timeout=1.0,
            previous_stable_version="3.5.2",
        )
        == "3.5.2"
    )


def test_build_config_records_pqs_scribe_component_source() -> None:
    module = load_script_module()

    config = module.build_config(
        {"versions": {}, "repositories": {}},
        dashboard_snapshot(
            generated_at="2026-06-03T12:00:00+00:00",
            splice_version="0.6.3",
        ),
    )

    assert config["repositories"]["pqs"]["url"] == (
        f"https://{module.PQS_SCRIBE_COMPONENT_REPOSITORY}"
    )
    assert config["repositories"]["pqs"]["versionMapping"]["mainnet"] == {
        "branch": "",
        "externalVersion": "3.5.1",
        "folderPathRepo": (
            f"{module.PQS_SCRIBE_COMPONENT_REPOSITORY}:"
            f"{module.PQS_SCRIBE_RELEASE_LINE_TAG}"
        ),
    }
    assert "org.opencontainers.image.version" in config["_generated"]["sourceContract"]["pqs"]


def test_request_headers_use_github_token_for_github_api(monkeypatch) -> None:
    module = load_script_module()
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    assert module.request_headers("https://api.github.com/repos/example/project/releases") == {
        "User-Agent": module.USER_AGENT,
        "Authorization": "Bearer test-token",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def test_request_headers_do_not_send_github_token_to_other_hosts(monkeypatch) -> None:
    module = load_script_module()
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    assert module.request_headers("https://registry.npmjs.org/example") == {
        "User-Agent": module.USER_AGENT,
    }


def test_fetch_latest_wallet_gateway_version_paginates_releases(monkeypatch) -> None:
    module = load_script_module()
    requested_urls: list[str] = []

    def fake_fetch_json(url: str, timeout: float) -> list[dict[str, str]]:
        requested_urls.append(url)
        page = parse_qs(urlparse(url).query).get("page", ["1"])[0]
        if page == "1":
            return [{"tag_name": "@canton-network/core-wallet-store@1.7.0"}]
        if page == "2":
            return [{"tag_name": "@canton-network/wallet-gateway-remote@1.4.0"}]
        return []

    monkeypatch.setattr(module, "fetch_json", fake_fetch_json)

    assert module.fetch_latest_wallet_gateway_version(timeout=1.0) == "1.4.0"
    assert requested_urls == [
        f"{module.WALLET_GATEWAY_RELEASES_URL}?per_page=100&page=1",
        f"{module.WALLET_GATEWAY_RELEASES_URL}?per_page=100&page=2",
        f"{module.WALLET_GATEWAY_RELEASES_URL}?per_page=100&page=3",
    ]
