#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from docs_env import ensure_repo_direnv


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = REPO_ROOT / ".internal" / "cache" / "canton-metrics-reference"
DEFAULT_CANTON_DIR = DEFAULT_CACHE_DIR / "repos" / "canton"
DEFAULT_OUTPUT = REPO_ROOT / "docs-main" / "global-synchronizer" / "reference" / "canton-metrics.mdx"
DEFAULT_REMOTE = "https://github.com/digital-asset/canton.git"
DEFAULT_RELEASE_REPO = "digital-asset/canton"
METRICS_RST = Path("docs-open/src/sphinx/participant/reference/metrics.rst")
GENERATED_INCLUDES_DIR = Path("docs-open/target/generated")
USER_AGENT = "cf-docs-canton-metrics-reference/1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Canton Metrics MDX page from the latest Canton release docs build."
    )
    parser.add_argument("--release-repo", default=DEFAULT_RELEASE_REPO)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument(
        "--canton-ref",
        help="Canton git ref to generate from. Defaults to the latest GitHub release tag.",
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--canton-dir",
        type=Path,
        help="Existing or cached Canton checkout. Defaults to <cache-dir>/repos/canton.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--generation-command",
        nargs="+",
        default=["sbt", "--batch", "community-app / bundle", "docs-open / generateIncludes"],
        help=(
            "Command to run inside the Canton checkout before reading the metrics RST template and generated "
            "include files. Quote each SBT task when overriding this."
        ),
    )
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Use already-generated include files in the Canton checkout.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Delete and reclone the cached Canton checkout before generation.",
    )
    parser.add_argument(
        "--skip-direnv",
        action="store_true",
        help="Run the Canton generation command directly instead of through direnv.",
    )
    return parser.parse_args()


def run(command: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "check": True,
        "text": True,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    completed = subprocess.run(command, **kwargs)
    return completed.stdout.strip() if capture else ""


def github_api_json(path: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/{path.lstrip('/')}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_release_tag(release_repo: str) -> str:
    gh = shutil.which("gh")
    if gh:
        try:
            tag = run(
                [
                    gh,
                    "release",
                    "view",
                    "--repo",
                    release_repo,
                    "--json",
                    "tagName",
                    "--jq",
                    ".tagName",
                ],
                capture=True,
            )
            if tag:
                return tag
        except subprocess.CalledProcessError:
            pass

    payload = github_api_json(f"repos/{release_repo}/releases/latest")
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag:
        raise ValueError(f"Unable to resolve latest release tag for {release_repo}")
    return tag


def checkout_canton_release(*, canton_dir: Path, remote: str, ref: str, force_refresh: bool) -> None:
    if force_refresh and canton_dir.exists():
        shutil.rmtree(canton_dir)
    canton_dir.parent.mkdir(parents=True, exist_ok=True)
    if not (canton_dir / ".git").exists():
        run(["git", "clone", remote, str(canton_dir)])
    run(["git", "fetch", "origin", "--tags", "--prune"], cwd=canton_dir)
    run(["git", "checkout", "--detach", ref], cwd=canton_dir)
    run(["git", "reset", "--hard", ref], cwd=canton_dir)
    run(["git", "clean", "-ffd"], cwd=canton_dir)


def allow_direnv(canton_dir: Path) -> None:
    if not (canton_dir / ".envrc").exists() or not shutil.which("direnv"):
        return
    run(["direnv", "allow"], cwd=canton_dir)


def run_generation(*, canton_dir: Path, command: list[str], skip_direnv: bool) -> None:
    generated = canton_dir / GENERATED_INCLUDES_DIR
    if generated.exists():
        shutil.rmtree(generated)
    generated.mkdir(parents=True, exist_ok=True)
    command_without_ci = ["env", "-u", "CI", *command]
    if skip_direnv or not (canton_dir / ".envrc").exists() or not shutil.which("direnv"):
        run(command_without_ci, cwd=canton_dir)
        return
    allow_direnv(canton_dir)
    run(["direnv", "exec", str(canton_dir), *command_without_ci], cwd=canton_dir)


def resolve_generated_includes(template: str, *, generated_dir: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        include_name = match.group(1)
        include_path = generated_dir / include_name
        if not include_path.is_file():
            raise FileNotFoundError(f"Expected generated Canton include at {include_path}")
        return include_path.read_text(encoding="utf-8").rstrip()

    return re.sub(r"^\.\. generatedinclude::\s+(\S+)\s*$", replace, template, flags=re.MULTILINE)


def convert_inline(text: str) -> str:
    text = re.sub(r"`([^`<]+?) <([^`>]+)>`_", r"[\1](\2)", text)
    text = re.sub(r"``([^`]+)``", r"`\1`", text)
    text = text.replace("<", r"\<").replace(">", r"\>")
    return text.rstrip()


def escape_heading(text: str) -> str:
    return convert_inline(text).replace("*", r"\*")


def convert_rst_to_mdx(rst: str, *, source_ref: str) -> str:
    if "generatedinclude::" in rst:
        raise ValueError("Metrics RST still contains generatedinclude directives; run the Canton docs generator first.")

    output: list[str] = [
        "---",
        'title: "Canton Metrics"',
        'description: "Canton node metrics exported for Prometheus scraping."',
        "---",
        "",
        (
            "{/* GENERATED_FROM "
            f'source="digital-asset/canton" ref="{source_ref}" '
            f'path="{METRICS_RST.as_posix()}" */}}'
        ),
        "",
    ]

    lines = rst.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()

        if not stripped:
            output.append("")
            index += 1
            continue

        if stripped == "..":
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index].startswith((" ", "\t"))):
                index += 1
            continue

        if stripped.startswith(".. "):
            index += 1
            continue

        if index + 1 < len(lines):
            underline = lines[index + 1].strip()
            if underline and len(underline) >= len(stripped) and set(underline) <= {"-", "~", "^"}:
                marker = underline[0]
                level = {"-": "#", "~": "##", "^": "###"}[marker]
                output.append(f"{level} {escape_heading(stripped)}")
                output.append("")
                index += 2
                continue

        bullet = re.match(r"^(\s*)\*\s+(.*)$", line)
        if bullet:
            indent = len(bullet.group(1).replace("\t", "    "))
            nested = "  " if indent >= 8 else ""
            output.append(f"> {nested}- {convert_inline(bullet.group(2))}")
            index += 1
            continue

        output.append(convert_inline(stripped))
        index += 1

    content = "\n".join(output)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.rstrip() + "\n"


def main() -> int:
    args = parse_args()
    ensure_repo_direnv(repo_root=REPO_ROOT, script_path=Path(__file__).resolve(), argv=sys.argv[1:])
    if args.canton_dir is None:
        args.canton_dir = args.cache_dir / "repos" / "canton"

    source_ref = args.canton_ref or latest_release_tag(args.release_repo)
    checkout_canton_release(
        canton_dir=args.canton_dir,
        remote=args.remote,
        ref=source_ref,
        force_refresh=args.force_refresh,
    )
    if not args.skip_generation:
        run_generation(canton_dir=args.canton_dir, command=args.generation_command, skip_direnv=args.skip_direnv)

    metrics_rst = args.canton_dir / METRICS_RST
    if not metrics_rst.is_file():
        raise FileNotFoundError(f"Expected Canton metrics page template at {metrics_rst}")

    resolved_rst = resolve_generated_includes(
        metrics_rst.read_text(encoding="utf-8"),
        generated_dir=args.canton_dir / GENERATED_INCLUDES_DIR,
    )
    mdx = convert_rst_to_mdx(resolved_rst, source_ref=source_ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(mdx, encoding="utf-8")
    print(f"Generated {args.output} from Canton {source_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
