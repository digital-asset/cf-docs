from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate_all_reference_docs  # noqa: E402
from x2mdx.history.io import load_history_report  # noqa: E402
from x2mdx.history.validation import validate_history_report  # noqa: E402
from reference_target_inventory import (  # noqa: E402
    load_reference_target_inventory,
    validate_runner_targets,
)


EXPECTED_TARGET_IDS = {
    "admin-api-protobuf",
    "daml-script",
    "daml-standard-library",
    "java-bindings",
    "json-ledger-api-asyncapi",
    "json-ledger-api-openapi",
    "ledger-api-grpc",
    "ledger-api-protobuf",
    "splice-openapi",
    "splice-token-standard-v2-daml",
    "typescript-bindings",
    "wallet-gateway-openrpc",
}


def runner_targets() -> dict[str, tuple[str, ...]]:
    return {
        job.script_path.relative_to(REPO_ROOT).as_posix(): tuple(sorted(job.target_ids))
        for job in generate_all_reference_docs.SCRIPT_JOBS
        if job.target_ids
    }


def test_inventory_declares_every_current_reader_target() -> None:
    inventory = load_reference_target_inventory()

    assert set(inventory.by_id()) == EXPECTED_TARGET_IDS
    assert len(inventory.targets) == 12
    validate_runner_targets(inventory, runner_targets())


def test_every_target_converges_on_checked_in_mdx() -> None:
    inventory = load_reference_target_inventory()

    assert {target.target_page_renderer for target in inventory.targets} == {
        "x2mdx_mdx"
    }
    assert {
        target.id
        for target in inventory.targets
        if target.current_page_renderer == "native_mintlify_openapi"
    } == {"json-ledger-api-openapi", "splice-openapi"}


def test_every_target_has_valid_normalized_history_and_resolvable_current_pages() -> None:
    inventory = load_reference_target_inventory()
    declared_reports = {
        report_path
        for target in inventory.targets
        for report_path in target.history_report_paths
    }
    discovered_reports = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "docs-main").rglob("*history-report.json")
    }
    assert discovered_reports == declared_reports

    checked_pages: set[Path] = set()
    for target in inventory.targets:
        for report_path in target.history_report_paths:
            report = load_history_report(REPO_ROOT / report_path)
            validate_history_report(report)
            assert report.format.value == target.format
            assert report.items
            for item in report.current_items():
                assert item.route is not None
                route = item.route.split("#", 1)[0].lstrip("/")
                page_path = REPO_ROOT / "docs-main" / f"{route}.mdx"
                assert page_path.is_file(), (item.id, page_path)
                checked_pages.add(page_path)

    assert checked_pages
    for page_path in checked_pages:
        text = page_path.read_text(encoding="utf-8")
        assert "x2mdx-ref-page" in text, page_path
        assert "Present since at least" not in text, page_path
        if "## History" in text:
            history_tail = text.rsplit("## History", 1)[1]
            assert re.search(r"^## ", history_tail, re.MULTILINE) is None, page_path


def test_separate_details_and_history_pages_are_absent() -> None:
    inventory = load_reference_target_inventory()
    for target in inventory.targets:
        for output_root in target.reader_output_roots:
            path = REPO_ROOT / output_root
            pages = [path] if path.is_file() else path.rglob("*.mdx")
            for page in pages:
                assert "Details and history" not in page.read_text(encoding="utf-8")


def test_scala_is_not_an_active_reader_target() -> None:
    inventory = load_reference_target_inventory()

    assert "scala-bindings" not in inventory.by_id()
    assert all(
        "scala" not in output_root
        for target in inventory.targets
        for output_root in target.reader_output_roots
    )


def test_inventory_rejects_runner_drift() -> None:
    inventory = load_reference_target_inventory()
    drifted = runner_targets()
    drifted["scripts/generate_new_reference.py"] = ("new-reference",)

    try:
        validate_runner_targets(inventory, drifted)
    except ValueError as error:
        assert "runner generators missing from inventory" in str(error)
    else:
        raise AssertionError("Expected aggregate-runner drift to fail validation")
