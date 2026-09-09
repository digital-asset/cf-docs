#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docs_env import ensure_repo_direnv, repo_direnv_command
from generated_reference_sources.typescript_bindings import stable_npm_versions, version_key

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "x2mdx"
DEFAULT_SOURCE_CONFIG = REPO_ROOT / "config" / "x2mdx" / "typescript-bindings" / "source-artifacts.json"
DEFAULT_CACHE_DIR = DEFAULT_CACHE_ROOT / "typescript-bindings"
DEFAULT_MANIFEST = REPO_ROOT / ".internal" / "generated" / "x2mdx" / "typescript-bindings" / "manifest.json"
DEFAULT_TYPEDOC_DIR = REPO_ROOT / ".internal" / "generated" / "x2mdx" / "typescript-bindings" / "typedoc"
DEFAULT_OUTPUT_FILE = REPO_ROOT / "docs-main" / "reference" / "typescript.mdx"
LEGACY_OUTPUT_FILE = REPO_ROOT / "docs-main" / "sdks-tools" / "language-bindings" / "typescript.mdx"
DEFAULT_DOCS_JSON = REPO_ROOT / "docs-main" / "docs.json"
DEFAULT_LIFECYCLE_METADATA = REPO_ROOT / "config" / "x2mdx" / "typescript-bindings" / "lifecycle.json"
DEFAULT_NAV_GROUP = "TypeScript"
LEGACY_NAV_GROUPS = ("Daml TypeScript Bindings",)


@dataclass(frozen=True)
class TypeScriptPackageConfig:
    package_name: str
    versions: list[str]
    publish_version: str
    typedoc_version: str
    entry_point: str
    typedoc_args: list[str]
    source_name: str
    version_filter: str
    page_title: str
    page_description: str
    output_file: Path
    history_report: Path
    reader_route: str
    surface_id: str
    cache_key: str


def nav_group_labels(group_label: str) -> set[str]:
    return {group_label, *LEGACY_NAV_GROUPS}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the TypeScript bindings reference from published @daml/types npm tarballs via local TypeDoc JSON."
    )
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--typedoc-dir", default=str(DEFAULT_TYPEDOC_DIR))
    parser.add_argument("--manifest-out", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output-file", default=str(DEFAULT_OUTPUT_FILE))
    parser.add_argument("--docs-json", default=str(DEFAULT_DOCS_JSON))
    parser.add_argument("--lifecycle-metadata", default=str(DEFAULT_LIFECYCLE_METADATA))
    parser.add_argument("--nav-dropdown", default="API Reference")
    parser.add_argument("--nav-group", default=DEFAULT_NAV_GROUP)
    parser.add_argument("--version", action="append", help="Version to include. Repeat to limit generation.")
    parser.add_argument("--publish-version", help="Version whose TypeScript surface should be published.")
    parser.add_argument("--force-regenerate", action="store_true")
    parser.add_argument(
        "--source-name",
        default="Published @daml/types npm tarballs rendered to local TypeDoc JSON",
        help="Source label embedded in generated content.",
    )
    parser.add_argument(
        "--version-filter",
        default="configured @daml/types npm versions",
        help="Version-filter label embedded in generated content.",
    )
    parser.add_argument(
        "--page-title",
        default="@daml/types",
        help="Title to use for the generated page.",
    )
    parser.add_argument(
        "--page-description",
        default="TypeScript and JavaScript language bindings for Canton.",
        help="Description to use for the generated page.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def docs_json_page_ref(path: Path, docs_json_path: Path) -> str:
    relative = path.resolve().relative_to(docs_json_path.resolve().parent)
    if relative.suffix != ".mdx":
        raise ValueError(f"Expected MDX file under docs root, got: {path}")
    return relative.with_suffix("").as_posix()


def prune_nav_items(items: list[Any], *, page_refs: set[str], group_label: str) -> list[Any]:
    pruned: list[Any] = []
    group_labels = nav_group_labels(group_label)
    for item in items:
        if isinstance(item, str):
            if item not in page_refs:
                pruned.append(item)
            continue
        if isinstance(item, dict):
            if item.get("group") in group_labels:
                continue
            updated = dict(item)
            pages = updated.get("pages")
            if isinstance(pages, list):
                updated["pages"] = prune_nav_items(pages, page_refs=page_refs, group_label=group_label)
            pruned.append(updated)
            continue
        pruned.append(item)
    return pruned


def nav_group_index(items: list[Any], *, group_label: str) -> int | None:
    group_labels = nav_group_labels(group_label)
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("group") in group_labels:
            return index
    return None


def api_reference_pages(docs: dict[str, Any], *, label: str, docs_json_path: Path) -> list[Any]:
    navigation = docs.get("navigation")
    if not isinstance(navigation, dict):
        raise ValueError(f"docs.json missing navigation object: {docs_json_path}")

    dropdowns = navigation.get("dropdowns")
    if isinstance(dropdowns, list):
        dropdown = next((item for item in dropdowns if isinstance(item, dict) and item.get("dropdown") == label), None)
        if dropdown is None:
            raise ValueError(f"Dropdown not found in docs.json: {label}")
        pages = dropdown.get("pages")
        if not isinstance(pages, list):
            raise ValueError(f"Dropdown does not expose a pages list: {label}")
        return pages

    products = navigation.get("products")
    if isinstance(products, list):
        product = next((item for item in products if isinstance(item, dict) and item.get("product") == label), None)
        if product is None:
            raise ValueError(f"Product not found in docs.json: {label}")
        pages = product.get("pages")
        if not isinstance(pages, list):
            raise ValueError(f"Product does not expose a pages list: {label}")
        return pages

    raise ValueError(f"docs.json navigation must define dropdowns or products: {docs_json_path}")


def update_docs_navigation(
    *,
    docs_json_path: Path,
    dropdown_label: str,
    output_files: list[Path],
    nav_group: str,
) -> Path:
    docs = load_json(docs_json_path)
    pages = api_reference_pages(docs, label=dropdown_label, docs_json_path=docs_json_path)

    page_refs = [docs_json_page_ref(output_file, docs_json_path) for output_file in output_files]
    existing_index = nav_group_index(pages, group_label=nav_group)
    updated_pages = prune_nav_items(pages, page_refs=set(page_refs), group_label=nav_group)
    nav_item = {"group": nav_group, "pages": page_refs}
    if existing_index is None:
        updated_pages.append(nav_item)
    else:
        updated_pages.insert(min(existing_index, len(updated_pages)), nav_item)
    pages[:] = updated_pages

    docs_json_path.write_text(json.dumps(docs, indent=2) + "\n", encoding="utf-8")
    print(f"Updated docs navigation: {docs_json_path}")
    return docs_json_path


def remove_legacy_output(*, output_file: Path) -> None:
    legacy_output = LEGACY_OUTPUT_FILE.resolve()
    if output_file == legacy_output:
        return
    if legacy_output.exists():
        legacy_output.unlink()
        print(f"Removed legacy output: {legacy_output}")


def run(command: list[str], *, cwd: Path, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    print("Running:", " ".join(command))
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def package_cache_key(package_name: str) -> str:
    return package_name.replace("@", "").replace("/", "-")


def path_from_config(value: str, *, base: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base / path


def string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"Source config must define a non-empty string list under `{field}`")
    return value


def configured_packages(source_config: dict[str, Any], args: argparse.Namespace) -> list[TypeScriptPackageConfig]:
    packages = source_config.get("packages")
    if packages is None:
        packages = [
            {
                "package_name": source_config.get("package_name") or "@daml/types",
                "min_version": source_config.get("min_version"),
                "versions": source_config.get("versions"),
                "publish_version": source_config.get("publish_version"),
                "typedoc_version": source_config.get("typedoc_version"),
                "entry_point": source_config.get("entry_point"),
                "typedoc_args": source_config.get("typedoc_args"),
                "source": source_config.get("source") or args.source_name,
                "version_filter": args.version_filter,
                "page_title": args.page_title,
                "page_description": args.page_description,
                "output_file": args.output_file,
            }
        ]
    if not isinstance(packages, list) or not all(isinstance(item, dict) for item in packages):
        raise ValueError("Source config `packages` must be a list of package objects")

    configured: list[TypeScriptPackageConfig] = []
    seen_output_files: set[Path] = set()
    for package in packages:
        package_name = str(package.get("package_name") or "")
        if not package_name:
            raise ValueError("Each TypeScript package config must define `package_name`")
        configured_versions = package.get("versions")
        if configured_versions is not None:
            versions = string_list(
                configured_versions,
                field=f"packages[{package_name}].versions",
            )
        else:
            min_version = str(package.get("min_version") or "")
            if not min_version:
                raise ValueError(
                    f"Each TypeScript package config must define `min_version`: {package_name}"
                )
            minimum_key = version_key(min_version)
            versions = [
                version
                for version in stable_npm_versions(package_name)
                if version_key(version) >= minimum_key
            ]
            if not versions:
                raise ValueError(
                    f"No stable npm versions found for {package_name} at or above {min_version}"
                )
        selected_versions = [version for version in versions if not args.version or version in set(args.version)]
        if not selected_versions:
            raise ValueError(f"No versions selected for {package_name}")
        publish_version = args.publish_version or selected_versions[-1]
        if publish_version not in selected_versions:
            raise ValueError(
                f"Publish version '{publish_version}' for {package_name} is not present in selected versions: {selected_versions}"
            )

        output_file = path_from_config(str(package.get("output_file") or args.output_file), base=REPO_ROOT).resolve()
        if output_file in seen_output_files:
            raise ValueError(f"Duplicate TypeScript output file configured: {output_file}")
        seen_output_files.add(output_file)
        history_report = path_from_config(
            str(package.get("history_report") or ""),
            base=REPO_ROOT,
        ).resolve()
        if not str(package.get("history_report") or ""):
            raise ValueError(
                f"Each TypeScript package config must define `history_report`: {package_name}"
            )
        reader_route = "/" + docs_json_page_ref(
            output_file,
            Path(args.docs_json).resolve(),
        )

        typedoc_args = package.get("typedoc_args") or []
        if not isinstance(typedoc_args, list) or not all(isinstance(item, str) and item for item in typedoc_args):
            raise ValueError(f"`typedoc_args` for {package_name} must be a string list when present")

        configured.append(
            TypeScriptPackageConfig(
                package_name=package_name,
                versions=selected_versions,
                publish_version=publish_version,
                typedoc_version=str(package.get("typedoc_version") or source_config.get("typedoc_version") or "0.27.9"),
                entry_point=str(package.get("entry_point") or source_config.get("entry_point") or "index.d.ts"),
                typedoc_args=list(typedoc_args),
                source_name=str(package.get("source") or source_config.get("source") or f"Published {package_name} npm tarballs rendered to local TypeDoc JSON"),
                version_filter=str(
                    package.get("version_filter")
                    or (
                        f"stable {package_name} npm releases at or above "
                        f"{package.get('min_version')}"
                    )
                ),
                page_title=str(package.get("page_title") or package_name),
                page_description=str(package.get("page_description") or args.page_description),
                output_file=output_file,
                history_report=history_report,
                reader_route=reader_route,
                surface_id=str(
                    package.get("surface_id")
                    or f"typescript-{package_cache_key(package_name)}"
                ),
                cache_key=str(package.get("cache_key") or package_cache_key(package_name)),
            )
        )
    return configured


def patch_tsconfig(package_dir: Path) -> None:
    tsconfig_path = package_dir / "tsconfig.json"
    if not tsconfig_path.exists():
        return
    payload = json.loads(tsconfig_path.read_text(encoding="utf-8"))
    compiler_options = payload.setdefault("compilerOptions", {})
    if not isinstance(compiler_options, dict):
        raise ValueError(f"Expected compilerOptions object in {tsconfig_path}")
    compiler_options["ignoreDeprecations"] = "5.0"
    compiler_options["typeRoots"] = ["./node_modules/@types"]
    tsconfig_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def prepare_package(
    *,
    cache_dir: Path,
    package_name: str,
    version: str,
    force_regenerate: bool,
) -> Path:
    version_dir = cache_dir / version
    package_dir = version_dir / "package"
    tarball_path = version_dir / "package.tgz"

    if force_regenerate and version_dir.exists():
        shutil.rmtree(version_dir)

    if package_dir.exists():
        patch_tsconfig(package_dir)
        return package_dir

    version_dir.mkdir(parents=True, exist_ok=True)
    completed = run(
        ["npm", "pack", "--silent", f"{package_name}@{version}"],
        cwd=version_dir,
        capture_output=True,
    )
    tarball_name = completed.stdout.strip().splitlines()[-1].strip()
    packed_tarball = version_dir / tarball_name
    packed_tarball.rename(tarball_path)
    with tarfile.open(tarball_path, "r:gz") as archive:
        archive.extractall(version_dir)
    if not package_dir.exists():
        raise ValueError(f"Expected npm tarball to extract a package directory at {package_dir}")
    patch_tsconfig(package_dir)
    return package_dir


def ensure_package_dependencies(package_dir: Path, *, force_regenerate: bool) -> None:
    node_modules_dir = package_dir / "node_modules"
    if force_regenerate and node_modules_dir.exists():
        shutil.rmtree(node_modules_dir)
    if node_modules_dir.exists():
        print(f"Using cached npm install: {package_dir}")
        return
    run(
        ["npm", "install", "--ignore-scripts", "--no-package-lock", "--silent"],
        cwd=package_dir,
    )


def ensure_typedoc_json(
    *,
    cache_dir: Path,
    typedoc_dir: Path,
    package_name: str,
    typedoc_version: str,
    entry_point: str,
    typedoc_args: list[str],
    version: str,
    force_regenerate: bool,
) -> Path:
    output_json = typedoc_dir / version / "typedoc.json"
    if output_json.exists() and not force_regenerate:
        print(f"Using cached TypeDoc JSON: {output_json}")
        return output_json

    package_dir = prepare_package(
        cache_dir=cache_dir,
        package_name=package_name,
        version=version,
        force_regenerate=force_regenerate,
    )
    ensure_package_dependencies(package_dir, force_regenerate=force_regenerate)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "npx",
            "--yes",
            f"typedoc@{typedoc_version}",
            *typedoc_args,
            "--json",
            str(output_json),
            entry_point,
        ],
        cwd=package_dir,
    )
    return output_json


def write_manifest(
    *,
    package_config: TypeScriptPackageConfig,
    manifest_path: Path,
    typedoc_dir: Path,
) -> Path:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": package_config.source_name,
        "package_name": package_config.package_name,
        "publish_version": package_config.publish_version,
        "versions": [
            {
                "version": version,
                "json_path": str((typedoc_dir / version / "typedoc.json").resolve()),
            }
            for version in package_config.versions
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote manifest: {manifest_path}")
    return manifest_path


def main() -> int:
    ensure_repo_direnv(repo_root=REPO_ROOT, script_path=Path(__file__).resolve(), argv=sys.argv[1:])
    args = parse_args()
    source_config = load_json(Path(args.source_config).resolve())
    packages = configured_packages(source_config, args)

    cache_root = Path(args.cache_dir).resolve()
    typedoc_root = Path(args.typedoc_dir).resolve()
    manifest_out = Path(args.manifest_out).resolve()
    for index, package in enumerate(packages):
        package_cache_dir = cache_root / package.cache_key
        package_typedoc_dir = typedoc_root / package.cache_key
        for version in package.versions:
            ensure_typedoc_json(
                cache_dir=package_cache_dir,
                typedoc_dir=package_typedoc_dir,
                package_name=package.package_name,
                typedoc_version=package.typedoc_version,
                entry_point=package.entry_point,
                typedoc_args=package.typedoc_args,
                version=version,
                force_regenerate=args.force_regenerate,
            )

        if len(packages) == 1:
            manifest_path = manifest_out
        else:
            manifest_path = manifest_out.with_name(f"{manifest_out.stem}-{package.cache_key}{manifest_out.suffix}")
        manifest_path = write_manifest(
            package_config=package,
            manifest_path=manifest_path,
            typedoc_dir=package_typedoc_dir,
        )

        command = repo_direnv_command(
            REPO_ROOT,
            "x2mdx",
            "typedoc",
            "build-api-pages-from-manifest",
            "--manifest",
            str(manifest_path),
            "--output-file",
            str(package.output_file),
            "--publish-version",
            package.publish_version,
            "--source-name",
            package.source_name,
            "--version-filter",
            package.version_filter,
            "--page-title",
            package.page_title,
            "--page-description",
            package.page_description,
            "--history-report",
            str(package.history_report),
            "--reader-route",
            package.reader_route,
            "--surface-id",
            package.surface_id,
            "--surface-title",
            package.page_title,
            "--configured-scope",
            f"Published exports from {package.package_name}",
            "--lifecycle-metadata",
            str(Path(args.lifecycle_metadata).resolve()),
        )
        for version in args.version or []:
            command.extend(["--version", version])
        print(f"[{index + 1}/{len(packages)}] Running:", " ".join(command))
        completed = subprocess.run(command, cwd=REPO_ROOT)
        if completed.returncode != 0:
            return completed.returncode

    remove_legacy_output(output_file=packages[0].output_file)
    update_docs_navigation(
        docs_json_path=Path(args.docs_json).resolve(),
        dropdown_label=args.nav_dropdown,
        output_files=[package.output_file for package in packages],
        nav_group=args.nav_group,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
