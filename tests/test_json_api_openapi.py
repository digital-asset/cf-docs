from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

from x2mdx.render import render_page


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script_module(script_name: str) -> ModuleType:
    script_path = REPO_ROOT / "scripts" / script_name
    scripts_dir = str(script_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_add_missing_operation_summaries_uses_method_path_labels_for_mintlify_nav() -> (
    None
):
    module = load_script_module("generate_json_api_reference.py")
    source = """  openapi: 3.0.3
  paths:
    /v2/commands/submit-and-wait:
      post:
        description: Submit and wait.
        operationId: postV2CommandsSubmit-and-wait
    /v2/version:
      get:
        summary: Existing summary
        description: Read the version.
        operationId: getV2Version
  components: {}
"""

    rendered = module.add_missing_operation_summaries(source)

    assert '        summary: "POST /v2/commands/submit-and-wait"' in rendered
    assert "summary: Existing summary" in rendered
    assert 'summary: "/v2/commands/submit-and-wait"' not in rendered
    assert module.missing_operation_summaries(module.yaml.safe_load(rendered)) == set()


def test_add_missing_operation_summaries_disambiguates_methods_on_same_path() -> None:
    module = load_script_module("generate_json_api_reference.py")
    source = """openapi: 3.0.3
paths:
  /v2/users/{user-id}:
    get:
      description: Get user.
      operationId: getV2UsersUser-id
    delete:
      description: Delete user.
      operationId: deleteV2UsersUser-id
    patch:
      description: Update user.
      operationId: patchV2UsersUser-id
components: {}
"""

    rendered = module.add_missing_operation_summaries(source)
    spec = module.yaml.safe_load(rendered)
    operations = spec["paths"]["/v2/users/{user-id}"]

    assert operations["get"]["summary"] == "GET /v2/users/:user-id"
    assert operations["delete"]["summary"] == "DELETE /v2/users/:user-id"
    assert operations["patch"]["summary"] == "PATCH /v2/users/:user-id"


def test_add_missing_operation_summaries_preserves_specs_that_already_have_summaries() -> (
    None
):
    module = load_script_module("generate_json_api_reference.py")
    source = """openapi: 3.0.3
paths:
  /v2/version:
    get:
      summary: /v2/version
      description: Read the version.
      operationId: getV2Version
components: {}
"""

    assert module.add_missing_operation_summaries(source) == source


def test_sanitize_internal_todos_removes_only_standalone_tracker_lines() -> None:
    module = load_script_module("generate_json_api_reference.py")
    source = """openapi: 3.0.3
paths:
  /v2/parties:
    post:
      summary: Allocate a party
      description: |-
        TODO(#27670) support synchronizer aliases
        Synchronizer ID on which to onboard the party.

        Required
      example: TODO(#12345) remains because it is not a standalone line
components: {}
"""

    assert (
        module.sanitize_internal_todos(source)
        == """openapi: 3.0.3
paths:
  /v2/parties:
    post:
      summary: Allocate a party
      description: |-
        Synchronizer ID on which to onboard the party.

        Required
      example: TODO(#12345) remains because it is not a standalone line
components: {}
"""
    )


def test_normalize_mintlify_openapi_text_sanitizes_todos_and_adds_summaries() -> None:
    module = load_script_module("generate_json_api_reference.py")
    source = """openapi: 3.0.3
paths:
  /v2/parties:
    post:
      description: |-
        TODO(#27670) support synchronizer aliases
        Allocate a party.
components: {}
"""

    rendered = module.normalize_mintlify_openapi_text(source)

    assert "TODO(#27670)" not in rendered
    assert '      summary: "POST /v2/parties"' in rendered
    assert module.missing_operation_summaries(module.yaml.safe_load(rendered)) == set()


def test_openapi_operation_page_refs_lists_endpoint_refs_in_source_order() -> None:
    module = load_script_module("generate_json_api_reference.py")
    spec = {
        "paths": {
            "/v2/packages": {
                "get": {"summary": "/v2/packages"},
                "post": {"summary": "/v2/packages"},
                "parameters": [],
            },
            "/v2/version": {
                "get": {"summary": "/v2/version"},
            },
        }
    }

    assert module.openapi_operation_page_refs(spec) == [
        "GET /v2/packages",
        "POST /v2/packages",
        "GET /v2/version",
    ]


def test_openapi_navigation_replaces_only_configured_manual_operations() -> None:
    module = load_script_module("generate_json_api_reference.py")
    spec = {
        "paths": {
            "/v2/updates": {"post": {"summary": "POST /v2/updates"}},
            "/v2/updates/flats": {"post": {"summary": "POST /v2/updates/flats"}},
        }
    }
    manual_operations = module.configured_manual_operations(
        {
            "manual_operations": [
                {
                    "method": "post",
                    "path": "/v2/updates/flats",
                    "page_ref": "reference/json-api-reference/post-v2updatesflats",
                }
            ]
        }
    )

    assert manual_operations[0]["method"] == "POST"
    assert module.openapi_navigation_page_refs(
        spec, manual_operations=manual_operations
    ) == [
        "POST /v2/updates",
        "reference/json-api-reference/post-v2updatesflats",
    ]


def test_all_manual_operations_preserve_native_mintlify_routes() -> None:
    module = load_script_module("generate_json_api_reference.py")
    spec = {
        "paths": {
            "/v2/packages/{package-id}/status": {
                "get": {"operationId": "getPackageStatus"}
            },
            "/v2/interactive-submission/executeAndWait": {
                "post": {"operationId": "executeAndWait"}
            },
        }
    }

    operations = module.configured_manual_operations(
        {"manual_operations": "all"},
        spec=spec,
        directory="reference/json-api-reference",
    )

    assert operations == [
        {
            "method": "GET",
            "path": "/v2/packages/{package-id}/status",
            "page_ref": "reference/json-api-reference/get-v2packages:package-idstatus",
        },
        {
            "method": "POST",
            "path": "/v2/interactive-submission/executeAndWait",
            "page_ref": "reference/json-api-reference/post-v2interactive-submissionexecuteandwait",
        },
    ]
    assert all(
        not module.is_native_openapi_page_ref(page_ref)
        for page_ref in module.openapi_navigation_page_refs(
            spec, manual_operations=operations
        )
    )


def test_all_manual_operations_require_published_spec() -> None:
    module = load_script_module("generate_json_api_reference.py")

    try:
        module.configured_manual_operations({"manual_operations": "all"})
    except ValueError as error:
        assert "requires the published OpenAPI spec" in str(error)
    else:
        raise AssertionError("Expected all-operation mode without a spec to fail")


def test_manual_openapi_config_rejects_duplicate_operation_identity() -> None:
    module = load_script_module("generate_json_api_reference.py")
    operation = {
        "method": "POST",
        "path": "/v2/updates/flats",
        "page_ref": "reference/json-api-reference/post-v2updatesflats",
    }

    try:
        module.configured_manual_operations(
            {
                "manual_operations": [
                    operation,
                    {**operation, "page_ref": "reference/duplicate"},
                ]
            }
        )
    except ValueError as error:
        assert "duplicate method/path" in str(error)
    else:
        raise AssertionError("Expected duplicate manual operation identity to fail")


def test_update_docs_navigation_supports_product_navigation(tmp_path: Path) -> None:
    module = load_script_module("generate_json_api_reference.py")
    docs_json = tmp_path / "docs.json"
    docs_json.write_text(
        json.dumps(
            {
                "navigation": {
                    "products": [
                        {
                            "product": "API Reference",
                            "pages": [
                                "api-reference",
                                {
                                    "group": "Ledger API",
                                    "pages": [
                                        {
                                            "group": "OpenAPI",
                                            "openapi": {
                                                "source": "stale.yaml",
                                                "directory": "stale-directory",
                                            },
                                            "pages": ["stale-page"],
                                        },
                                        {
                                            "group": "AsyncAPI",
                                            "pages": ["reference/asyncapi"],
                                        },
                                    ],
                                },
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    module.update_docs_navigation(
        docs_json_path=docs_json,
        dropdown_label="API Reference",
        parent_group_label="Ledger API",
        group_label="OpenAPI",
        openapi_source_ref="openapi/json-ledger-api/openapi.yaml",
        openapi_directory="reference/json-api-reference",
        overview_page_ref="reference/json-api-reference/overview",
        details_page_ref="reference/json-api-reference/details",
        openapi_page_refs=["GET /v2/users", "POST /v2/users"],
    )

    docs = json.loads(docs_json.read_text(encoding="utf-8"))
    ledger_pages = docs["navigation"]["products"][0]["pages"][1]["pages"]

    assert ledger_pages == [
        {
            "group": "OpenAPI",
            "openapi": {
                "source": "openapi/json-ledger-api/openapi.yaml",
                "directory": "reference/json-api-reference",
            },
            "pages": [
                "GET /v2/users",
                "POST /v2/users",
                "reference/json-api-reference/details",
            ],
        },
        {"group": "AsyncAPI", "pages": ["reference/asyncapi"]},
    ]


def test_update_docs_navigation_removes_native_openapi_and_history_page(
    tmp_path: Path,
) -> None:
    module = load_script_module("generate_json_api_reference.py")
    docs_json = tmp_path / "docs.json"
    docs_json.write_text(
        json.dumps(
            {
                "navigation": {
                    "products": [
                        {
                            "product": "API Reference",
                            "pages": [
                                {
                                    "group": "Ledger API",
                                    "pages": [
                                        {
                                            "group": "OpenAPI",
                                            "openapi": {"source": "stale.yaml"},
                                            "pages": [
                                                "GET /v2/users",
                                                "reference/json-api-reference/details",
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    module.update_docs_navigation(
        docs_json_path=docs_json,
        dropdown_label="API Reference",
        parent_group_label="Ledger API",
        group_label="OpenAPI",
        openapi_source_ref="openapi/json-ledger-api/openapi.yaml",
        openapi_directory="reference/json-api-reference",
        overview_page_ref="reference/json-api-reference/overview",
        details_page_ref="reference/json-api-reference/details",
        openapi_page_refs=["reference/json-api-reference/get-v2users"],
    )

    docs = json.loads(docs_json.read_text(encoding="utf-8"))
    group = docs["navigation"]["products"][0]["pages"][0]["pages"][0]
    assert group == {
        "group": "OpenAPI",
        "pages": [
            "reference/json-api-reference/overview",
            "reference/json-api-reference/get-v2users",
        ],
    }


def test_operation_summary_uses_descriptions_for_generated_method_path_summaries() -> (
    None
):
    module = load_script_module("generate_json_api_reference.py")
    path_item = {
        "get": {
            "summary": "GET /v2/users/:user-id",
            "description": "Get user.",
        },
        "patch": {
            "summary": "PATCH /v2/users/:user-id",
            "description": "Update user.",
        },
    }

    assert (
        module.operation_summary("/v2/users/{user-id}", path_item)
        == "GET: Get user.; PATCH: Update user."
    )


def test_build_openapi_details_page_uses_reference_overview_layout() -> None:
    module = load_script_module("generate_json_api_reference.py")
    specs = {
        "3.4": {
            "paths": {
                "/v2/version": {
                    "get": {
                        "summary": "/v2/version",
                        "description": "Read the version.",
                    }
                }
            }
        },
        "3.5": {
            "paths": {
                "/v2/version": {
                    "get": {
                        "summary": "/v2/version",
                        "description": "Read the Ledger API version.",
                    }
                },
                "/readyz": {
                    "get": {
                        "summary": "/readyz",
                        "description": "Check readiness.",
                    }
                },
            }
        },
    }

    rendered = render_page(
        module.build_openapi_details_page(
            specs_by_version=specs,
            versions=["3.4", "3.5"],
            publish_version="3.5",
            details_page_ref="reference/json-api-reference/details",
            source_name="unit test OpenAPI fixtures",
        )
    )

    assert '<div class="x2mdx-ref-hero">' in rendered
    assert '<p class="x2mdx-ref-eyebrow">OpenAPI Reference</p>' in rendered
    assert '<a class="x2mdx-ref-card"' not in rendered
    assert '<div class="x2mdx-ref-card x2mdx-ref-card--static">' in rendered
    assert "Changed 3.5" in rendered
    assert "## Endpoint Reference (Latest)" not in rendered


def test_openapi_overview_links_raw_spec_without_history_label() -> None:
    module = load_script_module("generate_json_api_reference.py")

    rendered = render_page(
        module.build_openapi_overview_page(
            overview_page_ref="reference/json-api-reference/overview",
            publish_version="3.5",
            source_name="release fixtures",
            raw_spec_ref="openapi/json-ledger-api/openapi.yaml",
            operation_count=67,
        )
    )

    assert 'title: "JSON Ledger API OpenAPI"' in rendered
    assert "Operations</dt>" in rendered
    assert "67</dd>" in rendered
    assert "(/openapi/json-ledger-api/openapi.yaml)" in rendered
    assert "Details and history" not in rendered


def test_ensure_redirect_is_idempotent(tmp_path: Path) -> None:
    module = load_script_module("generate_json_api_reference.py")
    docs_json = tmp_path / "docs.json"
    docs_json.write_text('{"redirects": []}\n', encoding="utf-8")

    for _ in range(2):
        module.ensure_redirect(
            docs_json_path=docs_json,
            source="/reference/json-api-reference/details",
            destination="/reference/json-api-reference/overview",
        )

    assert json.loads(docs_json.read_text(encoding="utf-8"))["redirects"] == [
        {
            "source": "/reference/json-api-reference/details",
            "destination": "/reference/json-api-reference/overview",
        }
    ]


def test_checked_in_json_openapi_target_is_fully_manual_and_conformant() -> None:
    module = load_script_module("generate_json_api_reference.py")
    source_config = json.loads(
        (REPO_ROOT / "config/x2mdx/ledger-api/source-artifacts.json").read_text(
            encoding="utf-8"
        )
    )
    spec = module.yaml.safe_load(
        (REPO_ROOT / "docs-main/openapi/json-ledger-api/openapi.yaml").read_text(
            encoding="utf-8"
        )
    )
    operations = module.configured_manual_operations(
        source_config,
        spec=spec,
        directory="reference/json-api-reference",
    )
    expected_page_refs = [operation["page_ref"] for operation in operations]
    output_directory = REPO_ROOT / "docs-main/reference/json-api-reference"
    expected_files = {
        REPO_ROOT / f"docs-main/{page_ref}.mdx" for page_ref in expected_page_refs
    } | {output_directory / "overview.mdx"}
    assert set(output_directory.glob("*.mdx")) == expected_files

    docs_json = json.loads(
        (REPO_ROOT / "docs-main/docs.json").read_text(encoding="utf-8")
    )
    pages = module.reference_nav.navigation_pages(
        docs_json,
        label="API Reference",
        docs_json_path=REPO_ROOT / "docs-main/docs.json",
    )
    ledger_group = module._find_group(pages, "Ledger API")
    assert ledger_group is not None
    openapi_group = module._find_group(ledger_group["pages"], "OpenAPI")
    assert openapi_group == {
        "group": "OpenAPI",
        "pages": ["reference/json-api-reference/overview", *expected_page_refs],
    }
    assert {
        "source": "/reference/json-api-reference/details",
        "destination": "/reference/json-api-reference/overview",
    } in docs_json["redirects"]

    for operation in operations:
        output_path = REPO_ROOT / f"docs-main/{operation['page_ref']}.mdx"
        rendered = output_path.read_text(encoding="utf-8")
        sidebar_path = re.sub(r"\{([^{}]+)\}", r":\1", operation["path"])
        assert '\napi: "' in rendered
        assert f'title: "{operation["method"]} {sidebar_path}"' in rendered
        assert f'sidebarTitle: "{sidebar_path}"' in rendered
        assert "\n## History\n" in rendered
        assert "lifecycle events" not in rendered.lower()
        assert "details and history" not in rendered.lower()
        badge_block = rendered[
            rendered.index('<div class="x2mdx-ref-badges">') : rendered.index(
                "</div>", rendered.index('<div class="x2mdx-ref-badges">')
            )
        ]
        assert "Present since at least" not in badge_block
        added_position = badge_block.find("Added")
        updated_position = badge_block.find("Updated")
        if added_position >= 0 and updated_position >= 0:
            assert added_position < updated_position
