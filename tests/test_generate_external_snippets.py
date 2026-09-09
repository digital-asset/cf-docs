from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import generate_external_snippets as generator


def test_canton_docs_cn_imports_have_manifest_destinations() -> None:
    """Historical snippet names must still match their configured destinations."""
    config = json.loads(generator.config_path(generator.REPOS["canton"]).read_text())
    destinations = {snippet["snippetName"] for snippet in config["snippets"]}
    imports = {
        name
        for page in (generator.CF_DOCS_ROOT / "docs-main").rglob("*.mdx")
        for name in re.findall(
            r"from [\"']/snippets/external/canton/main/"
            r"(docs-open/target/snippet_json_data/docs-cn/[^\"']+)\.mdx[\"']",
            page.read_text(encoding="utf-8"),
        )
    }

    assert imports
    assert not imports - destinations


def test_copy_helper_and_config_copies_helper(tmp_path: Path) -> None:
    source_dir = tmp_path / "daml-shell"
    helper = generator.copy_helper_and_config(
        generator.REPOS["daml-shell"],
        source_dir,
        dry_run=False,
    )

    target_scripts = source_dir / "scripts" / "docs"
    assert helper == target_scripts / "generateOutputDocs.js"
    assert helper.is_file()
    assert (target_scripts / "exportConfig.json").is_file()


def test_copy_helper_and_config_uses_default_scripts_subdir_for_splice(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "splice"
    helper = generator.copy_helper_and_config(
        generator.REPOS["splice"],
        source_dir,
        dry_run=False,
    )

    target_scripts = source_dir / "scripts" / "docs"
    assert helper == target_scripts / "generateOutputDocs.js"
    assert helper.is_file()
    assert (target_scripts / "exportConfig.json").is_file()
    assert generator.REPOS["splice"].scripts_subdir == "scripts/docs"


def test_validate_inputs_reports_missing_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "cf-docs"
    fake_config = fake_root / "config" / "snippet-config"
    fake_config.mkdir(parents=True)
    (fake_config / "splice-snippet-list-remote.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(generator, "CF_DOCS_ROOT", fake_root)

    with pytest.raises(SystemExit) as error:
        generator.validate_inputs(generator.REPOS["splice"])

    assert "generateOutputDocs.js" in str(error.value)


def test_copy_output_targets_docs_main_snippets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "splice"
    docs_output = source_dir / "docs-output"
    docs_output.mkdir(parents=True)
    (docs_output / "example.mdx").write_text("content", encoding="utf-8")
    fake_root = tmp_path / "cf-docs"

    monkeypatch.setattr(generator, "CF_DOCS_ROOT", fake_root)

    target = generator.copy_output(
        generator.REPOS["splice"],
        source_dir,
        version="main",
        replace=False,
        dry_run=False,
    )

    assert target == fake_root / "docs-main" / "snippets" / "external" / "splice" / "main"
    assert (target / "example.mdx").read_text(encoding="utf-8") == "content"
    assert not (fake_root / "snippets").exists()


def test_wrapper_copies_helper_runs_extraction_and_copies_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_helper = generator.helper_path()
    fake_root = tmp_path / "cf-docs"
    fake_helper = fake_root / "scripts" / "helpers" / "generateOutputDocs.js"
    fake_config = fake_root / "config" / "snippet-config" / "test-snippet-list.json"
    source_dir = tmp_path / "source"

    fake_helper.parent.mkdir(parents=True)
    shutil.copy2(real_helper, fake_helper)
    fake_config.parent.mkdir(parents=True)
    fake_config.write_text(
        """{
  "snippets": [
    {
      "snippetName": "example",
      "sourceRepo": "test",
      "sourceFilepath": "docs/example.txt",
      "location": {
        "type": "stringMarker",
        "start": "SNIPPET_START",
        "end": "SNIPPET_END"
      },
      "options": {
        "language": "text"
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    (source_dir / "docs").mkdir(parents=True)
    (source_dir / "docs" / "example.txt").write_text(
        "before\nSNIPPET_START\nhello\nSNIPPET_END\nafter\n",
        encoding="utf-8",
    )

    repo = generator.SnippetRepo(
        name="test",
        config_name="test-snippet-list.json",
        aliases=("test",),
    )
    monkeypatch.setattr(generator, "CF_DOCS_ROOT", fake_root)

    helper = generator.copy_helper_and_config(repo, source_dir, dry_run=False)
    generator.run_extraction(source_dir, helper, quiet=True, dry_run=False)
    target = generator.copy_output(
        repo,
        source_dir,
        version="main",
        replace=False,
        dry_run=False,
    )

    assert (source_dir / "docs-output" / "example.mdx").read_text(encoding="utf-8") == (
        "```text\nhello\n```"
    )
    assert (target / "example.mdx").read_text(encoding="utf-8") == "```text\nhello\n```"


def _write_string_marker_fixture(
    tmp_path: Path, source_text: str, *, normalize_indent: bool | None = None
) -> tuple[Path, Path]:
    source_dir = tmp_path / "source"
    helper = source_dir / "scripts" / "docs" / "generateOutputDocs.js"
    config = helper.parent / "exportConfig.json"
    source = source_dir / "docs" / "example.txt"

    helper.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    shutil.copy2(generator.helper_path(), helper)
    options: dict[str, object] = {"language": "text"}
    if normalize_indent is not None:
        options["normalizeIndent"] = normalize_indent
    config.write_text(
        json.dumps(
            {
                "snippets": [
                    {
                        "snippetName": "example",
                        "sourceFilepath": "docs/example.txt",
                        "location": {
                            "type": "stringMarker",
                            "start": "SNIPPET_START",
                            "end": "SNIPPET_END",
                        },
                        "options": options,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    source.write_text(source_text, encoding="utf-8")
    return source_dir, helper


def test_string_marker_preserves_first_line_indentation(tmp_path: Path) -> None:
    source_dir, helper = _write_string_marker_fixture(
        tmp_path,
        "before\nSNIPPET_START\n\n    first\n        second\n\nSNIPPET_END\nafter\n",
        normalize_indent=False,
    )

    generator.run_extraction(source_dir, helper, quiet=True, dry_run=False)

    assert (source_dir / "docs-output" / "example.mdx").read_text(
        encoding="utf-8"
    ) == "```text\n    first\n        second\n```"


@pytest.mark.parametrize("duplicate_marker", ["SNIPPET_START", "SNIPPET_END"])
def test_string_marker_rejects_duplicate_markers(
    tmp_path: Path, duplicate_marker: str
) -> None:
    source_dir, helper = _write_string_marker_fixture(
        tmp_path,
        "\n".join(
            [
                "SNIPPET_START",
                "content",
                "SNIPPET_END",
                duplicate_marker,
                "",
            ]
        ),
    )

    result = subprocess.run(
        ["node", helper],
        cwd=source_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert f'Marker must appear exactly once, found 2: "{duplicate_marker}"' in (
        result.stderr
    )
