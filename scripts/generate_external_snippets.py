#!/usr/bin/env python3
"""Run external snippet extraction from this cf-docs checkout.

Examples:
  python3 scripts/generate_external_snippets.py canton --source-dir ../canton
  python3 scripts/generate_external_snippets.py daml-shell --source-dir ../daml-shell
  python3 scripts/generate_external_snippets.py canton --copy-output --version main
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CF_DOCS_ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3


@dataclass(frozen=True)
class SnippetRepo:
    name: str
    config_name: str
    aliases: tuple[str, ...]
    output_repo_name: str | None = None
    helper_name: str = "generateOutputDocs.js"
    scripts_subdir: str = "scripts/docs"
    prepare: tuple[str, ...] = ()
    needs_docker: bool = False


REPOS: dict[str, SnippetRepo] = {
    "cn-quickstart": SnippetRepo(
        name="cn-quickstart",
        config_name="cn-quickstart-snippet-list-remote.json",
        aliases=("cn-quickstart",),
    ),
    "canton": SnippetRepo(
        name="canton",
        config_name="canton-snippet-list-remote.json",
        aliases=("canton",),
        prepare=(
            'sbt "docs-open / reset" "docs-open / generateSphinxSnippets"',
        ),
        needs_docker=True,
    ),
    "daml": SnippetRepo(
        name="daml",
        config_name="daml-snippet-list-remote.json",
        aliases=("daml",),
    ),
    "daml-shell": SnippetRepo(
        name="daml-shell",
        config_name="daml-shell-snippet-list-remote.json",
        aliases=("daml-shell",),
    ),
    "splice": SnippetRepo(
        name="splice",
        config_name="splice-snippet-list-remote.json",
        aliases=("splice",),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate external snippets from a local source repository."
    )
    parser.add_argument(
        "repo",
        nargs="?",
        help="Snippet repo to generate. Use --list to show supported names.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List supported snippet repos and whether their config exists.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Path to the local source repository checkout. If omitted, common sibling locations are searched.",
    )
    parser.add_argument(
        "--ref",
        help="Optional git ref to check out in the source repo before generation.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch the source repo's origin before checking out --ref.",
    )
    parser.add_argument(
        "--skip-prepare",
        action="store_true",
        help="Skip repo-specific preparation, such as Canton docs-open snippet JSON generation.",
    )
    parser.add_argument(
        "--copy-output",
        action="store_true",
        help="Copy docs-output into docs-main/snippets/external/<repo>/<version> in this cf-docs checkout.",
    )
    parser.add_argument(
        "--version",
        default="main",
        help="Version folder used with --copy-output. Default: main.",
    )
    parser.add_argument(
        "--replace-output",
        action="store_true",
        help="With --copy-output, remove the target docs-main/snippets/external folder before copying.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=20.0,
        help="Refuse to start if the source filesystem has less free space. Default: 20.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Pass a quiet run to the helper. The current helper still prints per-snippet progress.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without changing files or running generation.",
    )
    return parser.parse_args()


def config_path(repo: SnippetRepo) -> Path:
    return CF_DOCS_ROOT / "config" / "snippet-config" / repo.config_name


def helper_path() -> Path:
    return CF_DOCS_ROOT / "scripts" / "helpers" / "generateOutputDocs.js"


def repo_label(repo: SnippetRepo) -> str:
    return repo.output_repo_name or repo.name


def list_repos() -> None:
    for name in sorted(REPOS):
        repo = REPOS[name]
        status = "ok" if config_path(repo).is_file() else "missing config"
        aliases = ", ".join(repo.aliases)
        print(f"{name:22} {status:15} {repo.config_name} aliases=[{aliases}]")


def source_env_names(repo: SnippetRepo) -> list[str]:
    names = {repo.name, *repo.aliases}
    env_names: list[str] = []
    for name in sorted(names):
        token = "".join(ch if ch.isalnum() else "_" for ch in name).upper()
        env_names.extend((f"SNIPPET_REPO_DIR_{token}", f"{token}_REPO_DIR"))
    return env_names


def candidate_roots() -> list[Path]:
    roots: list[Path] = []
    current = CF_DOCS_ROOT
    for parent in (current.parent, current.parent.parent, current.parent.parent.parent):
        if parent and parent not in roots:
            roots.append(parent)
        worktrees = parent / ".worktrees"
        if worktrees.is_dir() and worktrees not in roots:
            roots.append(worktrees)
    extra = os.environ.get("SNIPPET_REPOS_ROOT")
    if extra:
        roots.insert(0, Path(extra).expanduser())
    return roots


def is_repo_dir(path: Path) -> bool:
    return (path / ".git").exists()


def find_source_dir(repo: SnippetRepo, explicit: Path | None) -> Path:
    if explicit:
        path = explicit.expanduser().resolve()
        if not is_repo_dir(path):
            raise SystemExit(f"Source directory is not a git checkout: {path}")
        return path

    for env_name in source_env_names(repo):
        value = os.environ.get(env_name)
        if value:
            path = Path(value).expanduser().resolve()
            if not is_repo_dir(path):
                raise SystemExit(f"{env_name} is not a git checkout: {path}")
            return path

    exact: list[Path] = []
    prefix: list[Path] = []
    for root in candidate_roots():
        for alias in repo.aliases:
            candidate = root / alias
            if is_repo_dir(candidate):
                exact.append(candidate.resolve())
        if root.is_dir():
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                if any(child.name.startswith(f"{alias}-") for alias in repo.aliases):
                    if is_repo_dir(child):
                        prefix.append(child.resolve())

    matches = sorted(dict.fromkeys(exact + prefix))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        env_hint = source_env_names(repo)[0]
        raise SystemExit(
            f"Could not find a local checkout for {repo.name}. "
            f"Pass --source-dir or set {env_hint}."
        )
    formatted = "\n  ".join(str(path) for path in matches)
    raise SystemExit(
        f"Found multiple possible checkouts for {repo.name}; pass --source-dir:\n  {formatted}"
    )


def command_for_repo(source_dir: Path, command: str) -> list[str]:
    if (source_dir / ".envrc").is_file() and shutil.which("direnv"):
        return ["direnv", "exec", str(source_dir), "bash", "-lc", command]
    return ["bash", "-lc", command]


def run(
    argv: list[str],
    *,
    cwd: Path,
    dry_run: bool,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> None:
    printable = " ".join(argv)
    print(f"$ {printable}")
    if dry_run:
        return
    subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, check=True)


def ensure_free_space(path: Path, min_free_gb: float) -> None:
    free_gb = shutil.disk_usage(path).free / GIB
    if free_gb < min_free_gb:
        raise SystemExit(
            f"Refusing to run with only {free_gb:.1f} GiB free under {path}; "
            f"minimum is {min_free_gb:.1f} GiB."
        )


def check_docker(dry_run: bool) -> None:
    if dry_run:
        print("$ docker info --format '{{.ServerVersion}}'")
        return
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
    except FileNotFoundError as error:
        raise SystemExit("Docker is required for this repo but docker is not on PATH.") from error
    except subprocess.TimeoutExpired as error:
        raise SystemExit("Docker is required for this repo but `docker info` timed out.") from error
    except subprocess.CalledProcessError as error:
        message = (error.stderr or error.stdout or "").strip()
        raise SystemExit(f"Docker is required for this repo but is not healthy: {message}") from error
    print(f"Docker server: {result.stdout.strip()}")


def checkout_ref(source_dir: Path, ref: str | None, fetch: bool, dry_run: bool) -> None:
    if fetch:
        run(
            ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "fetch", "origin"],
            cwd=source_dir,
            dry_run=dry_run,
        )
    if ref:
        run(
            ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "checkout", ref],
            cwd=source_dir,
            dry_run=dry_run,
        )


def copy_helper_and_config(repo: SnippetRepo, source_dir: Path, dry_run: bool) -> Path:
    target_scripts = source_dir / repo.scripts_subdir
    target_helper = target_scripts / repo.helper_name
    target_export = target_scripts / "exportConfig.json"

    print(f"Copy helper: {helper_path()} -> {target_helper}")
    print(f"Copy config: {config_path(repo)} -> {target_export}")
    if dry_run:
        return target_helper

    target_scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(helper_path(), target_helper)
    shutil.copy2(config_path(repo), target_export)
    return target_helper


def prepare_repo(repo: SnippetRepo, source_dir: Path, skip_prepare: bool, dry_run: bool) -> None:
    if skip_prepare or not repo.prepare:
        return
    if repo.needs_docker:
        check_docker(dry_run)
    env = os.environ.copy()
    commands = repo.prepare
    if repo.name == "canton":
        env["SBT_OPTS"] = os.environ.get("SNIPPET_CANTON_SBT_OPTS", "-Xmx8G -Xms2G")
        commands = tuple(f'SBT_OPTS="{env["SBT_OPTS"]}" {command}' for command in commands)
    for command in commands:
        run(command_for_repo(source_dir, command), cwd=source_dir, dry_run=dry_run, env=env)


def run_extraction(
    source_dir: Path,
    helper: Path,
    quiet: bool,
    dry_run: bool,
) -> None:
    relative_helper = helper.relative_to(source_dir)
    command = f"node {relative_helper}"
    if not quiet:
        command += " --verbose"
    run(command_for_repo(source_dir, command), cwd=source_dir, dry_run=dry_run)


def copy_output(repo: SnippetRepo, source_dir: Path, version: str, replace: bool, dry_run: bool) -> Path:
    source_output = source_dir / "docs-output"
    target = CF_DOCS_ROOT / "docs-main" / "snippets" / "external" / repo_label(repo) / version
    if not dry_run and not source_output.is_dir():
        raise SystemExit(f"Expected generated docs-output directory does not exist: {source_output}")
    print(f"Copy output: {source_output} -> {target}")
    if dry_run:
        return target
    if replace and target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_output, target, dirs_exist_ok=True)
    return target


def validate_inputs(repo: SnippetRepo) -> None:
    missing = [
        path
        for path in (helper_path(), config_path(repo))
        if not path.is_file()
    ]
    if missing:
        formatted = "\n  ".join(str(path) for path in missing)
        raise SystemExit(f"Missing required cf-docs file(s):\n  {formatted}")


def main() -> int:
    args = parse_args()
    if args.list:
        list_repos()
        return 0
    if not args.repo:
        raise SystemExit("Missing repo argument. Use --list to show supported repos.")
    repo = REPOS.get(args.repo)
    if not repo:
        raise SystemExit(f"Unknown repo {args.repo!r}. Use --list to show supported repos.")
    validate_inputs(repo)

    source_dir = find_source_dir(repo, args.source_dir)
    print(f"cf-docs: {CF_DOCS_ROOT}")
    print(f"source:  {source_dir}")
    print(f"repo:    {repo.name}")
    ensure_free_space(source_dir, args.min_free_gb)

    checkout_ref(source_dir, args.ref, args.fetch, args.dry_run)
    helper = copy_helper_and_config(repo, source_dir, args.dry_run)
    prepare_repo(repo, source_dir, args.skip_prepare, args.dry_run)
    run_extraction(source_dir, helper, args.quiet, args.dry_run)
    if args.copy_output:
        target = copy_output(repo, source_dir, args.version, args.replace_output, args.dry_run)
        print(f"Copied snippets to {target}")
    print(f"Generated snippets are in {source_dir / 'docs-output'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
