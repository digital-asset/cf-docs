#!/usr/bin/env python3
"""Add and edit external snippet manifest entries from a local source checkout."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.generate_external_snippets import (
    REPOS,
    SnippetRepo,
    find_source_dir,
)


CF_DOCS_ROOT = Path(__file__).resolve().parents[1]
MAIN_VERSION = "main"

LANGUAGES = {
    ".daml": "haskell",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "jsx",
    ".json": "json",
    ".md": "markdown",
    ".mdx": "mdx",
    ".proto": "protobuf",
    ".py": "python",
    ".scala": "scala",
    ".sh": "bash",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
}


class SnippetAuthoringError(Exception):
    """A contributor-facing snippet authoring error."""


@dataclass(frozen=True)
class SourceRevision:
    commit: str
    remote: str
    ref: str


@dataclass(frozen=True)
class FileChange:
    heading: str
    path: Path
    content: bytes | None


def manifest_path(repo: SnippetRepo) -> Path:
    return CF_DOCS_ROOT / "config" / "snippet-config" / repo.config_name


def helper_path() -> Path:
    return CF_DOCS_ROOT / "scripts" / "helpers" / "generateOutputDocs.js"


def source_lock_path() -> Path:
    return CF_DOCS_ROOT / "config" / "snippet-config" / "snippet-source-lock.json"


def output_path(repo: SnippetRepo, snippet_name: str) -> Path:
    return (
        CF_DOCS_ROOT
        / "docs-main"
        / "snippets"
        / "external"
        / (repo.output_repo_name or repo.name)
        / MAIN_VERSION
        / f"{snippet_name}.mdx"
    )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SnippetAuthoringError(
            f"Snippet manifest does not exist: {path}"
        ) from error
    except json.JSONDecodeError as error:
        raise SnippetAuthoringError(
            f"Snippet manifest is not valid JSON: {path}: {error}"
        ) from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("snippets"), list):
        raise SnippetAuthoringError(
            f'Snippet manifest must contain a top-level "snippets" array: {path}'
        )
    if not all(isinstance(item, dict) for item in manifest["snippets"]):
        raise SnippetAuthoringError(f"Every snippet entry must be an object: {path}")
    return manifest


def load_source_lock() -> dict[str, Any]:
    path = source_lock_path()
    if not path.exists():
        return {"schemaVersion": 1, "snippets": {}}
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SnippetAuthoringError(
            f"Snippet source lock is not valid JSON: {path}: {error}"
        ) from error
    if (
        not isinstance(lock, dict)
        or lock.get("schemaVersion") != 1
        or not isinstance(lock.get("snippets"), dict)
    ):
        raise SnippetAuthoringError(
            f'Snippet source lock must use schemaVersion 1 and a "snippets" object: {path}'
        )
    return lock


def run_git(source_dir: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_dir), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise SnippetAuthoringError(
            f"Git command failed in {source_dir}: git {' '.join(arguments)}: {details}"
        )
    return result.stdout.strip()


def normalized_remote_url(remote: str) -> str:
    patterns = (
        r"^(?:git@github\.com:|ssh://git@github\.com/)(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
        r"^https?://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, remote)
        if match:
            return f"https://github.com/{match.group('repo')}"
    return remote


def source_revision(source_dir: Path, source: str) -> SourceRevision:
    tracked = subprocess.run(
        ["git", "-C", str(source_dir), "ls-files", "--error-unmatch", "--", source],
        text=True,
        capture_output=True,
        check=False,
    )
    if tracked.returncode != 0:
        raise SnippetAuthoringError(
            f"Snippet source must be tracked before authoring: {source}"
        )
    dirty = run_git(
        source_dir,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        source,
    )
    if dirty:
        raise SnippetAuthoringError(
            f"Snippet source must match HEAD before its commit can be recorded: {source}"
        )

    commit = run_git(source_dir, "rev-parse", "--verify", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SnippetAuthoringError(
            f"Could not resolve a 40-character source commit: {commit}"
        )

    refs = {
        ref
        for ref in run_git(
            source_dir,
            "for-each-ref",
            "--format=%(refname:short)",
            f"--points-at={commit}",
            "refs/remotes",
        ).splitlines()
        if "/" in ref and not ref.endswith("/HEAD")
    }
    upstream = run_git(
        source_dir,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    if upstream in refs:
        remote_ref = upstream
    elif refs:
        remote_ref = sorted(refs, key=lambda ref: (not ref.startswith("origin/"), ref))[
            0
        ]
    else:
        raise SnippetAuthoringError(
            "Source HEAD must be available at an exact remote-tracking ref; fetch or push it first"
        )

    remote_name = remote_ref.split("/", 1)[0]
    remote = normalized_remote_url(
        run_git(source_dir, "remote", "get-url", remote_name)
    )
    return SourceRevision(commit=commit, remote=remote, ref=remote_ref)


def revision_record(repo: SnippetRepo, revision: SourceRevision) -> dict[str, str]:
    return {
        "repository": repo.name,
        "commit": revision.commit,
        "remote": revision.remote,
        "ref": revision.ref,
    }


def normalized_source_path(source: str) -> str:
    if not source or source.startswith("/") or "\\" in source:
        raise SnippetAuthoringError(
            "--source must be a non-empty repository-relative POSIX path"
        )
    parts = source.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SnippetAuthoringError(
            "--source must not contain empty, '.' or '..' path components"
        )
    return PurePosixPath(*parts).as_posix()


def validate_source_file(source_dir: Path, source: str) -> Path:
    root = source_dir.resolve()
    candidate = (root / source).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise SnippetAuthoringError(
            f"Snippet source escapes its checkout: {source}"
        ) from error
    if not candidate.is_file():
        raise SnippetAuthoringError(f"Snippet source file does not exist: {candidate}")
    return candidate


def infer_language(source: str) -> str:
    suffix = PurePosixPath(source).suffix.lower()
    language = LANGUAGES.get(suffix)
    if not language:
        raise SnippetAuthoringError(
            f"Cannot infer a language from {source!r}; pass --language explicitly"
        )
    return language


def slug(value: str) -> str:
    rendered = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not rendered:
        raise SnippetAuthoringError(f"Cannot derive a name from {value!r}")
    return rendered


def path_slug(source: str) -> str:
    path = PurePosixPath(source)
    without_suffix = path.with_suffix("") if path.suffix else path
    return slug(without_suffix.as_posix())


def marker_pair(args: argparse.Namespace, *, editing: bool) -> dict[str, Any] | None:
    supplied_exact = bool(args.start_marker or args.end_marker)
    choices = int(args.full_file) + int(bool(args.marker)) + int(supplied_exact)
    if choices > 1:
        raise SnippetAuthoringError(
            "Choose only one selector: --full-file, --marker, or --start-marker/--end-marker"
        )
    if supplied_exact and not (args.start_marker and args.end_marker):
        raise SnippetAuthoringError(
            "Pass both --start-marker and --end-marker when using exact markers"
        )
    if args.full_file:
        return {"type": "fullFile"}
    if args.marker:
        return {
            "type": "stringMarker",
            "start": f"{args.marker}_START",
            "end": f"{args.marker}_END",
        }
    if supplied_exact:
        return {
            "type": "stringMarker",
            "start": args.start_marker,
            "end": args.end_marker,
        }
    if editing:
        return None
    return {"type": "fullFile"}


def validate_snippet_name(name: str) -> str:
    if not name or name.startswith("/") or "\\" in name:
        raise SnippetAuthoringError(
            "Snippet names must be non-empty relative POSIX paths"
        )
    if any(part in {"", ".", ".."} for part in name.split("/")):
        raise SnippetAuthoringError(
            "Snippet names must not contain empty, '.' or '..' path components"
        )
    return name


def derive_snippet_name(
    repo: SnippetRepo, source: str, location: dict[str, Any]
) -> str:
    name = f"{repo.name}-literal-"
    if location["type"] == "fullFile":
        return f"{name}full-{path_slug(source)}"
    return f"{name}marker-{path_slug(source)}-{slug(str(location['start']))}"


def duplicate_name_locations(name: str) -> list[Path]:
    matches: list[Path] = []
    config_dir = CF_DOCS_ROOT / "config" / "snippet-config"
    for path in sorted(config_dir.glob("*-snippet-list-remote.json")):
        manifest = load_manifest(path)
        if any(entry.get("snippetName") == name for entry in manifest["snippets"]):
            matches.append(path)
    return matches


def same_source(entry: dict[str, Any], source: str, location: dict[str, Any]) -> bool:
    return entry.get("sourceFilepath") == source and entry.get("location") == location


def render_one_snippet(
    *,
    source_dir: Path,
    manifest: dict[str, Any],
    entry: dict[str, Any],
) -> bytes:
    helper = helper_path()
    if not helper.is_file():
        raise SnippetAuthoringError(
            f"Snippet extraction helper does not exist: {helper}"
        )

    single_manifest = {
        key: value for key, value in manifest.items() if key != "snippets"
    }
    single_manifest["snippets"] = [entry]
    with tempfile.TemporaryDirectory(prefix="cf-docs-snippet-") as temp_name:
        temp = Path(temp_name)
        config = temp / "exportConfig.json"
        output = temp / "output"
        config.write_text(
            json.dumps(single_manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "node",
                str(helper),
                "--repo-root",
                str(source_dir),
                "--export-config",
                str(config),
                "--output",
                str(output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            raise SnippetAuthoringError(f"Snippet extraction failed:\n{details}")
        generated = output / f"{entry['snippetName']}.mdx"
        if not generated.is_file():
            raise SnippetAuthoringError(
                f"Snippet extraction did not create expected output: {generated}"
            )
        return generated.read_bytes()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def serialized_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def commit_changes(changes: list[FileChange]) -> None:
    originals = {
        change.path: change.path.read_bytes() if change.path.exists() else None
        for change in changes
    }
    try:
        for change in changes:
            if change.content is None:
                change.path.unlink(missing_ok=True)
            else:
                atomic_write(change.path, change.content)
    except BaseException:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, original)
        raise


def print_file_diff(path: Path, proposed: bytes | None) -> None:
    original = path.read_bytes() if path.exists() else b""
    proposed_content = proposed or b""
    try:
        label = path.relative_to(CF_DOCS_ROOT).as_posix()
    except ValueError:
        label = str(path)
    from_label = f"a/{label}" if path.exists() else "/dev/null"
    to_label = f"b/{label}" if proposed is not None else "/dev/null"
    diff = difflib.unified_diff(
        original.decode("utf-8").splitlines(keepends=True),
        proposed_content.decode("utf-8").splitlines(keepends=True),
        fromfile=from_label,
        tofile=to_label,
    )
    rendered = "".join(diff)
    print(rendered, end="" if rendered.endswith("\n") else "\n")
    if not rendered:
        print("(no changes)")


def print_change_preview(
    *,
    action: str,
    snippet_name: str,
    changes: list[FileChange],
) -> None:
    print(f"Dry run: would {action} {snippet_name}; no files written")
    for change in changes:
        print(f"\n{change.heading}:")
        print_file_diff(change.path, change.content)


def component_name(repo: SnippetRepo, snippet_name: str) -> str:
    words = re.findall(
        r"[A-Za-z0-9]+",
        f"external-{repo.output_repo_name or repo.name}-{MAIN_VERSION}-{snippet_name}",
    )
    return "".join(word[:1].upper() + word[1:] for word in words)


def print_usage(repo: SnippetRepo, snippet_name: str) -> None:
    name = component_name(repo, snippet_name)
    path = (
        f"/snippets/external/{repo.output_repo_name or repo.name}/"
        f"{MAIN_VERSION}/{snippet_name}.mdx"
    )
    print("\nAdd this to the page:")
    print(f"import {name} from '{path}';")
    print(f"\n<{name} />")


def source_dir_for(args: argparse.Namespace, repo: SnippetRepo) -> Path:
    try:
        return find_source_dir(repo, args.source_dir)
    except SystemExit as error:
        raise SnippetAuthoringError(str(error)) from error


def find_manifest_entry(
    manifest: dict[str, Any], manifest_file: Path, snippet_name: str
) -> dict[str, Any]:
    matches = [
        entry
        for entry in manifest["snippets"]
        if entry.get("snippetName") == snippet_name
    ]
    if len(matches) != 1:
        raise SnippetAuthoringError(
            f"Expected exactly one snippet named {snippet_name!r} in {manifest_file}; "
            f"found {len(matches)}"
        )
    return matches[0]


def authoring_changes(
    *,
    manifest_file: Path,
    manifest: dict[str, Any],
    lock: dict[str, Any],
    generated_file: Path,
    generated_content: bytes | None,
) -> list[FileChange]:
    return [
        FileChange("Manifest diff", manifest_file, serialized_json(manifest)),
        FileChange("Source lock diff", source_lock_path(), serialized_json(lock)),
        FileChange("Generated MDX diff", generated_file, generated_content),
    ]


def add(args: argparse.Namespace, repo: SnippetRepo) -> int:
    source_dir = source_dir_for(args, repo)
    source = normalized_source_path(args.source)
    validate_source_file(source_dir, source)
    revision = source_revision(source_dir, source)
    location = marker_pair(args, editing=False)
    assert location is not None
    language = args.language or infer_language(source)
    name = validate_snippet_name(
        args.name or derive_snippet_name(repo, source, location)
    )

    manifest_file = manifest_path(repo)
    manifest = load_manifest(manifest_file)
    lock = load_source_lock()
    duplicates = duplicate_name_locations(name)
    if duplicates:
        locations = ", ".join(str(path) for path in duplicates)
        raise SnippetAuthoringError(
            f"Snippet name already exists: {name} ({locations})"
        )
    for entry in manifest["snippets"]:
        if same_source(entry, source, location):
            raise SnippetAuthoringError(
                f"A snippet already uses this source and selector: {entry.get('snippetName')}"
            )
    if name in lock["snippets"]:
        raise SnippetAuthoringError(
            f"Snippet source lock already contains an orphaned entry: {name}"
        )

    entry = {
        "snippetName": name,
        "sourceRepo": repo.name,
        "sourceFilepath": source,
        "location": location,
        "description": "",
        "options": {"language": language},
    }
    generated = render_one_snippet(
        source_dir=source_dir,
        manifest=manifest,
        entry=entry,
    )
    manifest["snippets"].append(entry)
    lock["snippets"][name] = revision_record(repo, revision)
    generated_file = output_path(repo, name)
    if generated_file.exists():
        raise SnippetAuthoringError(
            f"Refusing to overwrite an existing output not owned by the manifest: "
            f"{generated_file}"
        )
    changes = authoring_changes(
        manifest_file=manifest_file,
        manifest=manifest,
        lock=lock,
        generated_file=generated_file,
        generated_content=generated,
    )
    if args.dry_run:
        print_change_preview(
            action="add",
            snippet_name=name,
            changes=changes,
        )
        print_usage(repo, name)
        return 0
    commit_changes(changes)

    print(f"Added {name}")
    print(f"Manifest: {manifest_file.relative_to(CF_DOCS_ROOT)}")
    print(f"Source:   {revision.commit} at {revision.remote} ({revision.ref})")
    print(f"Output:   {generated_file.relative_to(CF_DOCS_ROOT)}")
    print_usage(repo, name)
    return 0


def update(args: argparse.Namespace, repo: SnippetRepo) -> int:
    source_dir = source_dir_for(args, repo)
    manifest_file = manifest_path(repo)
    manifest = load_manifest(manifest_file)
    lock = load_source_lock()
    entry = find_manifest_entry(manifest, manifest_file, args.snippet_name)
    requested_location = marker_pair(args, editing=True)
    has_change = requested_location is not None or args.language is not None
    if args.command == "move":
        has_change = True
    if not has_change:
        raise SnippetAuthoringError("Edit requires a selector option or --language")

    source = normalized_source_path(
        args.source if args.command == "move" else str(entry.get("sourceFilepath", ""))
    )
    validate_source_file(source_dir, source)
    revision = source_revision(source_dir, source)
    location = requested_location or entry.get("location")
    if not isinstance(location, dict) or location.get("type") not in {
        "fullFile",
        "stringMarker",
        "lines",
        "jsonIndex",
        "regexWrap",
    }:
        raise SnippetAuthoringError(
            f"Snippet has an unsupported existing selector: {location!r}"
        )
    options = entry.get("options")
    if not isinstance(options, dict):
        options = {}
        entry["options"] = options
    language = args.language or options.get("language") or infer_language(source)

    for other in manifest["snippets"]:
        if other is not entry and same_source(other, source, location):
            raise SnippetAuthoringError(
                f"Another snippet already uses this source and selector: "
                f"{other.get('snippetName')}"
            )

    entry["sourceRepo"] = repo.name
    entry["sourceFilepath"] = source
    entry["location"] = location
    options["language"] = language
    lock["snippets"][args.snippet_name] = revision_record(repo, revision)
    generated = render_one_snippet(
        source_dir=source_dir,
        manifest=manifest,
        entry=entry,
    )
    generated_file = output_path(repo, args.snippet_name)
    changes = authoring_changes(
        manifest_file=manifest_file,
        manifest=manifest,
        lock=lock,
        generated_file=generated_file,
        generated_content=generated,
    )
    if args.dry_run:
        print_change_preview(
            action=args.command,
            snippet_name=args.snippet_name,
            changes=changes,
        )
        return 0
    commit_changes(changes)

    verb = "Moved" if args.command == "move" else "Edited"
    print(f"{verb} {args.snippet_name}; its import path is unchanged")
    print(f"Manifest: {manifest_file.relative_to(CF_DOCS_ROOT)}")
    print(f"Source:   {revision.commit} at {revision.remote} ({revision.ref})")
    print(f"Output:   {generated_file.relative_to(CF_DOCS_ROOT)}")
    return 0


def find_references(generated_file: Path) -> list[str]:
    docs_root = CF_DOCS_ROOT / "docs-main"
    target = generated_file.relative_to(docs_root).as_posix().removesuffix(".mdx")
    target_pattern = re.compile(rf"{re.escape(target)}(?:\.mdx)?(?=$|['\"\s;)\]}}>])")
    references: list[str] = []
    searchable_suffixes = {".js", ".jsx", ".json", ".md", ".mdx", ".ts", ".tsx"}
    for path in docs_root.rglob("*"):
        if (
            not path.is_file()
            or path == generated_file
            or path.suffix.lower() not in searchable_suffixes
        ):
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if target_pattern.search(line):
                references.append(
                    f"{path.relative_to(CF_DOCS_ROOT).as_posix()}:{line_number}"
                )
    return references


def delete(args: argparse.Namespace, repo: SnippetRepo) -> int:
    manifest_file = manifest_path(repo)
    manifest = load_manifest(manifest_file)
    lock = load_source_lock()
    entry = find_manifest_entry(manifest, manifest_file, args.snippet_name)
    generated_file = output_path(repo, args.snippet_name)
    references = find_references(generated_file)
    if references:
        rendered = "\n  ".join(references)
        raise SnippetAuthoringError(
            f"Cannot delete {args.snippet_name}; page references remain:\n  {rendered}"
        )

    manifest["snippets"].remove(entry)
    lock["snippets"].pop(args.snippet_name, None)
    changes = authoring_changes(
        manifest_file=manifest_file,
        manifest=manifest,
        lock=lock,
        generated_file=generated_file,
        generated_content=None,
    )
    if args.dry_run:
        print_change_preview(
            action="delete",
            snippet_name=args.snippet_name,
            changes=changes,
        )
        return 0
    commit_changes(changes)
    print(f"Deleted {args.snippet_name}")
    print(f"Manifest: {manifest_file.relative_to(CF_DOCS_ROOT)}")
    print(f"Output:   {generated_file.relative_to(CF_DOCS_ROOT)}")
    return 0


def add_authoring_arguments(
    parser: argparse.ArgumentParser,
    *,
    command: str,
) -> None:
    parser.add_argument("repo", choices=sorted(REPOS), help="Source repository key")
    if command in {"edit", "move"}:
        parser.add_argument("snippet_name", help="Existing stable snippetName")
    if command in {"add", "move"}:
        parser.add_argument("--source", required=True)
    if command == "add":
        parser.add_argument("--name", help="Override the derived snippetName")
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Local source checkout; common sibling locations are searched when omitted",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print manifest/MDX diffs without writing files",
    )
    parser.add_argument("--language")
    parser.add_argument("--full-file", action="store_true")
    parser.add_argument(
        "--marker",
        help="Marker base; expands to <value>_START and <value>_END",
    )
    parser.add_argument("--start-marker", help="Exact start marker")
    parser.add_argument("--end-marker", help="Exact end marker")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add, edit, move, or delete cf-docs external snippets"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_parser = subparsers.add_parser("add", help="Add and render a snippet")
    add_authoring_arguments(add_parser, command="add")
    edit_parser = subparsers.add_parser(
        "edit", help="Edit and rerender a snippet without changing its name"
    )
    add_authoring_arguments(edit_parser, command="edit")
    move_parser = subparsers.add_parser(
        "move", help="Move and rerender a snippet without changing its name"
    )
    add_authoring_arguments(move_parser, command="move")
    delete_parser = subparsers.add_parser(
        "delete", help="Delete an unreferenced snippet and its generated output"
    )
    delete_parser.add_argument("repo", choices=sorted(REPOS))
    delete_parser.add_argument("snippet_name", help="Existing stable snippetName")
    delete_parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = REPOS[args.repo]
    try:
        if args.command == "add":
            return add(args, repo)
        if args.command in {"edit", "move"}:
            return update(args, repo)
        return delete(args, repo)
    except (OSError, SnippetAuthoringError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
