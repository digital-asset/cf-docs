from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_MAIN = REPO_ROOT / "docs-main"
NETWORK_DATA_PATH = DOCS_MAIN / "snippets" / "generated" / "version-dashboard-data.mdx"
NETWORKVARS_ROOT = DOCS_MAIN / "snippets" / "networkvars"
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
    r"<NetworkVariables(?P<attrs>[^>]*)>(?P<body>.*?)</NetworkVariables>",
    re.DOTALL,
)
GENERATED_BLOCK_RE = re.compile(
    r"\{/\*\s*NETWORKVARS_START\s+source=\"(?P<source>[^\"]+)\"\s*\*/\}"
    r".*?"
    r"\{/\*\s*NETWORKVARS_END\s*\*/\}",
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
    source = path.read_text(encoding="utf-8")
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


def resolve_mdx_import(import_path: str, docs_main: Path = DOCS_MAIN) -> Path:
    if import_path.startswith("/"):
        return docs_main / import_path.removeprefix("/")
    return docs_main / import_path


def find_imports(text: str, docs_main: Path = DOCS_MAIN) -> dict[str, ImportRef]:
    imports: dict[str, ImportRef] = {}
    for match in IMPORT_RE.finditer(text):
        import_path = match.group("path")
        imports[match.group("name")] = ImportRef(
            name=match.group("name"),
            import_path=import_path,
            file_path=resolve_mdx_import(import_path, docs_main),
            line=match.group(0),
        )
    return imports


def split_source_imports(text: str, docs_main: Path = DOCS_MAIN) -> tuple[dict[str, ImportRef], str]:
    imports = find_imports(text, docs_main)
    body = IMPORT_RE.sub("", text).strip()
    return imports, body


def imported_components_used(body: str, imports: dict[str, ImportRef]) -> list[ImportRef]:
    used: list[ImportRef] = []
    for name, ref in imports.items():
        if re.search(rf"<{re.escape(name)}(?:\s*/|\s|>)", body):
            used.append(ref)
    return used


def source_snippet_path(page_path: Path, block_index: int, docs_main: Path = DOCS_MAIN) -> Path:
    rel = page_path.relative_to(docs_main).with_suffix("")
    return docs_main / "snippets" / "networkvars" / rel.parent / f"{rel.name}-{block_index}.mdx"


def source_ref_for_path(source_path: Path, docs_main: Path = DOCS_MAIN) -> str:
    return "/" + source_path.relative_to(docs_main).as_posix()


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
    docs_main: Path = DOCS_MAIN,
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
        nested_imports, imported_body = split_source_imports(imported_text, docs_main)
        imported_body = inline_imported_components(
            imported_body,
            nested_imports,
            docs_main,
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
    docs_main: Path = DOCS_MAIN,
) -> str:
    body = inline_imported_components(source_body, imports, docs_main)
    body = expand_network_only_blocks(body, network_key)
    body = replace_tokens(body, network_key, network)
    return body.strip()


def render_generated_block(
    source_ref: str,
    source_text: str,
    network_data: dict[str, Any],
    docs_main: Path = DOCS_MAIN,
) -> str:
    imports, source_body = split_source_imports(source_text, docs_main)
    tabs: list[str] = [f'{{/* NETWORKVARS_START source="{source_ref}" */}}', "<Tabs>"]
    for network_key in NETWORK_ORDER:
        network = network_data.get(network_key)
        if not network or not network.get("substitutions"):
            continue
        body = render_network_body(source_body, imports, network_key, network, docs_main)
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


def bootstrap_page(page_path: Path, network_data: dict[str, Any], docs_main: Path = DOCS_MAIN) -> bool:
    text = page_path.read_text(encoding="utf-8")
    if "<NetworkVariables" not in text:
        return False

    page_imports = find_imports(text, docs_main)
    changed = False
    block_index = 0

    def replace_block(match: re.Match[str]) -> str:
        nonlocal block_index, changed
        block_index += 1
        changed = True
        body = match.group("body").strip()
        used_imports = imported_components_used(body, page_imports)
        source_path = source_snippet_path(page_path, block_index, docs_main)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        import_lines = "\n".join(ref.line for ref in used_imports)
        source_text = f"{import_lines}\n\n{body}\n" if import_lines else f"{body}\n"
        if not source_path.exists():
            source_path.write_text(source_text, encoding="utf-8")
        source_ref = source_ref_for_path(source_path, docs_main)
        return render_generated_block(source_ref, source_path.read_text(encoding="utf-8"), network_data, docs_main)

    text = NETWORKVARS_BLOCK_RE.sub(replace_block, text)
    text = clean_unused_imports(text)
    if changed:
        page_path.write_text(text, encoding="utf-8")
    return changed


def update_page(
    page_path: Path,
    network_data: dict[str, Any],
    docs_main: Path = DOCS_MAIN,
    *,
    check: bool = False,
) -> bool:
    text = page_path.read_bytes().decode("utf-8")
    if not GENERATED_BLOCK_RE.search(text):
        return False

    def replace_block(match: re.Match[str]) -> str:
        source_ref = match.group("source")
        source_path = resolve_mdx_import(source_ref, docs_main)
        source_text = source_path.read_text(encoding="utf-8")
        rendered = render_generated_block(source_ref, source_text, network_data, docs_main)
        return re.sub(r"\n{3,}", "\n\n", rendered)

    updated = GENERATED_BLOCK_RE.sub(replace_block, text)
    if updated != text and not check:
        page_path.write_bytes(updated.encode("utf-8"))
    return updated != text


def iter_pages(docs_main: Path = DOCS_MAIN) -> list[Path]:
    return sorted(
        path
        for path in docs_main.rglob("*.mdx")
        if "/snippets/" not in path.as_posix()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate static Mintlify tabs for network variable snippets.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--bootstrap", action="store_true", help="Extract existing NetworkVariables blocks into source snippets before regenerating.")
    mode.add_argument("--check", action="store_true", help="Check generated sections without changing any files.")
    args = parser.parse_args()

    network_data = load_network_data()
    changed_pages: list[Path] = []
    for page_path in iter_pages():
        changed = bootstrap_page(page_path, network_data) if args.bootstrap else update_page(
            page_path, network_data, check=args.check
        )
        if changed:
            changed_pages.append(page_path)

    if args.check and changed_pages:
        changed_list = "\n".join(path.relative_to(REPO_ROOT).as_posix() for path in changed_pages)
        raise SystemExit(
            "Network variable tabs are stale. Run `npm run generate:network-variable-tabs` "
            f"and commit the rendered MDX changes.\n{changed_list}"
        )

    if changed_pages:
        print(f"Updated {len(changed_pages)} page(s).")
    else:
        print("Network variable tabs are rendered and up to date.")


if __name__ == "__main__":
    main()
