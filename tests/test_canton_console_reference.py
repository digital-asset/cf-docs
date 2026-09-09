from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import generate_all_reference_docs  # noqa: E402
from scripts import generate_canton_console_reference as generator  # noqa: E402


def console_item(
    name: str,
    *,
    topic: list[str],
    scope: str = "Stable",
    description: str = "Description",
) -> generator.ConsoleItem:
    return {
        "name": name,
        "arguments": [["argument", "String"]],
        "return_type": "Boolean",
        "summary": f"Summary for {name}",
        "description": description,
        "topic": topic,
        "scope": scope,
    }


class CantonConsoleReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asset = generator.ReleaseAsset(
            tag="v3.5.15",
            version="3.5.15",
            name="canton-open-source-3.5.15.tar.gz",
            url=(
                "https://github.com/digital-asset/canton/releases/download/"
                "v3.5.15/canton-open-source-3.5.15.tar.gz"
            ),
            size=123,
            digest="sha256:" + "a" * 64,
        )
        self.source = generator.PublicSourceArtifact(
            repo="digital-asset/canton",
            ref=self.asset.tag,
            commit="b" * 40,
            path=generator.CONSOLE_TEMPLATE_PATH,
            blob="c" * 40,
            content=""".. _canton_console_reference:

.. note::
    test note

Console Commands
================

Top-level Commands
------------------

<console-topic-marker: Top-level Commands>

Participant Commands
--------------------

<console-topic-marker: Participant>

<console-topic-marker: Participant, DAR Management>

Multiple Participants
---------------------

<console-topic-marker: Multiple Participants, DAR Management>

Sequencer Administration Commands
---------------------------------

<console-topic-marker: Sequencer, Traffic>

Mediator Administration Commands
--------------------------------

<console-topic-marker: Mediator, Health>
""",
        )

    def test_defaults_use_public_canton_release(self) -> None:
        self.assertEqual(generator.DEFAULT_RELEASE_REPO, "digital-asset/canton")
        self.assertEqual(
            generator.CONSOLE_TEMPLATE_PATH,
            "docs-open/src/main/resources/console.rst.template",
        )
        self.assertNotIn(
            "DACH-NY", generator.REFERENCE_SCRIPT.read_text(encoding="utf-8")
        )

    def test_aggregate_generator_includes_console_without_claiming_an_x2mdx_target(
        self,
    ) -> None:
        job = next(
            job
            for job in generate_all_reference_docs.SCRIPT_JOBS
            if job.script_path.name == "generate_canton_console_reference.py"
        )

        self.assertEqual(job.nav_slices, ())
        self.assertEqual(job.target_ids, ())

    def test_resolve_release_asset_requires_the_full_public_distribution(self) -> None:
        payload = {
            "tag_name": "v3.5.15",
            "assets": [
                {
                    "name": "canton-open-source-3.5.15.tar.gz",
                    "browser_download_url": self.asset.url,
                    "size": self.asset.size,
                    "digest": self.asset.digest,
                }
            ],
        }
        original = generator.github_api_json
        try:
            generator.github_api_json = lambda _path: payload
            resolved = generator.resolve_release_asset(
                release_repo="digital-asset/canton", tag=None
            )
        finally:
            generator.github_api_json = original

        self.assertEqual(resolved, self.asset)

    def test_resolve_public_source_artifact_verifies_the_git_blob(self) -> None:
        content = b"Console Commands\n================\n"
        blob = generator.git_blob_sha(content)
        payloads = {
            "commits": {"sha": "d" * 40},
            "contents": {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(content).decode(),
                "sha": blob,
            },
        }
        original = generator.github_api_json
        try:
            generator.github_api_json = lambda path: (
                payloads["contents"] if "/contents/" in path else payloads["commits"]
            )
            resolved = generator.resolve_public_source_artifact(
                repo="digital-asset/canton",
                ref="v3.5.15",
                path=generator.CONSOLE_TEMPLATE_PATH,
            )
        finally:
            generator.github_api_json = original

        self.assertEqual(resolved.commit, "d" * 40)
        self.assertEqual(resolved.blob, blob)
        self.assertEqual(resolved.content, content.decode())
        header = f"blob {len(content)}\0".encode()
        self.assertEqual(hashlib.sha1(header + content).hexdigest(), blob)

    def test_load_console_items_rejects_invalid_shapes(self) -> None:
        with self.assertRaisesRegex(ValueError, "console list"):
            generator.load_console_items({})
        with self.assertRaisesRegex(ValueError, "invalid topics"):
            generator.load_console_items(
                {"console": [{**console_item("bad", topic=["Topic"]), "topic": []}]}
            )

    def test_render_console_reference_covers_every_item_and_disambiguates_anchors(
        self,
    ) -> None:
        items = [
            console_item(
                "help",
                topic=["Top-level Commands"],
                description='Type help("<command>").',
            ),
            console_item("help", topic=["Participant"]),
            console_item(
                "repair.contract",
                topic=["Repair", "Active Contract Store"],
                scope="Repair",
            ),
            console_item("dars.upload", topic=["Participant", "DAR Management"]),
            console_item(
                "traffic.set", topic=["Sequencer", "Traffic"], scope="Preview"
            ),
            console_item("health.status", topic=["Mediator", "Health"]),
            console_item(
                "dars.upload", topic=["Multiple Participants", "DAR Management"]
            ),
        ]

        mdx, rendered_count = generator.render_console_reference(
            items, asset=self.asset, source=self.source
        )

        self.assertIn('template_source="digital-asset/canton:', mdx)
        self.assertIn('ref="v3.5.15"', mdx)
        self.assertIn('raw_command_count="7"', mdx)
        self.assertIn('rendered_command_count="6"', mdx)
        self.assertEqual(rendered_count, 6)
        self.assertEqual(mdx.count('<div id="'), rendered_count + 1)
        self.assertIn('<div id="help" />', mdx)
        self.assertIn('<div id="help_1" />', mdx)
        self.assertIn("## Participant Commands", mdx)
        self.assertIn("### `traffic.set`", mdx)
        self.assertIn(r'Type help("\<command\>").', mdx)
        self.assertNotIn("repair.contract", mdx)

    def test_render_console_reference_preserves_current_content_corrections(
        self,
    ) -> None:
        items = [
            console_item(
                "resources.set_resource_limits",
                topic=["Participant"],
                description=(
                    "Resource limits can only be changed, if the server runs Canton enterprise. "
                    "In the community edition, the server uses fixed limits that cannot be changed."
                ),
            )
        ]

        source = replace(
            self.source,
            content=(
                "Console Commands\n"
                "================\n\n"
                "Participant Commands\n"
                "--------------------\n\n"
                "<console-topic-marker: Participant>\n"
            ),
        )
        mdx, rendered_count = generator.render_console_reference(
            items, asset=self.asset, source=source
        )

        self.assertEqual(rendered_count, 1)
        self.assertIn(
            "Resource limits can be changed at runtime using this command.", mdx
        )
        self.assertNotIn("Canton enterprise", mdx)

    def test_generate_reference_json_caches_release_binary_output(self) -> None:
        payload = {"console": [console_item("help", topic=["Top-level Commands"])]}
        with TemporaryDirectory() as tmp:
            temporary_root = Path(tmp)
            distribution_root = temporary_root / "distribution"
            (distribution_root / "bin").mkdir(parents=True)
            (distribution_root / generator.SIMPLE_TOPOLOGY_CONFIG).parent.mkdir(
                parents=True
            )
            original_run = generator.subprocess.run
            calls: list[tuple[list[str], Path, dict[str, str]]] = []

            def fake_run(
                command: list[str],
                *,
                cwd: Path,
                env: dict[str, str],
                check: bool,
                text: bool,
                stdout: int,
            ) -> subprocess.CompletedProcess[str]:
                self.assertTrue(check)
                self.assertTrue(text)
                self.assertEqual(stdout, subprocess.PIPE)
                calls.append((command, cwd, env))
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(payload)
                )

            try:
                generator.subprocess.run = fake_run
                first = generator.generate_reference_json(
                    distribution_root=distribution_root,
                    cache_dir=temporary_root / "cache",
                    asset=self.asset,
                    force_refresh=False,
                )
                second = generator.generate_reference_json(
                    distribution_root=distribution_root,
                    cache_dir=temporary_root / "cache",
                    asset=self.asset,
                    force_refresh=False,
                )
                refreshed = generator.generate_reference_json(
                    distribution_root=distribution_root,
                    cache_dir=temporary_root / "cache",
                    asset=self.asset,
                    force_refresh=True,
                )
            finally:
                generator.subprocess.run = original_run

        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        self.assertEqual(refreshed, payload)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][1], "run")
        self.assertNotIn("CI", calls[0][2])


if __name__ == "__main__":
    unittest.main()
