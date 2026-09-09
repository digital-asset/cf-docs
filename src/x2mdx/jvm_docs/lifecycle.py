"""Build lifecycle metadata for local Javadoc/Scaladoc snapshots."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from x2mdx.jvm_docs.models import (
    JvmDocArtifactLifecycle,
    JvmDocArtifactSource,
    JvmDocLifecycleReport,
    JvmDocSymbolLifecycle,
)


def version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    version_text = version[1:] if version.startswith("v") else version
    parts = re.split(r"[.\-+]", version_text)
    key: list[tuple[int, int | str]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def normalize_href(href: str) -> str:
    out = html.unescape(href.strip())
    if out.startswith("./"):
        out = out[2:]
    if out.startswith("/"):
        out = out[1:]
    return out


def strip_html_tags(fragment: str) -> str:
    no_tags = re.sub(r"<[^>]+>", "", fragment, flags=re.S)
    text = html.unescape(no_tags)
    return re.sub(r"\s+", " ", text).strip()


def normalize_type_summary(summary: str) -> str:
    text = re.sub(r"\s+", " ", summary).strip()
    if not text:
        return ""
    if text.lower().startswith("declaration:"):
        return ""

    candidate = text[1:].strip() if text.startswith("-") else text
    if re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+", candidate):
        return ""
    return text


def include_name(name: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    for prefix in prefixes:
        if name == prefix or name.startswith(prefix + ".") or name.startswith(prefix + "$"):
            return True
    return False


def js_assignment_to_json(text: str, variable: str) -> Any:
    pattern = re.compile(
        rf"{re.escape(variable)}\s*=\s*(.+?)\s*;\s*(?:updateSearchResults\(\)\s*;?)?\s*$",
        flags=re.S,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(f"Could not parse JS assignment for {variable}")
    return json.loads(match.group(1).strip())


def javadocio_symbol_url(group: str, artifact: str, version: str, doc_path: str) -> str:
    group_slug = urllib.parse.quote(group, safe="")
    artifact_slug = urllib.parse.quote(artifact, safe="")
    version_slug = urllib.parse.quote(version, safe="")
    return f"https://javadoc.io/doc/{group_slug}/{artifact_slug}/{version_slug}/{doc_path.lstrip('/')}"


def deprecated_refs_from_java_html(text: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    row_pattern = re.compile(
        r'<div class="col-summary-item-name[^"]*">\s*<a href="([^"]+)".*?</a>\s*</div>\s*'
        r'<div class="col-last[^"]*">\s*(.*?)</div>',
        flags=re.S,
    )
    for href, comment_html in row_pattern.findall(text):
        note = strip_html_tags(comment_html) if comment_html else ""
        normalized = normalize_href(href)
        refs[normalized] = note
        refs[urllib.parse.unquote(normalized)] = note
    return refs


def java_member_hrefs(entry: dict[str, Any]) -> set[str]:
    package_name = str(entry.get("p", "")).strip()
    owner = str(entry.get("c", "")).strip()
    if not package_name or not owner:
        return set()

    base = f"{package_name.replace('.', '/')}/{owner}.html"
    raw_anchor = entry.get("u")
    if raw_anchor is None:
        raw_anchor = urllib.parse.quote(str(entry.get("l", "")), safe="")
    href = f"{base}#{raw_anchor}" if raw_anchor else base
    normalized = normalize_href(href)
    return {normalized, urllib.parse.unquote(normalized)}


def java_type_href(entry: dict[str, Any]) -> str | None:
    if entry.get("u"):
        return normalize_href(str(entry["u"]))
    package_name = str(entry.get("p", "")).strip()
    label = str(entry.get("l", "")).strip()
    if not label or label == "All Classes and Interfaces":
        return None
    if package_name:
        return normalize_href(f"{package_name.replace('.', '/')}/{label}.html")
    return normalize_href(f"{label}.html")


def parse_since_from_note(note: str) -> str | None:
    match = re.search(r"\bsince\s+([A-Za-z0-9._-]+)", note, flags=re.I)
    if match:
        return match.group(1).strip()
    return None


def parse_java_symbols(
    archive: zipfile.ZipFile,
    *,
    group: str,
    artifact: str,
    version: str,
    include_prefixes: list[str],
) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []

    deprecated_refs: dict[str, str] = {}
    if "deprecated-list.html" in archive.namelist():
        deprecated_refs = deprecated_refs_from_java_html(
            archive.read("deprecated-list.html").decode("utf-8", errors="replace")
        )

    type_entries: list[dict[str, Any]] = []
    member_entries: list[dict[str, Any]] = []
    if "type-search-index.js" in archive.namelist():
        type_entries = js_assignment_to_json(
            archive.read("type-search-index.js").decode("utf-8", errors="replace"),
            "typeSearchIndex",
        )
    if "member-search-index.js" in archive.namelist():
        member_entries = js_assignment_to_json(
            archive.read("member-search-index.js").decode("utf-8", errors="replace"),
            "memberSearchIndex",
        )

    for entry in type_entries:
        package_name = str(entry.get("p", "")).strip()
        label = str(entry.get("l", "")).strip()
        if not label or label == "All Classes and Interfaces":
            continue
        symbol = f"{package_name}.{label}" if package_name else label
        if not include_name(symbol, include_prefixes):
            continue

        doc_path = java_type_href(entry)
        if not doc_path:
            continue
        symbols.append(
            {
                "symbol_key": f"{artifact}:java:type:{symbol}",
                "language": "java",
                "kind": "type",
                "symbol": symbol,
                "doc_path": doc_path,
                "doc_url": javadocio_symbol_url(group, artifact, version, doc_path),
                "deprecated_note": deprecated_refs.get(doc_path) or deprecated_refs.get(urllib.parse.unquote(doc_path)),
            }
        )

    for entry in member_entries:
        package_name = str(entry.get("p", "")).strip()
        owner = str(entry.get("c", "")).strip()
        label = str(entry.get("l", "")).strip()
        if not package_name or not owner or not label:
            continue
        owner_symbol = f"{package_name}.{owner}"
        if not include_name(owner_symbol, include_prefixes):
            continue

        signature_token = str(entry.get("u", "") or label)
        hrefs = java_member_hrefs(entry)
        if not hrefs:
            continue
        doc_path = sorted(hrefs)[0]
        deprecated_note = next((deprecated_refs[href] for href in hrefs if href in deprecated_refs), None)
        symbols.append(
            {
                "symbol_key": f"{artifact}:java:member:{owner_symbol}#{signature_token}",
                "language": "java",
                "kind": "member",
                "symbol": f"{owner_symbol}#{label}",
                "doc_path": doc_path,
                "doc_url": javadocio_symbol_url(group, artifact, version, doc_path),
                "deprecated_note": deprecated_note,
            }
        )

    return symbols


def first_doc_path_from_scaladoc_entry(entry: dict[str, Any]) -> str | None:
    preferred_keys = [
        "final case class",
        "case class",
        "class",
        "sealed trait",
        "trait",
        "enum",
        "object",
        "case object",
    ]
    for key in preferred_keys:
        value = entry.get(key)
        if isinstance(value, str) and value.endswith(".html"):
            return normalize_href(value)

    for key, value in entry.items():
        if key in {"name", "shortDescription", "kind"} or key.startswith("members_"):
            continue
        if isinstance(value, str) and value.endswith(".html"):
            return normalize_href(value)
    return None


def parse_scala_symbols(
    archive: zipfile.ZipFile,
    *,
    group: str,
    artifact: str,
    version: str,
    include_prefixes: list[str],
) -> list[dict[str, Any]]:
    if "index.js" not in archive.namelist():
        return []

    index_data = js_assignment_to_json(
        archive.read("index.js").decode("utf-8", errors="replace"),
        "Index.PACKAGES",
    )
    symbols: list[dict[str, Any]] = []

    for entries in index_data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if name and include_name(name, include_prefixes):
                doc_path = first_doc_path_from_scaladoc_entry(entry)
                if doc_path:
                    symbols.append(
                        {
                            "symbol_key": f"{artifact}:scala:type:{name}",
                            "language": "scala",
                            "kind": "type",
                            "symbol": name,
                            "doc_path": doc_path,
                            "doc_url": javadocio_symbol_url(group, artifact, version, doc_path),
                            "deprecated_note": None,
                        }
                    )

            for key, members in entry.items():
                if not key.startswith("members_") or not isinstance(members, list):
                    continue
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    member_fqn = str(member.get("member", "")).strip()
                    tail = str(member.get("tail", "")).strip()
                    link = str(member.get("link", "")).strip()
                    if not member_fqn or not link:
                        continue
                    if not include_name(member_fqn, include_prefixes):
                        continue
                    display = f"{member_fqn}{tail}" if tail else member_fqn
                    normalized_link = normalize_href(link)
                    symbols.append(
                        {
                            "symbol_key": f"{artifact}:scala:member:{member_fqn}|{tail}|{normalized_link}",
                            "language": "scala",
                            "kind": "member",
                            "symbol": display,
                            "doc_path": normalized_link,
                            "doc_url": javadocio_symbol_url(group, artifact, version, normalized_link),
                            "deprecated_note": None,
                        }
                    )

    return symbols


def parse_symbols_from_jar(
    jar_path: Path,
    *,
    language: str,
    group: str,
    artifact: str,
    version: str,
    include_prefixes: list[str],
) -> list[dict[str, Any]]:
    with zipfile.ZipFile(jar_path) as archive:
        if language == "java":
            return parse_java_symbols(
                archive,
                group=group,
                artifact=artifact,
                version=version,
                include_prefixes=include_prefixes,
            )
        if language == "scala":
            return parse_scala_symbols(
                archive,
                group=group,
                artifact=artifact,
                version=version,
                include_prefixes=include_prefixes,
            )
    raise ValueError(f"Unsupported JVM docs language: {language}")


def consolidate_lifecycle(
    artifact_source: JvmDocArtifactSource,
    version_symbols: dict[str, list[dict[str, Any]]],
) -> list[JvmDocSymbolLifecycle]:
    versions = [item.version for item in artifact_source.versions]
    version_index = {version: index for index, version in enumerate(versions)}
    aggregate: dict[str, dict[str, Any]] = {}

    for version, symbols in version_symbols.items():
        for symbol in symbols:
            record = aggregate.setdefault(
                symbol["symbol_key"],
                {
                    "language": symbol["language"],
                    "kind": symbol["kind"],
                    "symbol": symbol["symbol"],
                    "versions_present": set(),
                    "doc_links": {},
                    "doc_paths": {},
                    "deprecation_notes": {},
                },
            )
            record["versions_present"].add(version)
            record["doc_links"][version] = symbol["doc_url"]
            record["doc_paths"][version] = symbol["doc_path"]
            if symbol.get("deprecated_note") is not None:
                record["deprecation_notes"][version] = symbol.get("deprecated_note", "")

    lifecycle: list[JvmDocSymbolLifecycle] = []
    for symbol_key, record in aggregate.items():
        present = sorted(record["versions_present"], key=lambda version: version_index[version])
        introduced = present[0]
        last_seen_index = max(version_index[version] for version in present)
        removed = versions[last_seen_index + 1] if last_seen_index + 1 < len(versions) else None

        deprecated_version: str | None = None
        deprecation_note: str | None = None
        if record["deprecation_notes"]:
            observed = sorted(record["deprecation_notes"], key=lambda version: version_index[version])
            deprecated_version = observed[0]
            deprecation_note = record["deprecation_notes"][deprecated_version] or None
            inferred_versions = [
                candidate
                for candidate in (parse_since_from_note(record["deprecation_notes"][version] or "") for version in observed)
                if candidate in version_index
            ]
            if inferred_versions:
                deprecated_version = min(inferred_versions, key=lambda version: version_index[version])

        latest_present = present[-1]
        status: str | None = None
        if record["kind"] == "type" and deprecated_version is not None:
            status = "deprecated"
        lifecycle.append(
            JvmDocSymbolLifecycle(
                symbol_key=symbol_key,
                language=record["language"],
                kind=record["kind"],
                symbol=record["symbol"],
                introduced_version=introduced,
                deprecated_version=deprecated_version,
                removed_version=removed,
                versions_present=present,
                doc_links=dict(record["doc_links"]),
                latest_doc_path=str(record["doc_paths"][latest_present]),
                status=status,
                deprecation_note=deprecation_note,
            )
        )

    lifecycle.sort(key=lambda symbol: (symbol.language, symbol.kind, symbol.symbol, symbol.symbol_key))
    return lifecycle


def parse_java_type_page(raw_html: str) -> tuple[str, str]:
    signature = ""
    summary = ""

    signature_match = re.search(r'<div class="type-signature">(.*?)</div>', raw_html, flags=re.S)
    if signature_match:
        signature = strip_html_tags(signature_match.group(1))

    class_description = re.search(r'<section class="class-description".*?</section>', raw_html, flags=re.S)
    if class_description:
        summary_match = re.search(r'<div class="block">(.*?)</div>', class_description.group(0), flags=re.S)
        if summary_match:
            summary = strip_html_tags(summary_match.group(1))

    if not summary:
        meta_match = re.search(r'<meta name="description" content="([^"]+)"', raw_html)
        if meta_match:
            summary = strip_html_tags(meta_match.group(1))

    return signature, normalize_type_summary(summary)


def parse_scala_type_page(raw_html: str) -> tuple[str, str]:
    signature = ""
    summary = ""

    signature_match = re.search(r'<h4 id="signature" class="signature">(.*?)</h4>', raw_html, flags=re.S)
    if signature_match:
        signature = strip_html_tags(signature_match.group(1))

    summary_match = re.search(
        r'<div id="comment" class="fullcommenttop">.*?<div class="comment cmt"><p>(.*?)</p>',
        raw_html,
        flags=re.S,
    )
    if summary_match:
        summary = strip_html_tags(summary_match.group(1))

    if not summary:
        meta_match = re.search(r'<meta content="([^"]+)" name="description"', raw_html)
        if meta_match:
            summary = strip_html_tags(meta_match.group(1))

    return signature, normalize_type_summary(summary)


def enrich_type_metadata(
    artifact_source: JvmDocArtifactSource,
    symbols: list[JvmDocSymbolLifecycle],
) -> list[JvmDocSymbolLifecycle]:
    version_to_jar_path = {source.version: Path(source.jar_path) for source in artifact_source.versions}
    metadata: dict[str, tuple[str, str]] = {}
    symbols_by_version: dict[str, list[JvmDocSymbolLifecycle]] = {}
    for symbol in symbols:
        if symbol.kind != "type" or not symbol.versions_present:
            continue
        last_present_version = symbol.versions_present[-1]
        symbols_by_version.setdefault(last_present_version, []).append(symbol)

    for version, version_symbols in symbols_by_version.items():
        jar_path = version_to_jar_path.get(version)
        if jar_path is None or not jar_path.exists():
            continue
        with zipfile.ZipFile(jar_path) as archive:
            names = set(archive.namelist())
            for symbol in version_symbols:
                doc_file = symbol.latest_doc_path.split("#", 1)[0]
                if doc_file not in names:
                    continue
                raw_html = archive.read(doc_file).decode("utf-8", errors="replace")
                if artifact_source.language == "java":
                    metadata[symbol.symbol_key] = parse_java_type_page(raw_html)
                else:
                    metadata[symbol.symbol_key] = parse_scala_type_page(raw_html)

    enriched: list[JvmDocSymbolLifecycle] = []
    for symbol in symbols:
        signature, summary = metadata.get(symbol.symbol_key, ("", ""))
        enriched.append(
            replace(
                symbol,
                latest_signature=signature or None,
                latest_summary=summary or None,
            )
        )
    return enriched


def process_artifact_source(artifact_source: JvmDocArtifactSource) -> JvmDocArtifactLifecycle:
    version_symbols: dict[str, list[dict[str, Any]]] = {}
    failures: list[dict[str, str]] = []

    for version_source in artifact_source.versions:
        jar_path = Path(version_source.jar_path)
        try:
            version_symbols[version_source.version] = parse_symbols_from_jar(
                jar_path,
                language=artifact_source.language,
                group=artifact_source.group,
                artifact=artifact_source.artifact,
                version=version_source.version,
                include_prefixes=artifact_source.include_prefixes,
            )
        except Exception as exc:
            failures.append(
                {
                    "version": version_source.version,
                    "error": str(exc),
                    "jar_path": version_source.jar_path,
                }
            )
            version_symbols[version_source.version] = []

    symbols = enrich_type_metadata(artifact_source, consolidate_lifecycle(artifact_source, version_symbols))
    type_count = sum(1 for symbol in symbols if symbol.kind == "type")
    member_count = sum(1 for symbol in symbols if symbol.kind == "member")
    return JvmDocArtifactLifecycle(
        group=artifact_source.group,
        artifact=artifact_source.artifact,
        language=artifact_source.language,
        versions=[source.version for source in artifact_source.versions],
        symbol_count=len(symbols),
        type_count=type_count,
        member_count=member_count,
        failures=failures,
        symbols=symbols,
    )


def build_jvm_doc_lifecycle_report_from_sources(
    sources: list[JvmDocArtifactSource],
    *,
    source_name: str,
    version_filter: str,
) -> JvmDocLifecycleReport:
    artifacts = [process_artifact_source(source) for source in sources]
    total_types = sum(artifact.type_count for artifact in artifacts)
    total_members = sum(artifact.member_count for artifact in artifacts)
    notes = [
        "Input acquisition stays outside x2mdx; this report is built from supplied local Javadoc/Scaladoc jars.",
        "Java deprecation metadata is best-effort from deprecated-list.html when present.",
        "Scala deprecation is not inferred from Scaladoc indexes in this initial implementation.",
        "Removed means the first configured version after the last observed presence.",
    ]
    return JvmDocLifecycleReport(
        source_name=source_name,
        version_filter=version_filter,
        summary={
            "artifact_count": len(artifacts),
            "symbol_count": total_types + total_members,
            "type_count": total_types,
            "member_count": total_members,
        },
        notes=notes,
        artifacts=artifacts,
    )
