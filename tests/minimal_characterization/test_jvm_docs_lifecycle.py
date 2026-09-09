from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

from .helpers import assert_contains_all, assert_text_tree_matches_fixture, mdx_file_set, read_mdx, run_x2mdx


def write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).lstrip(), encoding="utf-8")


def write_javadoc_jar(root: Path, version: str, sources: dict[str, str]) -> Path:
    javadoc_version = subprocess.run(
        ["javadoc", "--version"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if not javadoc_version.startswith("javadoc 25."):
        raise AssertionError(f"Expected repo-local Javadoc 25, got: {javadoc_version}")

    source_root = root / "src" / version
    docs_root = root / "javadoc" / version
    jar_path = root / "jars" / "bindings-java" / version / f"bindings-java-{version}-javadoc.jar"

    for relative_path, contents in sources.items():
        write_text(source_root / relative_path, contents)

    java_files = sorted(str(path) for path in source_root.rglob("*.java"))
    subprocess.run(
        [
            "javadoc",
            "-quiet",
            "-Xdoclint:none",
            "-d",
            str(docs_root),
            *java_files,
        ],
        check=True,
        cwd=root,
        text=True,
        capture_output=True,
    )

    jar_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(jar_path, "w") as archive:
        for path in sorted(docs_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(docs_root).as_posix())

    with zipfile.ZipFile(jar_path) as archive:
        members = set(archive.namelist())
    required_members = {
        "type-search-index.js",
        "member-search-index.js",
        "com/example/Foo.html",
    }
    if version in {"1.1.0", "1.2.0"}:
        required_members.add("deprecated-list.html")
    missing = sorted(required_members - members)
    if missing:
        raise AssertionError(f"Javadoc jar {version} is missing expected parser inputs: {missing}")

    return jar_path


def build_java_manifest(root: Path) -> Path:
    write_javadoc_jar(
        root,
        "1.0.0",
        {
            "com/example/Foo.java": """
                package com.example;

                /** Foo summary v1.0.0. */
                public class Foo {
                  /** Old method summary v1.0.0. */
                  public void oldMethod() {}
                }
            """,
            "com/example/Legacy.java": """
                package com.example;

                /** Legacy summary v1.0.0. */
                public class Legacy {}
            """,
        },
    )
    write_javadoc_jar(
        root,
        "1.1.0",
        {
            "com/example/Foo.java": """
                package com.example;

                /** Foo summary v1.1.0. */
                public class Foo {
                  /**
                   * Old method summary v1.1.0.
                   *
                   * @deprecated since 1.1.0 use {@link #newMethod()} instead.
                   */
                  @Deprecated(since = "1.1.0")
                  public void oldMethod() {}

                  /** New method summary v1.1.0. */
                  public void newMethod() {}

                  /** Inner summary v1.1.0. */
                  public static final class Inner {}
                }
            """,
            "com/example/Added.java": """
                package com.example;

                /** Added summary v1.1.0. */
                public class Added {}
            """,
        },
    )
    write_javadoc_jar(
        root,
        "1.2.0",
        {
            "com/example/Foo.java": """
                package com.example;

                /** Foo summary v1.2.0. */
                public class Foo {
                  /** New method summary v1.2.0. */
                  public void newMethod() {}

                  /** Inner summary v1.2.0. */
                  public static final class Inner {}
                }
            """,
            "com/example/Added.java": """
                package com.example;

                /** Added summary v1.2.0. */
                public class Added {}
            """,
            "com/example/ManualDeprecated.java": """
                package com.example;

                /** ManualDeprecated summary v1.2.0. */
                public class ManualDeprecated {}
            """,
            "com/example/NativeDeprecated.java": """
                package com.example;

                /**
                 * NativeDeprecated summary v1.2.0.
                 *
                 * @deprecated since 1.2.0 use {@link Foo} instead.
                 */
                @Deprecated(since = "1.2.0")
                public class NativeDeprecated {}
            """,
        },
    )

    manifest = {
        "source": "minimal Java Javadoc characterization",
        "artifacts": [
            {
                "group": "com.example",
                "artifact": "bindings-java",
                "language": "java",
                "include_prefixes": ["com.example"],
                "versions": [
                    {"version": "1.0.0", "jar_path": "jars/bindings-java/1.0.0/bindings-java-1.0.0-javadoc.jar"},
                    {"version": "1.1.0", "jar_path": "jars/bindings-java/1.1.0/bindings-java-1.1.0-javadoc.jar"},
                    {"version": "1.2.0", "jar_path": "jars/bindings-java/1.2.0/bindings-java-1.2.0-javadoc.jar"},
                ],
            }
        ],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


class JvmDocsMinimalLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _render_pages(self, *, relative_site_root: str = "site") -> Path:
        manifest_path = build_java_manifest(self.root)
        site_root = self.root / relative_site_root
        overview = site_root / "reference" / "jvm-api" / "index.mdx"
        details_dir = site_root / "reference" / "jvm-api" / "details"
        docs_json = site_root / "docs.json"
        docs_json.parent.mkdir(parents=True, exist_ok=True)
        docs_json.write_text(
            json.dumps({"navigation": {"dropdowns": [{"dropdown": "Reference", "pages": []}]}}, indent=2) + "\n",
            encoding="utf-8",
        )

        run_x2mdx(
            [
                "jvm-docs",
                "build-api-pages-from-manifest",
                "--manifest",
                str(manifest_path),
                "--overview-file",
                str(overview),
                "--details-dir",
                str(details_dir),
                "--overview-title",
                "Minimal JVM Docs",
                "--docs-json",
                str(docs_json),
                "--nav-dropdown",
                "Reference",
                "--source-name",
                "minimal Java Javadoc characterization",
                "--version-filter",
                "1.0.0 through 1.2.0",
            ]
        )
        return site_root

    def test_cli_renders_minimal_source_contract(self) -> None:
        site_root = self._render_pages()

        self.assertEqual(
            mdx_file_set(site_root),
            {
                "reference/jvm-api/index.mdx",
                "reference/jvm-api/details/bindings-java.mdx",
                "reference/jvm-api/details/bindings-java-packages/com-example/index.mdx",
                "reference/jvm-api/details/bindings-java-packages/com-example/added.mdx",
                "reference/jvm-api/details/bindings-java-packages/com-example/foo.mdx",
                "reference/jvm-api/details/bindings-java-packages/com-example/foo-inner.mdx",
                "reference/jvm-api/details/bindings-java-packages/com-example/legacy.mdx",
                "reference/jvm-api/details/bindings-java-packages/com-example/manualdeprecated.mdx",
                "reference/jvm-api/details/bindings-java-packages/com-example/nativedeprecated.mdx",
            },
        )
        assert_text_tree_matches_fixture(site_root, "jvm_docs/default")

        overview_text = read_mdx(site_root, "reference/jvm-api/index.mdx")
        artifact_text = read_mdx(site_root, "reference/jvm-api/details/bindings-java.mdx")
        foo_text = read_mdx(site_root, "reference/jvm-api/details/bindings-java-packages/com-example/foo.mdx")
        assert_contains_all(
            overview_text,
            [
                "Minimal JVM Docs",
                "Source name: `minimal Java Javadoc characterization`",
                "Version filter: `1.0.0 through 1.2.0`",
                "`com.example:bindings-java`",
                "`java`",
                "`1.0.0, 1.1.0, 1.2.0`",
            ],
        )
        assert_contains_all(artifact_text, ["## Table of Contents", "## Version Change Summary", "## Reference"])
        assert_contains_all(foo_text, ['title: "Foo"', 'description: "Foo summary v1.2.0."', "**Members**"])

    def test_cli_renders_deprecated_lifecycle_state(self) -> None:
        site_root = self._render_pages(relative_site_root="site-lifecycle")

        assert_text_tree_matches_fixture(site_root, "jvm_docs/default")
        package_text = read_mdx(site_root, "reference/jvm-api/details/bindings-java-packages/com-example/index.mdx")
        legacy_text = read_mdx(site_root, "reference/jvm-api/details/bindings-java-packages/com-example/legacy.mdx")
        native_deprecated_text = read_mdx(
            site_root,
            "reference/jvm-api/details/bindings-java-packages/com-example/nativedeprecated.mdx",
        )
        assert_contains_all(package_text, ["`deprecated`"])
        assert_contains_all(legacy_text, ["## Legacy", "Removed in `1.1.0`."])
        assert_contains_all(native_deprecated_text, ["## NativeDeprecated - deprecated"])

    def test_cli_prunes_stale_output(self) -> None:
        manifest_path = build_java_manifest(self.root)
        site_root = self.root / "site-prune"
        overview = site_root / "reference" / "jvm-api" / "index.mdx"
        details_dir = site_root / "reference" / "jvm-api" / "details"
        stale_file = details_dir / "stale.mdx"
        write_text(stale_file, "stale")

        run_x2mdx(
            [
                "jvm-docs",
                "build-api-pages-from-manifest",
                "--manifest",
                str(manifest_path),
                "--overview-file",
                str(overview),
                "--details-dir",
                str(details_dir),
                "--overview-title",
                "Minimal JVM Docs",
                "--source-name",
                "minimal Java Javadoc characterization",
                "--version-filter",
                "1.0.0 through 1.2.0",
            ]
        )

        self.assertFalse(stale_file.exists())
        assert_text_tree_matches_fixture(site_root, "jvm_docs/prune")

    def test_cli_updates_docs_json_navigation_idempotently(self) -> None:
        site_root = self._render_pages(relative_site_root="site-nav")
        docs_json = site_root / "docs.json"

        manifest_path = self.root / "manifest.json"
        overview = site_root / "reference" / "jvm-api" / "index.mdx"
        details_dir = site_root / "reference" / "jvm-api" / "details"
        run_x2mdx(
            [
                "jvm-docs",
                "build-api-pages-from-manifest",
                "--manifest",
                str(manifest_path),
                "--overview-file",
                str(overview),
                "--details-dir",
                str(details_dir),
                "--overview-title",
                "Minimal JVM Docs",
                "--docs-json",
                str(docs_json),
                "--nav-dropdown",
                "Reference",
                "--source-name",
                "minimal Java Javadoc characterization",
                "--version-filter",
                "1.0.0 through 1.2.0",
            ]
        )

        docs_payload = json.loads(docs_json.read_text(encoding="utf-8"))
        self.assertEqual(docs_payload["navigation"]["dropdowns"][0]["pages"], ["reference/jvm-api/index"])
        assert_text_tree_matches_fixture(docs_json.parent, "jvm_docs/default")
