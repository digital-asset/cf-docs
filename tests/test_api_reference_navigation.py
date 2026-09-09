from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def find_product(products: list[object], label: str) -> dict[str, object]:
    return next(item for item in products if isinstance(item, dict) and item.get("product") == label)


def find_group(items: list[object], label: str) -> dict[str, object]:
    return next(item for item in items if isinstance(item, dict) and item.get("group") == label)


def test_api_reference_landing_page_is_dropdown_root_and_index_page() -> None:
    docs = json.loads((REPO_ROOT / "docs-main" / "docs.json").read_text(encoding="utf-8"))
    products = docs["navigation"]["products"]
    api_reference = find_product(products, "API Reference")

    assert api_reference["root"] == "api-reference"
    assert api_reference["pages"][0] == "api-reference"
    assert any(
        isinstance(item, dict) and item.get("group") == "Ledger API"
        for item in api_reference["pages"]
    )

    frontmatter = (REPO_ROOT / "docs-main" / "api-reference.mdx").read_text(encoding="utf-8")
    assert 'sidebarTitle: "API Reference Index"' in frontmatter


def test_splice_daml_package_nav_lives_under_api_reference_splice_apis() -> None:
    docs = json.loads((REPO_ROOT / "docs-main" / "docs.json").read_text(encoding="utf-8"))
    products = docs["navigation"]["products"]
    api_reference = find_product(products, "API Reference")
    api_splice = find_group(api_reference["pages"], "Splice APIs")
    splice_packages = find_group(api_splice["pages"], "Splice Daml Packages")
    splice_amulet = find_group(splice_packages["pages"], "splice-amulet")
    splice_amulet_root = "sdks-tools/api-reference/splice-daml/splice-amulet"

    assert api_splice["pages"][:3] == [
        "sdks-tools/api-reference/splice-daml-apis",
        "sdks-tools/api-reference/splice-daml-models",
        find_group(api_splice["pages"], "Splice Daml Packages"),
    ]
    assert f"{splice_amulet_root}/index" not in splice_amulet["pages"]
    assert f"{splice_amulet_root}/splice-amulet" in splice_amulet["pages"]
    index_page = REPO_ROOT / "docs-main" / splice_amulet_root / "index.mdx"
    assert not index_page.exists()

    models_page = (
        REPO_ROOT / "docs-main" / "sdks-tools" / "api-reference" / "splice-daml-models.mdx"
    ).read_text(encoding="utf-8")
    assert f"](/{splice_amulet_root})" not in models_page

    sdks_tools = find_product(products, "SDKs and Tools")
    api_overview = find_group(sdks_tools["groups"], "API Overview")
    sdks_splice = find_group(api_overview["pages"], "Splice APIs")

    assert "sdks-tools/api-reference/splice-daml-apis" not in sdks_splice["pages"]
    assert "sdks-tools/api-reference/splice-daml-models" not in sdks_splice["pages"]
    assert all(
        not (isinstance(item, dict) and item.get("group") == "Splice Daml Packages")
        for item in sdks_splice["pages"]
    )


def test_product_selector_has_visible_accent_line() -> None:
    styles = (REPO_ROOT / "docs-main" / "styles.css").read_text(encoding="utf-8")

    assert ".nav-dropdown-products-selector-trigger" in styles
    assert "--canton-product-selector-line-active: #734BE2;" in styles
    assert "box-shadow: inset 0 0 0 2px var(--canton-product-selector-line-active)" in styles


def test_product_selector_groups_reference_utility_links() -> None:
    docs = json.loads((REPO_ROOT / "docs-main" / "docs.json").read_text(encoding="utf-8"))
    products = docs["navigation"]["products"]

    assert [item["product"] for item in products[-3:]] == [
        "API Reference",
        "Release Notes",
        "Version Dashboard",
    ]

    integrations = next(item for item in products if item["product"] == "Integrations")
    assert "integrations/wallet/release-notes" not in json.dumps(integrations)

    styles = (REPO_ROOT / "docs-main" / "styles.css").read_text(encoding="utf-8")
    assert "--canton-product-selector-divider" in styles
    assert '.nav-dropdown-products-selector-content > a[href="/api-reference"]' in styles
    assert "border-top: 1px solid var(--canton-product-selector-divider)" in styles


def test_site_uses_brand_kit_logo_assets() -> None:
    docs = json.loads((REPO_ROOT / "docs-main" / "docs.json").read_text(encoding="utf-8"))

    assert docs["favicon"] == "/images/canton-logo-dark.svg"
    assert docs["logo"]["light"] == "/images/canton-logo-dark.svg"
    assert docs["logo"]["dark"] == "/images/canton-logo-white.svg"

    for logo_path in docs["logo"]["light"], docs["logo"]["dark"]:
        svg_path = REPO_ROOT / "docs-main" / logo_path.removeprefix("/")
        assert svg_path.exists()
        svg = svg_path.read_text(encoding="utf-8")
        assert 'width="1432"' in svg
        assert 'height="369"' in svg
        assert "<text" not in svg
