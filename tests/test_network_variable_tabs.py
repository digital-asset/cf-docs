from __future__ import annotations

import importlib.util
import json
import shutil

import pytest
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


def make_source(repo: Path) -> Path:
    source = repo / "docs-source"
    data = source / "snippets/generated/version-dashboard-data.mdx"
    data.parent.mkdir(parents=True)
    data.write_text("export const networkData = " + json.dumps(sample_network_data()) + ";\n")
    (source / "docs.json").write_text('{"name": "Example"}\n')
    (source / "example.mdx").write_text(
        '---\ntitle: Example\n---\n\n<NetworkVariables>\nScan URL: |gsf_scan_url|\n</NetworkVariables>\n'
    )
    (source / "ordinary.md").write_text('Plain page with a [link](/example).\n')
    (source / "image.png").write_bytes(b"\x89PNG\r\n\x00\xff")
    return source


def snapshot(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_full_corpus_build_preserves_source_and_copies_non_network_files(tmp_path: Path) -> None:
    module = load_script_module()
    source = make_source(tmp_path)
    output = tmp_path / "docs-main"
    before = snapshot(source)
    assert module.generate_site(source, output)
    assert snapshot(source) == before
    assert snapshot(output).keys() == before.keys()
    for relative, content in before.items():
        if relative != Path("example.mdx"):
            assert (output / relative).read_bytes() == content
    assert "Scan URL: https://scan.dev.example" in (output / "example.mdx").read_text()
    assert "<NetworkVariables>" not in (output / "example.mdx").read_text()
    assert module.generate_site(source, output) == []
    assert module.generate_site(source, output, check=True) == []


@pytest.mark.parametrize("change", ["network", "ordinary", "binary", "missing", "extra", "deleted-source"])
def test_check_detects_all_target_drift_without_writing(tmp_path: Path, change: str) -> None:
    module = load_script_module()
    source = make_source(tmp_path)
    output = tmp_path / "docs-main"
    module.generate_site(source, output)
    if change == "network":
        (source / "example.mdx").write_text('<NetworkVariables>Updated |gsf_scan_url|</NetworkVariables>')
    elif change == "ordinary":
        (output / "ordinary.md").write_text("Already dirty before the check")
    elif change == "binary":
        (output / "image.png").write_bytes(b"corrupted")
    elif change == "missing":
        (output / "example.mdx").unlink()
    elif change == "extra":
        (output / "untracked.mdx").write_text("stale page")
    else:
        (source / "ordinary.md").unlink()
    source_before, output_before = snapshot(source), snapshot(output)
    assert module.generate_site(source, output, check=True)
    assert snapshot(source) == source_before
    assert snapshot(output) == output_before
    assert module.generate_site(source, output)
    assert not module.generate_site(source, output, check=True)


def test_build_handles_file_and_directory_renames(tmp_path: Path) -> None:
    module = load_script_module()
    source = make_source(tmp_path)
    output = tmp_path / "docs-main"
    module.generate_site(source, output)
    (source / "ordinary.md").unlink()
    (source / "ordinary.md").mkdir()
    (source / "ordinary.md/index.mdx").write_text("Moved page")
    module.generate_site(source, output)
    assert (output / "ordinary.md/index.mdx").read_text() == "Moved page"
    shutil.rmtree(source / "ordinary.md")
    (source / "ordinary.md").write_text("Moved back")
    module.generate_site(source, output)
    assert (output / "ordinary.md").read_text() == "Moved back"


def test_invalid_source_cannot_partially_update_output(tmp_path: Path) -> None:
    module = load_script_module()
    source = make_source(tmp_path)
    output = tmp_path / "docs-main"
    module.generate_site(source, output)
    before = snapshot(output)
    (source / "example.mdx").write_text(
        'import Missing from "/snippets/missing.mdx";\n\n<NetworkVariables><Missing /></NetworkVariables>'
    )
    with pytest.raises(FileNotFoundError, match="Missing imported snippet"):
        module.generate_site(source, output)
    assert snapshot(output) == before
    with pytest.raises(ValueError, match="non-overlapping"):
        module.generate_site(source, source)


def test_validator_checks_existing_dirty_and_untracked_targets_without_mutating(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    output = tmp_path / "docs-main"
    load_script_module().generate_site(source, output)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("generate_network_variable_tabs.py", "validate_network_variable_tabs.py"):
        shutil.copyfile(REPO_ROOT / "scripts" / name, scripts / name)
    for args in (["init"], ["add", "."], ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    command = [sys.executable, str(scripts / "validate_network_variable_tabs.py")]
    current = subprocess.run(command, capture_output=True, text=True)
    assert current.returncode == 0
    assert "rendered and up to date" in current.stdout
    (output / "example.mdx").write_text("already dirty")
    (output / "untracked.mdx").write_text("extra output")
    before = snapshot(output)
    stale = subprocess.run(command, capture_output=True, text=True)
    assert stale.returncode == 1
    assert "Network variable tabs are stale" in stale.stderr
    assert "docs-main/example.mdx" in stale.stderr
    assert "docs-main/untracked.mdx" in stale.stderr
    assert snapshot(output) == before


def test_source_pages_own_network_blocks_and_all_output_is_current() -> None:
    module = load_script_module()
    sources = list((REPO_ROOT / "docs-source").rglob("*.mdx"))
    assert any("<NetworkVariables>" in path.read_text() for path in sources)
    assert all("NETWORKVARS_START" not in path.read_text() for path in sources)
    assert module.generate_site(check=True) == []


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
