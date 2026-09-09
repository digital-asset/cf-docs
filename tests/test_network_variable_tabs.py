from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "generate_network_variable_tabs.py"
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


def sample_network_data() -> dict[str, object]:
    return {
        "devnet": {
            "name": "DevNet",
            "versions": {"splice": "0.6.4"},
            "substitutions": {
                "gsf_scan_url": "https://scan.dev.example",
                "bundle_download_link": {
                    "label": "Download Bundle",
                    "href": "https://download.dev.example/bundle.tgz",
                },
            },
        },
        "testnet": {
            "name": "TestNet",
            "versions": {"splice": "0.6.3"},
            "substitutions": {
                "gsf_scan_url": "https://scan.test.example",
                "bundle_download_link": {
                    "label": "Download Bundle",
                    "href": "https://download.test.example/bundle.tgz",
                },
            },
        },
    }


def test_load_network_data_preserves_export_in_substitution_strings(tmp_path: Path) -> None:
    module = load_script_module()
    network_data = tmp_path / "version-dashboard-data.mdx"
    network_data.write_text(
        """export const lastUpdatedAt = '2026-07-28T08:35:56+00:00';
export const lastUpdatedLabel = 'July 28, 2026';

export const networkData = {
  mainnet: {
    name: 'MainNet',
    versions: { splice: '0.6.12' },
    substitutions: {
      image_tag_set: 'export IMAGE_TAG=0.6.12',
      chart_version_set: 'export CHART_VERSION=0.6.12',
    },
  },
};
""",
        encoding="utf-8",
    )

    loaded = module.load_network_data(network_data)

    assert loaded["mainnet"]["substitutions"]["image_tag_set"] == "export IMAGE_TAG=0.6.12"
    assert loaded["mainnet"]["substitutions"]["chart_version_set"] == "export CHART_VERSION=0.6.12"


def test_render_generated_block_inlines_imported_snippets_and_substitutes_tokens(tmp_path: Path) -> None:
    module = load_script_module()
    docs_main = tmp_path / "docs-main"
    imported = docs_main / "snippets" / "external" / "sample.mdx"
    imported.parent.mkdir(parents=True)
    imported.write_text(
        """```bash
curl |gsf_scan_url|/api/scan/v0/dso-sequencers
```
""",
        encoding="utf-8",
    )
    source = """import ExternalSample from "/snippets/external/sample.mdx";

Download from |bundle_download_link|.

<div data-network-only="devnet">
DevNet only: |gsf_scan_url|
</div>

- Nested command:

  <ExternalSample />

> <ExternalSample />

<ExternalSample />
"""

    generated = module.render_generated_block(
        "/snippets/networkvars/example.mdx",
        source,
        sample_network_data(),
        docs_main,
    )

    assert '<Tabs>' in generated
    assert '<Tab title="DevNet (0.6.4)">' in generated
    assert '<Tab title="TestNet (0.6.3)">' in generated
    assert "https://scan.dev.example/api/scan/v0/dso-sequencers" in generated
    assert "https://scan.test.example/api/scan/v0/dso-sequencers" in generated
    assert "  ```bash\n  curl https://scan.dev.example/api/scan/v0/dso-sequencers\n  ```" in generated
    assert "> ```bash\n> curl https://scan.dev.example/api/scan/v0/dso-sequencers\n> ```" in generated
    assert "\n  \n" not in generated
    assert "\n> \n" not in generated
    assert '<a href="https://download.dev.example/bundle.tgz">Download Bundle (DevNet 0.6.4)</a>' in generated
    testnet_tab = generated.split('<Tab title="TestNet (0.6.3)">', 1)[1]
    assert "DevNet only" not in testnet_tab
    assert "|gsf_scan_url|" not in generated


def test_marked_page_update_is_idempotent(tmp_path: Path) -> None:
    module = load_script_module()
    docs_main = tmp_path / "docs-main"
    source = docs_main / "snippets" / "networkvars" / "example.mdx"
    source.parent.mkdir(parents=True)
    source.write_text("Scan URL: |gsf_scan_url|\n", encoding="utf-8")
    page = docs_main / "example.mdx"
    page.write_text(
        """---
title: Example
---

{/* NETWORKVARS_START source="/snippets/networkvars/example.mdx" */}
stale
{/* NETWORKVARS_END */}
""",
        encoding="utf-8",
    )

    prefix = 'import Existing from "/snippets/existing.mdx";\n\n\nEditor prose before.\n\n'
    suffix = '\n\n\nEditor prose after.\n'
    page.write_text(prefix + page.read_text() + suffix, encoding="utf-8")
    before = page.read_bytes()
    assert module.update_page(page, sample_network_data(), docs_main, check=True)
    assert page.read_bytes() == before
    assert module.update_page(page, sample_network_data(), docs_main)
    first = page.read_text(encoding="utf-8")
    assert "Scan URL: https://scan.dev.example" in first
    assert "stale" not in first
    assert first.startswith(prefix)
    assert first.endswith(suffix)
    assert not module.update_page(page, sample_network_data(), docs_main)
    assert page.read_text(encoding="utf-8") == first


def test_validate_script_detects_dirty_stale_output_without_writing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    docs_main = repo / "docs-main"
    source = docs_main / "snippets" / "networkvars" / "example.mdx"
    network_data = docs_main / "snippets" / "generated" / "version-dashboard-data.mdx"
    source.parent.mkdir(parents=True)
    network_data.parent.mkdir(parents=True)
    source.write_text("Scan URL: |gsf_scan_url|\n", encoding="utf-8")
    network_data.write_text(
        """export const networkData = {
  devnet: {
    name: "DevNet",
    versions: { splice: "0.6.4" },
    substitutions: { gsf_scan_url: "https://scan.dev.example" },
  },
};
""",
        encoding="utf-8",
    )
    page = docs_main / "example.mdx"
    page.write_text(
        """{/* NETWORKVARS_START source="/snippets/networkvars/example.mdx" */}
stale
{/* NETWORKVARS_END */}
""",
        encoding="utf-8",
    )
    script_dir = repo / "scripts"
    script_dir.mkdir()
    generator = REPO_ROOT / "scripts" / "generate_network_variable_tabs.py"
    validator = REPO_ROOT / "scripts" / "validate_network_variable_tabs.py"
    (script_dir / generator.name).write_text(generator.read_text(encoding="utf-8"), encoding="utf-8")
    (script_dir / validator.name).write_text(validator.read_text(encoding="utf-8"), encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    page.write_text(page.read_text() + "\nUncommitted editor prose.\n", encoding="utf-8")
    before = page.read_bytes()
    stale = subprocess.run(
        ["python3", "scripts/validate_network_variable_tabs.py"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert stale.returncode == 1
    assert "Network variable tabs are stale" in stale.stderr
    assert "docs-main/example.mdx" in stale.stderr
    assert page.read_bytes() == before

    subprocess.run(
        [sys.executable, "scripts/generate_network_variable_tabs.py"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert page.read_text().endswith("\nUncommitted editor prose.\n")

    subprocess.run(["git", "add", "docs-main/example.mdx"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "render"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    current = subprocess.run(
        ["python3", "scripts/validate_network_variable_tabs.py"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert current.returncode == 0
    assert "Network variable tabs are rendered and up to date." in current.stdout


def test_generation_preserves_ordinary_editor_files(tmp_path: Path) -> None:
    module = load_script_module()
    docs_main = tmp_path / "docs-main"
    docs_main.mkdir()
    page = docs_main / "new-page.mdx"
    page.write_text('---\ntitle: New page\n---\n\n\nEditor content.\n', encoding="utf-8")
    navigation = docs_main / "docs.json"
    navigation.write_text('{"navigation":{"pages":["new-page"]}}\n', encoding="utf-8")
    image = docs_main / "new-image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\neditor-upload")
    before = {path: path.read_bytes() for path in docs_main.iterdir()}

    for check in (True, False):
        assert not any(
            module.update_page(path, sample_network_data(), docs_main, check=check)
            for path in module.iter_pages(docs_main)
        )
        assert {path: path.read_bytes() for path in docs_main.iterdir()} == before


def test_checked_in_network_variable_pages_are_static_tabs() -> None:
    generated_pages = [
        path
        for path in (REPO_ROOT / "docs-main").rglob("*.mdx")
        if "NETWORKVARS_START" in path.read_text(encoding="utf-8")
    ]
    assert generated_pages
    for page in generated_pages:
        text = page.read_text(encoding="utf-8")
        assert "<NetworkVariables" not in text
        assert "from '/snippets/components/version.mdx'" not in text
        assert "<Tabs>" in text
        assert "<Tab title=" in text

    onboarding = REPO_ROOT / "docs-main" / "global-synchronizer" / "deployment" / "onboarding-process.mdx"
    onboarding_text = onboarding.read_text(encoding="utf-8")
    assert "scan.sv-1.dev.global.canton.network.sync.global" in onboarding_text
    assert "dso-sequencers" in onboarding_text
