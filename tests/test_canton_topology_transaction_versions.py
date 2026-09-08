from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import generate_all_reference_docs  # noqa: E402
from scripts import generate_canton_topology_transaction_versions as generator  # noqa: E402


class CantonTopologyTransactionVersionsTests(unittest.TestCase):
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

    def test_aggregate_generator_includes_topology_version_table(self) -> None:
        job = next(
            job
            for job in generate_all_reference_docs.SCRIPT_JOBS
            if job.script_path.name
            == "generate_canton_topology_transaction_versions.py"
        )
        self.assertEqual(job.nav_slices, ())
        self.assertEqual(job.target_ids, ())

    def test_load_rows_validates_shape(self) -> None:
        key = "topologyTransactionProtocolVersionToProtobufVersions"
        self.assertEqual(
            generator.load_rows({key: [["34", ["30"]], ["35", ["30"]]]}),
            [("34", ["30"]), ("35", ["30"])],
        )
        with self.assertRaisesRegex(ValueError, "row 0"):
            generator.load_rows({key: [["34", []]]})

    def test_render_table_includes_all_rows_and_provenance(self) -> None:
        table = generator.render_table(
            [("34", ["30"]), ("35", ["30"])], asset=self.asset
        )
        self.assertIn('source="digital-asset/canton"', table)
        self.assertIn('protocol_version_count="2"', table)
        self.assertIn("| 34 | 30 |", table)
        self.assertIn("| 35 | 30 |", table)

    def test_replace_table_is_idempotent_and_preserves_page(self) -> None:
        page = (
            "Before\n\n"
            "| Protocol Version | Topology Transaction Protobuf Version |\n"
            "| --- | --- |\n"
            "| 34 | 30 |\n\n"
            "After\n"
        )
        table = generator.render_table(
            [("34", ["30"]), ("35", ["30"])], asset=self.asset
        )
        first = generator.replace_table(page, table)
        second = generator.replace_table(first, table)

        self.assertEqual(first, second)
        self.assertIn("Before", first)
        self.assertIn("After", first)
        self.assertEqual(first.count(generator.TABLE_HEADER), 1)


if __name__ == "__main__":
    unittest.main()
