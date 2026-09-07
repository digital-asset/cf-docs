from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import generate_all_reference_docs  # noqa: E402
from scripts import generate_canton_release_protocol_versions as generator  # noqa: E402


class CantonReleaseProtocolVersionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asset = generator.ReleaseAsset(
            tag="v3.5.15",
            version="3.5.15",
            name="canton-open-source-3.5.15.tar.gz",
            url="https://github.com/digital-asset/canton/releases/download/v3.5.15/canton-open-source-3.5.15.tar.gz",
            size=123,
            digest="sha256:" + "a" * 64,
        )

    def test_uses_public_release_source(self) -> None:
        self.assertEqual(generator.DEFAULT_RELEASE_REPO, "digital-asset/canton")
        self.assertNotIn(
            "DACH-NY", generator.REFERENCE_SCRIPT.read_text(encoding="utf-8")
        )

    def test_aggregate_generator_includes_release_protocol_table(self) -> None:
        job = next(
            job
            for job in generate_all_reference_docs.SCRIPT_JOBS
            if job.script_path.name == "generate_canton_release_protocol_versions.py"
        )
        self.assertEqual(job.nav_slices, ())
        self.assertEqual(job.target_ids, ())

    def test_load_rows_validates_shape(self) -> None:
        self.assertEqual(
            generator.load_rows(
                {"releaseVersionsToProtocolVersions": [["3.5", ["34", "35"]]]}
            ),
            [("3.5", ["34", "35"])],
        )
        with self.assertRaisesRegex(ValueError, "row 0"):
            generator.load_rows({"releaseVersionsToProtocolVersions": [["3.5", []]]})

    def test_render_section_includes_beta_legend_and_provenance(self) -> None:
        section = generator.render_section(
            [("2.9", ["5", "6*"]), ("3.5", ["34", "35"])],
            asset=self.asset,
        )
        self.assertIn('source="digital-asset/canton"', section)
        self.assertIn('release_line_count="2"', section)
        self.assertIn("| 3.5 | 34, 35 |", section)
        self.assertIn("trailing asterisk identifies a beta", section)

    def test_replace_section_is_idempotent_and_preserves_page(self) -> None:
        page = "Frontmatter\n\nExisting release guidance.\n"
        section = generator.render_section([("3.5", ["34", "35"])], asset=self.asset)
        first = generator.replace_section(page, section)
        second = generator.replace_section(first, section)

        self.assertEqual(first, second)
        self.assertIn("Existing release guidance.", first)
        self.assertEqual(first.count(generator.GENERATED_START), 1)


if __name__ == "__main__":
    unittest.main()
