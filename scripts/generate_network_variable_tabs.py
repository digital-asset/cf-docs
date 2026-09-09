from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_SOURCE = REPO_ROOT / "docs-source"
DOCS_OUTPUT = REPO_ROOT / "docs-main"
NETWORK_DATA_PATH = DOCS_SOURCE / "snippets" / "generated" / "version-dashboard-data.mdx"
NETWORK_ORDER = ["devnet", "testnet", "mainnet"]

IMPORT_RE = re.compile(
    r"^import\s+(?P<name>[A-Za-z_$][\w$]*)\s+from\s+[\"'](?P<path>[^\"']+\.mdx)[\"'];\s*$",
    re.MULTILINE,
)
NETWORKVARS_IMPORT_RE = re.compile(
    r"^import\s+\{\s*NetworkVariables\s*\}\s+from\s+[\"']/snippets/components/version\.mdx[\"'];\s*\n?",
    re.MULTILINE,
)
NETWORK_DATA_IMPORT_RE = re.compile(
    r"^import\s+\{\s*networkData\s*\}\s+from\s+[\"']/snippets/generated/version-dashboard-data\.mdx[\"'];\s*\n?",
    re.MULTILINE,
)
NETWORKVARS_BLOCK_RE = re.compile(
    r"<NetworkVariables\b(?P<attrs>[^>]*)>(?P<body>.*?)</NetworkVariables>",
    re.DOTALL,
)
TOKEN_RE = re.compile(r"\|([A-Za-z0-9_]+)\|")


@dataclass(frozen=True)
class ImportRef:
    name: str
    import_path: str
    file_path: Path
    line: str


def load_network_data(path: Path = NETWORK_DATA_PATH) -> dict[str, Any]:
    node_script = """
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
// Strip only top-level export declarations (e.g. `export const networkData`),
// not the word "export" inside string substitution values.
const body = source.replace(/^export\\s+/gm, '');
const networkData = Function(`${body}; return networkData;`)();
process.stdout.write(JSON.stringify(networkData));
"""
    result = subprocess.run(
        ["node", "-e", node_script, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def resolve_mdx_import(import_path: str, docs_source: Path = DOCS_SOURCE) -> Path:
    if import_path.startswith("/"):
        return docs_source / import_path.removeprefix("/")
    return docs_source / import_path


def find_imports(text: str, docs_source: Path = DOCS_SOURCE) -> dict[str, ImportRef]:
    imports: dict[str, ImportRef] = {}
    for match in IMPORT_RE.finditer(text):
        import_path = match.group("path")
        imports[match.group("name")] = ImportRef(
            name=match.group("name"),
            import_path=import_path,
            file_path=resolve_mdx_import(import_path, docs_source),
            line=match.group(0),
        )
    return imports


def split_source_imports(text: str, docs_source: Path = DOCS_SOURCE) -> tuple[dict[str, ImportRef], str]:
    imports = find_imports(text, docs_source)
    body = IMPORT_RE.sub("", text).strip()
    return imports, body


def imported_components_used(body: str, imports: dict[str, ImportRef]) -> list[ImportRef]:
    used: list[ImportRef] = []
    for name, ref in imports.items():
        if re.search(rf"<{re.escape(name)}(?:\s*/|\s|>)", body):
            used.append(ref)
    return used


def network_label(network_key: str, network: dict[str, Any]) -> str:
    name = network.get("name", network_key)
    version = network.get("versions", {}).get("splice")
    return f"{name} ({version})" if version else str(name)


def link_label(network_key: str, network: dict[str, Any], replacement: dict[str, Any]) -> str:
    label = replacement.get("label") or replacement.get("href") or ""
    version = network.get("versions", {}).get("splice")
    name = network.get("name", network_key)
    network_suffix = f"{name} {version}" if version else str(name)
    return f"{label} ({network_suffix})" if network_suffix else str(label)


def replacement_text(token: str, network_key: str, network: dict[str, Any]) -> str | None:
    replacement = network.get("substitutions", {}).get(token)
    if replacement is None:
        return None
    if isinstance(replacement, dict) and replacement.get("href"):
        href = str(replacement["href"])
        return f'<a href="{href}">{link_label(network_key, network, replacement)}</a>'
    return str(replacement)


def replace_tokens(text: str, network_key: str, network: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        replacement = replacement_text(token, network_key, network)
        return match.group(0) if replacement is None else replacement

    return TOKEN_RE.sub(replace, text)


def expand_network_only_blocks(text: str, network_key: str) -> str:
    def replace_div(match: re.Match[str]) -> str:
        networks = [item.strip() for item in match.group("networks").split(",") if item.strip()]
        return match.group("body").strip() if network_key in networks else ""

    return re.sub(
        r"<div\s+data-network-only=\"(?P<networks>[^\"]+)\">\s*(?P<body>.*?)\s*</div>",
        replace_div,
        text,
        flags=re.DOTALL,
    )


def prefix_lines(text: str, prefix: str) -> str:
    if not prefix:
        return text
    blank_prefix = ">" if ">" in prefix else ""
    return "\n".join(f"{prefix}{line}" if line else blank_prefix for line in text.splitlines())


def inline_imported_components(
    text: str,
    imports: dict[str, ImportRef],
    docs_source: Path = DOCS_SOURCE,
    stack: tuple[Path, ...] = (),
) -> str:
    rendered = text
    for name, ref in imports.items():
        if not ref.file_path.exists():
            raise FileNotFoundError(f"Missing imported snippet for {name}: {ref.file_path}")
        if ref.file_path in stack:
            cycle = " -> ".join(path.as_posix() for path in (*stack, ref.file_path))
            raise ValueError(f"Recursive network variable snippet import: {cycle}")
        imported_text = ref.file_path.read_text(encoding="utf-8").strip()
        nested_imports, imported_body = split_source_imports(imported_text, docs_source)
        imported_body = inline_imported_components(
            imported_body,
            nested_imports,
            docs_source,
            (*stack, ref.file_path),
        )
        rendered = re.sub(
            rf"(?m)^(?P<prefix>[ \t>]*)<{re.escape(name)}\s*/>\s*$",
            lambda match: prefix_lines(imported_body, match.group("prefix")),
            rendered,
        )
        rendered = re.sub(
            rf"(?m)^(?P<prefix>[ \t>]*)<{re.escape(name)}>\s*</{re.escape(name)}>\s*$",
            lambda match: prefix_lines(imported_body, match.group("prefix")),
            rendered,
        )
        rendered = re.sub(rf"<{re.escape(name)}\s*/>", imported_body, rendered)
        rendered = re.sub(rf"<{re.escape(name)}>\s*</{re.escape(name)}>", imported_body, rendered)
    return rendered


def render_network_body(
    source_body: str,
    imports: dict[str, ImportRef],
    network_key: str,
    network: dict[str, Any],
    docs_source: Path = DOCS_SOURCE,
) -> str:
    body = inline_imported_components(source_body, imports, docs_source)
    body = expand_network_only_blocks(body, network_key)
    body = replace_tokens(body, network_key, network)
    return body.strip()


def render_generated_block(
    source_ref: str,
    source_text: str,
    network_data: dict[str, Any],
    docs_source: Path = DOCS_SOURCE,
) -> str:
    imports, source_body = split_source_imports(source_text, docs_source)
    tabs: list[str] = [f'{{/* NETWORKVARS_START source="{source_ref}" */}}', "<Tabs>"]
    for network_key in NETWORK_ORDER:
        network = network_data.get(network_key)
        if not network or not network.get("substitutions"):
            continue
        body = render_network_body(source_body, imports, network_key, network, docs_source)
        tabs.extend(
            [
                "",
                f'<Tab title="{network_label(network_key, network)}">',
                "",
                body,
                "",
                "</Tab>",
            ]
        )
    tabs.extend(["", "</Tabs>", "{/* NETWORKVARS_END */}"])
    return "\n".join(tabs)


def clean_unused_imports(text: str) -> str:
    text = NETWORKVARS_IMPORT_RE.sub("", text)
    network_data_match = NETWORK_DATA_IMPORT_RE.search(text)
    if network_data_match:
        without_network_data_import = text[: network_data_match.start()] + text[network_data_match.end() :]
        if "networkData" not in without_network_data_import:
            text = without_network_data_import

    while True:
        removed = False
        for match in IMPORT_RE.finditer(text):
            name = match.group("name")
            without_line = text[: match.start()] + text[match.end() :]
            if not re.search(rf"\b{re.escape(name)}\b", without_line):
                text = without_line
                removed = True
                break
        if not removed:
            break
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def render_page(text: str, source_ref: str, network_data: dict[str, Any], docs_source: Path) -> str:
    """Expand authored blocks without modifying the source page or its imports."""
    if not NETWORKVARS_BLOCK_RE.search(text):
        return text
    imports = find_imports(text, docs_source)

    def replace_block(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        used_imports = imported_components_used(body, imports)
        snippet = "\n".join(ref.line for ref in used_imports) + "\n\n" + body
        return render_generated_block(source_ref, snippet, network_data, docs_source)

    return clean_unused_imports(NETWORKVARS_BLOCK_RE.sub(replace_block, text))


IGNORED_NAMES = {".DS_Store", ".git", ".mintlify", ".internal", "node_modules", "__pycache__"}


def corpus_files(root: Path) -> dict[Path, Path]:
    return {
        path.relative_to(root): path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not any(part in IGNORED_NAMES for part in path.relative_to(root).parts)
    }


def generate_site(
    docs_source: Path = DOCS_SOURCE,
    docs_output: Path = DOCS_OUTPUT,
    *,
    check: bool = False,
) -> list[Path]:
    """Copy the entire corpus, rendering network blocks only in published pages.

    Check mode compares bytes and the complete file set without writing anything.
    Rendering completes before output is touched so a broken input cannot leave a
    partially regenerated site.
    """
    source_root, output_root = docs_source.resolve(), docs_output.resolve()
    if source_root == output_root or source_root in output_root.parents or output_root in source_root.parents:
        raise ValueError("Source and output must be separate, non-overlapping directories")
    if not (docs_source / "docs.json").is_file():
        raise FileNotFoundError(f"Missing source site configuration: {docs_source / 'docs.json'}")
    files = corpus_files(docs_source)
    network_data = load_network_data(docs_source / "snippets/generated/version-dashboard-data.mdx")
    expected: dict[Path, bytes] = {}
    for relative, path in files.items():
        content = path.read_bytes()
        if path.suffix in {".md", ".mdx"} and relative.parts[0] != "snippets":
            content = render_page(content.decode("utf-8"), "/" + relative.as_posix(), network_data, docs_source).encode("utf-8")
        expected[relative] = content

    current = corpus_files(docs_output)
    changed = sorted(
        relative for relative, content in expected.items()
        if relative not in current or current[relative].read_bytes() != content
    )
    removed = sorted(current.keys() - expected.keys())
    if not check:
        # Remove obsolete files first, including file-to-directory renames.
        for relative in removed:
            current[relative].unlink()
        for directory in sorted(docs_output.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        for relative in changed:
            destination = docs_output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(expected[relative])
    return [docs_output / relative for relative in sorted(set(changed + removed))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy docs-source to docs-main and render static network variable tabs.")
    parser.add_argument("--check", action="store_true", help="Check every output file without changing source or output.")
    args = parser.parse_args()
    changed = generate_site(check=args.check)
    if args.check and changed:
        changed_list = "\n".join(path.relative_to(REPO_ROOT).as_posix() for path in changed)
        raise SystemExit(f"Network variable tabs are stale. Run `npm run generate:network-variable-tabs` "
                         f"and commit docs-source and docs-main.\n{changed_list}")
    if changed:
        print(f"Updated {len(changed)} output file(s).")
    else:
        print("Network variable tabs are rendered and up to date.")


if __name__ == "__main__":
    main()
