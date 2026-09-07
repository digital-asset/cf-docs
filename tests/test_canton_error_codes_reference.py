from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import generate_all_reference_docs  # noqa: E402
from scripts import generate_canton_error_codes_reference as generator  # noqa: E402


def error_code(
    code: str,
    *,
    grouping: list[str],
    explanation: str | None = "Explanation",
) -> generator.ErrorCodeItem:
    return {
        "className": f"com.example.{code}",
        "category": "InvalidIndependentOfSystemState",
        "hierarchicalGrouping": grouping,
        "conveyance": "Shown to the caller.",
        "code": code,
        "explanation": explanation,
        "resolution": "Correct the request and retry.",
    }


class CantonErrorCodesReferenceTests(unittest.TestCase):
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

    def test_aggregate_generator_includes_error_codes(self) -> None:
        job = next(
            job
            for job in generate_all_reference_docs.SCRIPT_JOBS
            if job.script_path.name == "generate_canton_error_codes_reference.py"
        )
        self.assertEqual(job.nav_slices, ())
        self.assertEqual(job.target_ids, ())

    def test_load_error_codes_rejects_invalid_shapes(self) -> None:
        with self.assertRaisesRegex(ValueError, "errorCodes list"):
            generator.load_error_codes({})
        with self.assertRaisesRegex(ValueError, "invalid grouping"):
            generator.load_error_codes(
                {
                    "errorCodes": [
                        {
                            **error_code("BAD", grouping=["Group"]),
                            "hierarchicalGrouping": [],
                        }
                    ]
                }
            )

    def test_render_inventory_groups_and_covers_every_item(self) -> None:
        items = [
            error_code("BETA", grouping=["Participant", "Commands"]),
            error_code("ALPHA", grouping=["Participant", "Commands"]),
            error_code(
                "ALPHA", grouping=["Sequencer"], explanation="Use <value> safely"
            ),
        ]
        rendered = generator.render_inventory(items, asset=self.asset)

        self.assertIn('source="digital-asset/canton"', rendered)
        self.assertIn('error_code_count="3"', rendered)
        self.assertIn("### Participant › Commands", rendered)
        self.assertIn('<div id="error-code-alpha" />', rendered)
        self.assertIn('<div id="error-code-alpha-2" />', rendered)
        self.assertIn(r"Use \<value\>", rendered)
        self.assertEqual(rendered.count("#### `"), len(items))
        self.assertNotIn("This inventory is generated", rendered)

    def test_render_inventory_omits_missing_upstream_prose(self) -> None:
        item = error_code("ALPHA", grouping=["Participant"])
        item["explanation"] = None
        item["resolution"] = None
        item["conveyance"] = None

        rendered = generator.render_inventory([item], asset=self.asset)

        self.assertNotIn("Explanation:", rendered)
        self.assertNotIn("Resolution:", rendered)
        self.assertNotIn("Conveyance:", rendered)
        self.assertNotIn("Not documented", rendered)
        self.assertIn("- **Category:** `InvalidIndependentOfSystemState`", rendered)

    def test_replace_inventory_preserves_hand_authored_content(self) -> None:
        legacy = (
            "Intro\n\n## Error Codes Inventory\n\nold inventory\n\n"
            "{/* COPIED_END */}\n\nOperator guidance\n"
        )
        replacement = generator.render_inventory(
            [error_code("ALPHA", grouping=["Group"])], asset=self.asset
        )
        first = generator.replace_inventory(legacy, replacement)
        second = generator.replace_inventory(first, replacement)

        self.assertEqual(first, second)
        self.assertIn("Intro", first)
        self.assertIn("Operator guidance", first)
        self.assertNotIn("old inventory", first)
        self.assertLess(
            first.index(generator.COPIED_END), first.index(generator.GENERATED_START)
        )


if __name__ == "__main__":
    unittest.main()
