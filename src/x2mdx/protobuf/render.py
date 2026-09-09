"""Render descriptor-backed protobuf reports into Mintlify-like collection and operation pages."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import cmp_to_key
from pathlib import Path
from typing import Any

from x2mdx.history.events import history_events_for_item
from x2mdx.history.models import HistoryEvent, HistoryEventKind, HistoryItem, SurfaceHistoryReport
from x2mdx.history.versioning import compare_versions
from x2mdx.reference_pages import (
    ReferenceBadge,
    ReferenceBreadcrumb,
    ReferenceCard,
    ReferenceChange,
    ReferenceCollectionPage,
    ReferenceExample,
    ReferenceField,
    ReferenceMetaItem,
    ReferenceOperationPage,
    ReferencePanel,
    ReferenceSchema,
    ReferenceSection,
    compact_text,
    relative_page_ref,
    render_collection_page,
    render_operation_page,
    reference_badges_for_history_events,
    reference_badges_for_history_item,
    safe_markdown_text,
)


PACKAGE_GROUP_ORDER = [
    "Ledger API",
    "Participant Administration",
    "Sequencer",
    "Mediator",
    "Shared Administration",
    "Other APIs",
    "Schema Packages",
]

GRPC_TARGET_PLACEHOLDER = "<HOST:PORT>"
REQUEST_SAMPLE_MAX_DEPTH = 4
REQUEST_SAMPLE_MAX_FIELDS = 8
LIFECYCLE_STATES = {"alpha", "beta", "stable", "deprecated"}


def slugify_segment(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "item"


def package_group(package_name: str, *, has_services: bool) -> str:
    if not has_services:
        return "Schema Packages"
    if package_name.startswith("com.daml.ledger.api.v2"):
        return "Ledger API"
    if ".participant." in package_name:
        return "Participant Administration"
    if "sequencer" in package_name:
        return "Sequencer"
    if "mediator" in package_name:
        return "Mediator"
    if package_name.startswith(
        (
            "com.digitalasset.canton.admin.health",
            "com.digitalasset.canton.connection",
            "com.digitalasset.canton.crypto",
            "com.digitalasset.canton.time",
            "com.digitalasset.canton.topology",
        )
    ):
        return "Shared Administration"
    return "Other APIs"


def package_group_sort_key(package_name: str, *, has_services: bool) -> tuple[int, str]:
    label = package_group(package_name, has_services=has_services)
    return (PACKAGE_GROUP_ORDER.index(label), package_name)


def lifecycle_badges(
    *,
    introduced: str | None = None,
    changed: str | None = None,
    removed: str | None = None,
    item: HistoryItem | None = None,
    events: list[HistoryEvent] | None = None,
    comparison_versions: tuple[str, ...] = (),
    linked: bool = True,
) -> list[ReferenceBadge]:
    if item is not None:
        return reference_badges_for_history_item(
            item,
            kind_label="gRPC",
            comparison_versions=comparison_versions,
            linked=linked,
        )
    if events is not None:
        return reference_badges_for_history_events(events, kind_label="gRPC", linked=linked)
    if introduced is None:
        return [ReferenceBadge("gRPC", tone="protocol")]
    badges = [ReferenceBadge("gRPC", tone="protocol"), ReferenceBadge(f"Since {introduced}", tone="added")]
    if changed and changed != introduced:
        badges.append(ReferenceBadge(f"Changed {changed}", tone="changed"))
    if removed:
        badges.append(ReferenceBadge(f"Removed {removed}", tone="removed"))
    return badges


def lifecycle_meta_items(entity: dict[str, Any]) -> list[ReferenceMetaItem]:
    metadata = entity.get("metadata")
    lifecycle = metadata.get("lifecycle") if isinstance(metadata, dict) else None
    if not isinstance(lifecycle, dict):
        return []

    items: list[ReferenceMetaItem] = []
    raw_state = lifecycle.get("state")
    if raw_state is not None:
        if not isinstance(raw_state, str) or raw_state.strip().lower() not in LIFECYCLE_STATES:
            allowed = ", ".join(sorted(LIFECYCLE_STATES))
            raise ValueError(
                f"Unsupported Protobuf lifecycle state {raw_state!r} for {entity['id']}; "
                f"expected one of {allowed}"
            )
        items.append(ReferenceMetaItem("Lifecycle", raw_state.strip().title()))

    replaces = lifecycle.get("replaces")
    if replaces is not None:
        if not isinstance(replaces, str) or not replaces.strip():
            raise ValueError(
                f"Protobuf replacement target for {entity['id']} must be a non-empty string"
            )
        items.append(ReferenceMetaItem("Replaces", replaces.strip()))
    return items


def aggregate_history_events(
    items: list[HistoryItem],
    *,
    comparison_versions: tuple[str, ...],
) -> list[HistoryEvent]:
    combined: dict[tuple[HistoryEventKind, str], HistoryEvent] = {}
    for item in items:
        endpoint = item.id
        for event in history_events_for_item(item, comparison_versions=comparison_versions):
            key = (event.kind, event.version)
            details = tuple(
                f"{endpoint}: {detail}" for detail in event.details
            ) or (f"{endpoint}",)
            existing = combined.get(key)
            if existing is None:
                combined[key] = HistoryEvent(
                    kind=event.kind,
                    version=event.version,
                    label=event.label,
                    details=details,
                    evidence=event.evidence,
                )
            else:
                combined[key] = HistoryEvent(
                    kind=existing.kind,
                    version=existing.version,
                    label=existing.label,
                    details=tuple(dict.fromkeys((*existing.details, *details))),
                    evidence=tuple(dict.fromkeys((*existing.evidence, *event.evidence))),
                )

    priority = {
        HistoryEventKind.REMOVE_AS_OF: 0,
        HistoryEventKind.DEPRECATED: 1,
        HistoryEventKind.CHANGED: 2,
        HistoryEventKind.INTRODUCED: 3,
        HistoryEventKind.REPLACEMENT: 4,
    }

    def compare(left: HistoryEvent, right: HistoryEvent) -> int:
        version_comparison = compare_versions(
            left.version,
            right.version,
            known_order=comparison_versions,
        )
        if version_comparison:
            return -version_comparison
        return priority[left.kind] - priority[right.kind]

    return sorted(combined.values(), key=cmp_to_key(compare))


def page_ref(from_path: Path, to_path: Path) -> str:
    return relative_page_ref(from_path, to_path)


def package_page_path(output_dir: Path, package_name: str) -> Path:
    return output_dir / "packages" / f"{slugify_segment(package_name)}.mdx"


def operation_page_path(output_dir: Path, package_name: str, service_name: str, endpoint_name: str) -> Path:
    return (
        output_dir
        / "operations"
        / slugify_segment(package_name)
        / slugify_segment(service_name)
        / f"{slugify_segment(endpoint_name)}.mdx"
    )


def compact_package_summary(package: dict[str, Any]) -> str:
    parts = [
        f"{package['serviceCount']} services",
        f"{package['endpointCount']} endpoints",
        f"{package['messageCount']} messages",
    ]
    if package["enumCount"]:
        parts.append(f"{package['enumCount']} enums")
    return ", ".join(parts)


def endpoint_snapshot_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest_endpoints = dict(report["latestSnapshot"]["endpoints"])
    snapshots = dict(latest_endpoints)
    for release in reversed(report["releases"]):
        for endpoint in release["changes"]["endpoints"]["removed"]:
            snapshots.setdefault(endpoint["id"], endpoint)
    return snapshots


def build_package_docs(report: dict[str, Any]) -> list[dict[str, Any]]:
    latest = report["latestSnapshot"]
    package_docs = {package["package"]: dict(package) for package in latest["packages"]}
    current_services = latest["services"]
    current_endpoints = latest["endpoints"]

    for entry in report["endpointLifecycle"]:
        package_doc = package_docs.setdefault(
            entry["package"],
            {
                "package": entry["package"],
                "fileIds": [],
                "fileCount": 0,
                "serviceIds": [],
                "serviceCount": 0,
                "endpointIds": [],
                "endpointCount": 0,
                "messageIds": [],
                "messageCount": 0,
                "enumIds": [],
                "enumCount": 0,
            },
        )
        if entry["id"] not in package_doc["endpointIds"] and entry["id"] in current_endpoints:
            package_doc["endpointIds"].append(entry["id"])

    for service_id, service_doc in current_services.items():
        package_doc = package_docs.setdefault(
            service_doc["package"],
            {
                "package": service_doc["package"],
                "fileIds": [],
                "fileCount": 0,
                "serviceIds": [],
                "serviceCount": 0,
                "endpointIds": [],
                "endpointCount": 0,
                "messageIds": [],
                "messageCount": 0,
                "enumIds": [],
                "enumCount": 0,
            },
        )
        if service_id not in package_doc["serviceIds"]:
            package_doc["serviceIds"].append(service_id)

    for package_doc in package_docs.values():
        package_doc["serviceIds"] = sorted(package_doc["serviceIds"])
        package_doc["endpointIds"] = sorted(package_doc["endpointIds"])
        package_doc["serviceCount"] = len(package_doc["serviceIds"])
        package_doc["endpointCount"] = len(package_doc["endpointIds"])

    return sorted(
        package_docs.values(),
        key=lambda package: package_group_sort_key(
            package["package"],
            has_services=bool(package["serviceCount"] or package["endpointCount"]),
        ),
    )


def field_type_label(field: dict[str, Any]) -> str:
    if field.get("map"):
        base = "map"
    elif field.get("typeName"):
        base = str(field["typeName"])
    else:
        base = str(field.get("type") or "-")
    if field.get("label") == "repeated":
        return f"repeated {base}"
    return base


def short_type_name(type_name: str) -> str:
    return type_name.rsplit(".", 1)[-1] if "." in type_name else type_name


def display_field_type(field: dict[str, Any]) -> str:
    type_label = field_type_label(field)
    if type_label.startswith("repeated "):
        return f"repeated {short_type_name(type_label.removeprefix('repeated '))}"
    return short_type_name(type_label)


def message_schema(
    message: dict[str, Any],
    ctx: dict[str, dict[str, Any]],
    *,
    seen: set[str],
) -> list[ReferenceSchema]:
    if message["id"] in seen:
        return []
    seen.add(message["id"])

    schemas = [
        ReferenceSchema(
            name=message["id"],
            summary=f"{len(message['fieldIds'])} fields",
            description=str(message.get("description") or ""),
            anchor=f"schema-{slugify_segment(message['id'])}",
            fields=[
                ReferenceField(
                    name=field["name"],
                    type_label=display_field_type(field),
                    required=field.get("label") == "required",
                    description=str(field.get("description") or ""),
                )
                for field_id in message["fieldIds"]
                for field in [ctx["fields"][field_id]]
            ],
        )
    ]

    for enum_id in message.get("enumIds", []):
        schemas.extend(enum_schema(ctx["enums"][enum_id], ctx, seen=seen))
    for nested_id in message.get("nestedMessageIds", []):
        schemas.extend(message_schema(ctx["messages"][nested_id], ctx, seen=seen))
    for field_id in message["fieldIds"]:
        field = ctx["fields"][field_id]
        type_name = field.get("typeName")
        if type_name and type_name in ctx["messages"]:
            schemas.extend(message_schema(ctx["messages"][type_name], ctx, seen=seen))
        elif type_name and type_name in ctx["enums"]:
            schemas.extend(enum_schema(ctx["enums"][type_name], ctx, seen=seen))
    return schemas


def enum_schema(enum_doc: dict[str, Any], ctx: dict[str, dict[str, Any]], *, seen: set[str]) -> list[ReferenceSchema]:
    if enum_doc["id"] in seen:
        return []
    seen.add(enum_doc["id"])
    return [
        ReferenceSchema(
            name=enum_doc["id"],
            summary=f"{len(enum_doc['valueIds'])} values",
            description=str(enum_doc.get("description") or ""),
            anchor=f"schema-{slugify_segment(enum_doc['id'])}",
            enum_values=[
                str(ctx_value["name"])
                for value_id in enum_doc["valueIds"]
                for ctx_value in [ctx["enumValues"][value_id]]
            ],
        )
    ]


def related_schemas_for_types(
    type_names: list[str],
    ctx: dict[str, dict[str, Any]],
) -> list[ReferenceSchema]:
    seen: set[str] = set()
    schemas: list[ReferenceSchema] = []
    for type_name in type_names:
        if type_name in ctx["messages"]:
            schemas.extend(message_schema(ctx["messages"][type_name], ctx, seen=seen))
        elif type_name in ctx["enums"]:
            schemas.extend(enum_schema(ctx["enums"][type_name], ctx, seen=seen))
    return schemas


def endpoint_signature(endpoint: dict[str, Any]) -> str:
    request_prefix = "stream " if endpoint["clientStreaming"] else ""
    response_prefix = "stream " if endpoint["serverStreaming"] else ""
    return (
        f"rpc {endpoint['service']}.{endpoint['name']}("
        f"{request_prefix}{endpoint['requestType']}) returns "
        f"({response_prefix}{endpoint['responseType']});"
    )


def scalar_json_sample(type_name: str | None) -> Any:
    normalized = str(type_name or "").lower()
    if normalized in {"double", "float"}:
        return 0.0
    if normalized in {"int64", "sint64", "sfixed64", "uint64", "fixed64"}:
        return "0"
    if normalized in {"int32", "sint32", "sfixed32", "uint32", "fixed32"}:
        return 0
    if normalized == "bool":
        return True
    if normalized == "bytes":
        return "BASE64_ENCODED_BYTES"
    if normalized:
        return "string"
    return {}


def enum_json_sample(enum_name: str, ctx: dict[str, dict[str, Any]]) -> str:
    enum_doc = ctx["enums"].get(enum_name)
    if not enum_doc:
        return "ENUM_VALUE"
    for value_id in enum_doc.get("valueIds", []):
        enum_value = ctx["enumValues"].get(value_id)
        if enum_value:
            return str(enum_value["name"])
    return "ENUM_VALUE"


def type_json_sample(
    type_name: str | None,
    ctx: dict[str, dict[str, Any]],
    *,
    depth: int,
    seen_messages: set[str],
) -> Any:
    if type_name and type_name in ctx["messages"]:
        return message_json_sample(type_name, ctx, depth=depth, seen_messages=seen_messages)
    if type_name and type_name in ctx["enums"]:
        return enum_json_sample(type_name, ctx)
    return scalar_json_sample(type_name)


def field_json_sample(
    field: dict[str, Any],
    ctx: dict[str, dict[str, Any]],
    *,
    depth: int,
    seen_messages: set[str],
) -> Any:
    if field.get("map"):
        value_sample = type_json_sample(field.get("valueType"), ctx, depth=depth - 1, seen_messages=seen_messages)
        sample: Any = {"key": value_sample}
    elif field.get("typeName"):
        sample = type_json_sample(field.get("typeName"), ctx, depth=depth - 1, seen_messages=seen_messages)
    else:
        sample = scalar_json_sample(field.get("type"))
    if field.get("label") == "repeated":
        return [sample]
    return sample


def message_json_sample(
    message_name: str,
    ctx: dict[str, dict[str, Any]],
    *,
    depth: int = REQUEST_SAMPLE_MAX_DEPTH,
    seen_messages: set[str] | None = None,
) -> Any:
    if depth <= 0:
        return {}
    message = ctx["messages"].get(message_name)
    if not message:
        return {}
    if seen_messages is None:
        seen_messages = set()
    if message_name in seen_messages:
        return {}

    next_seen = seen_messages | {message_name}
    sample: dict[str, Any] = {}
    rendered_oneofs: set[str] = set()

    for field_id in message.get("fieldIds", [])[:REQUEST_SAMPLE_MAX_FIELDS]:
        field = ctx["fields"][field_id]
        oneof_name = field.get("oneof")
        if oneof_name:
            if oneof_name in rendered_oneofs:
                continue
            rendered_oneofs.add(oneof_name)
        sample[str(field.get("jsonName") or field["name"])] = field_json_sample(
            field,
            ctx,
            depth=depth,
            seen_messages=next_seen,
        )
    return sample


def grpc_method_name(package_name: str, endpoint: dict[str, Any]) -> str:
    endpoint_id = endpoint.get("id")
    if endpoint_id:
        return str(endpoint_id)
    return f"{package_name}.{endpoint['service']}/{endpoint['name']}"


def grpcurl_example(package_name: str, endpoint: dict[str, Any], request_body: Any) -> str:
    body = json.dumps(request_body, indent=2, ensure_ascii=False)
    lines = [
        "# Add -plaintext if the server is not using TLS.",
        "grpcurl \\",
        "  -d @ \\",
        f"  {GRPC_TARGET_PLACEHOLDER} \\",
        f"  {grpc_method_name(package_name, endpoint)} <<'EOF'",
        body,
        "EOF",
    ]
    if endpoint.get("clientStreaming") or endpoint.get("serverStreaming"):
        lines.insert(1, "# This RPC uses streaming semantics. Send additional JSON messages on stdin as needed.")
    return "\n".join(lines)


def build_overview_page(
    report: dict[str, Any],
    *,
    output_dir: Path,
    package_docs: list[dict[str, Any]],
    history_report: SurfaceHistoryReport | None = None,
    overview_name: str = "index.mdx",
) -> ReferenceCollectionPage:
    history_items = list(history_report.items) if history_report is not None else []
    overview_events = (
        aggregate_history_events(
            history_items,
            comparison_versions=history_report.comparison_versions,
        )
        if history_report is not None
        else []
    )
    package_page_map = {package["package"]: package_page_path(output_dir, package["package"]) for package in package_docs}
    package_groups: dict[str, list[ReferenceCard]] = defaultdict(list)
    for package in package_docs:
        group_label = package_group(package["package"], has_services=bool(package["serviceCount"] or package["endpointCount"]))
        package_items = [
            item
            for item in history_items
            if item.id.startswith(f"{package['package']}.")
        ]
        package_events = (
            aggregate_history_events(
                package_items,
                comparison_versions=history_report.comparison_versions,
            )
            if history_report is not None
            else []
        )
        package_groups[group_label].append(
            ReferenceCard(
                title=package["package"],
                href=page_ref(output_dir / overview_name, package_page_map[package["package"]]),
                summary=compact_package_summary(package),
                badges=lifecycle_badges(events=package_events, linked=False),
                meta_items=[
                    ReferenceMetaItem("Services", str(package["serviceCount"])),
                    ReferenceMetaItem("Endpoints", str(package["endpointCount"])),
                    ReferenceMetaItem("Messages", str(package["messageCount"])),
                    ReferenceMetaItem("Enums", str(package["enumCount"])),
                ],
            )
        )

    release_cards = []
    for release in report["releases"]:
        counts = release["changes"]["counts"]
        release_cards.append(
            ReferenceCard(
                title=str(release["version"]),
                summary="Endpoint / message / enum deltas for this release.",
                badges=[ReferenceBadge("Release", tone="neutral")],
                meta_items=[
                    ReferenceMetaItem("Endpoints", f"{counts['endpoints']['added']} / {counts['endpoints']['modified']} / {counts['endpoints']['removed']}"),
                    ReferenceMetaItem("Messages", f"{counts['messages']['added']} / {counts['messages']['modified']} / {counts['messages']['removed']}"),
                    ReferenceMetaItem("Enums", f"{counts['enums']['added']} / {counts['enums']['modified']} / {counts['enums']['removed']}"),
                ],
            )
        )

    sections = [
        ReferenceSection(
            heading="Release Summary",
            body_markdown="Counts are shown as added / changed / removed within each release slice.",
            cards=release_cards,
        )
    ]
    for label in PACKAGE_GROUP_ORDER:
        if label in package_groups:
            sections.append(
                ReferenceSection(
                    heading=label,
                    cards=package_groups[label],
                )
            )
    latest = report["latestSnapshot"]
    return ReferenceCollectionPage(
        path=overview_name,
        title="Canton Protobuf Reference",
        description="Descriptor-backed protobuf API history grouped by package.",
        eyebrow="Protobuf Reference",
        summary="Operation-first gRPC pages with package-level browsing and recursive related schema sections.",
        badges=(
            lifecycle_badges(events=overview_events)
            if history_report is not None
            else [ReferenceBadge("Protobuf", tone="protocol"), ReferenceBadge(str(report["latestRelease"]), tone="neutral")]
        ),
        meta_items=[
            ReferenceMetaItem("Source", str(report["sourceName"])),
            ReferenceMetaItem("Version filter", str(report["versionFilter"])),
            ReferenceMetaItem("Latest release", str(report["latestRelease"])),
            ReferenceMetaItem("Packages", str(latest["stats"]["packages"])),
            ReferenceMetaItem("Endpoints", str(latest["stats"]["endpoints"])),
            ReferenceMetaItem("Messages", str(latest["stats"]["messages"])),
        ],
        sections=sections,
        history_events=overview_events,
    )


def build_package_page(
    package_doc: dict[str, Any],
    report: dict[str, Any],
    *,
    output_dir: Path,
    ctx: dict[str, dict[str, Any]],
    endpoint_docs: dict[str, dict[str, Any]],
    history_report: SurfaceHistoryReport | None = None,
    overview_name: str = "index.mdx",
) -> ReferenceCollectionPage:
    lifecycle_map = {entry["id"]: entry for entry in report["endpointLifecycle"]}
    history_items_by_id = history_report.items_by_id() if history_report is not None else {}
    package_history_items = [
        item
        for item in history_items_by_id.values()
        if item.id.startswith(f"{package_doc['package']}.")
    ]
    package_events = (
        aggregate_history_events(
            package_history_items,
            comparison_versions=history_report.comparison_versions,
        )
        if history_report is not None
        else []
    )
    page_path = package_page_path(output_dir, package_doc["package"])
    overview_path = output_dir / overview_name

    service_sections: list[ReferenceSection] = []
    for service_id in package_doc["serviceIds"]:
        service = ctx["services"][service_id]
        service_cards: list[ReferenceCard] = []
        for endpoint_id in sorted(service["endpointIds"]):
            endpoint = endpoint_docs[endpoint_id]
            lifecycle = lifecycle_map[endpoint_id]
            history_item = history_items_by_id.get(endpoint_id)
            operation_path = operation_page_path(output_dir, package_doc["package"], endpoint["service"], endpoint["name"])
            service_cards.append(
                ReferenceCard(
                    title=f"{endpoint['service']}.{endpoint['name']}",
                    href=page_ref(page_path, operation_path),
                    summary=compact_text(endpoint.get("description") or endpoint_signature(endpoint), limit=180),
                    badges=(
                        lifecycle_badges(
                            item=history_item,
                            comparison_versions=history_report.comparison_versions,
                            linked=False,
                        )
                        if history_item is not None
                        else lifecycle_badges(
                            introduced=str(lifecycle["introducedIn"]),
                            changed=str(lifecycle.get("lastChangedIn") or ""),
                            removed=str(lifecycle.get("removedIn") or "") or None,
                        )
                    ),
                    meta_items=[
                        ReferenceMetaItem("Request", endpoint["requestType"]),
                        ReferenceMetaItem("Response", endpoint["responseType"]),
                        ReferenceMetaItem("Client stream", "Yes" if endpoint["clientStreaming"] else "No"),
                        ReferenceMetaItem("Server stream", "Yes" if endpoint["serverStreaming"] else "No"),
                        *lifecycle_meta_items(endpoint),
                    ],
                )
            )
        service_sections.append(
            ReferenceSection(
                heading=service["name"],
                body_markdown=safe_markdown_text(service.get("description") or "") or None,
                meta_items=[
                    ReferenceMetaItem("Source file", service["file"], href=service.get("sourceUrl")),
                    ReferenceMetaItem("Operations", str(len(service_cards))),
                ],
                cards=service_cards,
            )
        )

    file_cards = [
        ReferenceCard(
                title=file_doc["repoPath"],
                summary="Current source file in the latest published descriptor snapshot.",
                meta_items=[
                    ReferenceMetaItem("Services", str(len(file_doc["serviceIds"]))),
                    ReferenceMetaItem("Messages", str(len(file_doc["messageIds"]))),
                    ReferenceMetaItem("Enums", str(len(file_doc["enumIds"]))),
                    ReferenceMetaItem("Source", file_doc["repoPath"], href=file_doc.get("sourceUrl")),
                ],
            )
        for file_id in package_doc["fileIds"]
        for file_doc in [ctx["files"][file_id]]
        if file_id in ctx["files"]
    ]

    related_types = related_schemas_for_types(
        [*package_doc["messageIds"], *package_doc["enumIds"]],
        ctx,
    )

    sections = []
    if file_cards:
        sections.append(ReferenceSection(heading="Source Files", cards=file_cards))
    sections.extend(service_sections)
    if related_types:
        sections.append(
            ReferenceSection(
                heading="Type Inventory",
                body_markdown="These are the package-level message and enum shapes in the publish-version snapshot.",
                schemas=related_types,
            )
        )

    return ReferenceCollectionPage(
        path=page_path.relative_to(output_dir).as_posix(),
        title=package_doc["package"],
        description=f"Package-level overview for {package_doc['package']}.",
        eyebrow="Protobuf Package",
        summary=compact_package_summary(package_doc),
        back_link=page_ref(page_path, overview_path),
        back_label="Back to overview",
        badges=lifecycle_badges(events=package_events),
        meta_items=[
            ReferenceMetaItem("Files", str(package_doc["fileCount"])),
            ReferenceMetaItem("Services", str(package_doc["serviceCount"])),
            ReferenceMetaItem("Endpoints", str(package_doc["endpointCount"])),
            ReferenceMetaItem("Messages", str(package_doc["messageCount"])),
            ReferenceMetaItem("Enums", str(package_doc["enumCount"])),
        ],
        sections=sections,
        history_events=package_events,
    )


def build_operation_page(
    package_name: str,
    endpoint: dict[str, Any],
    lifecycle: dict[str, Any],
    *,
    output_dir: Path,
    ctx: dict[str, dict[str, Any]],
    history_item: HistoryItem | None = None,
    comparison_versions: tuple[str, ...] = (),
    overview_name: str = "index.mdx",
) -> ReferenceOperationPage:
    page_path = operation_page_path(output_dir, package_name, endpoint["service"], endpoint["name"])
    package_path = package_page_path(output_dir, package_name)
    related_schemas = related_schemas_for_types([endpoint["requestType"], endpoint["responseType"]], ctx)
    schema_map = {schema.name: schema for schema in related_schemas}

    request_schema = schema_map.get(endpoint["requestType"])
    response_schema = schema_map.get(endpoint["responseType"])
    request_body = message_json_sample(endpoint["requestType"], ctx)
    response_body = message_json_sample(endpoint["responseType"], ctx)

    return ReferenceOperationPage(
        path=page_path.relative_to(output_dir).as_posix(),
        title=endpoint["name"],
        description=None,
        eyebrow=package_name,
        summary=None,
        back_link=page_ref(page_path, package_path),
        back_label="Back to package",
        breadcrumbs=[
            ReferenceBreadcrumb(package_group(package_name, has_services=True)),
            ReferenceBreadcrumb("Protobuf", page_ref(page_path, output_dir / overview_name)),
            ReferenceBreadcrumb(package_name, page_ref(page_path, package_path)),
            ReferenceBreadcrumb(endpoint["name"]),
        ],
        badges=(
            lifecycle_badges(
                item=history_item,
                comparison_versions=comparison_versions,
            )
            if history_item is not None
            else lifecycle_badges(
                introduced=str(lifecycle["introducedIn"]),
                changed=str(lifecycle.get("lastChangedIn") or ""),
                removed=str(lifecycle.get("removedIn") or "") or None,
            )
        ),
        meta_items=[
            ReferenceMetaItem("Package", package_name),
            ReferenceMetaItem("Service", endpoint["service"]),
            ReferenceMetaItem("Introduced", str(lifecycle["introducedIn"])),
            ReferenceMetaItem("Removed", str(lifecycle.get("removedIn") or "-")),
            ReferenceMetaItem("Source", endpoint["file"], href=endpoint.get("sourceUrl")),
        ],
        operation_method="RPC",
        operation_target=f"/{package_name}.{endpoint['service']}/{endpoint['name']}",
        overview_markdown=None,
        protocol_items=[
            ReferenceMetaItem("Protocol", "gRPC"),
            ReferenceMetaItem("Service", endpoint["service"]),
            ReferenceMetaItem("RPC", endpoint["name"]),
            ReferenceMetaItem("Client stream", "Yes" if endpoint["clientStreaming"] else "No"),
            ReferenceMetaItem("Server stream", "Yes" if endpoint["serverStreaming"] else "No"),
            *lifecycle_meta_items(endpoint),
        ],
        inputs=[
            ReferencePanel(
                title=short_type_name(endpoint["requestType"]),
                meta_items=[
                    ReferenceMetaItem("Message", endpoint["requestType"]),
                    ReferenceMetaItem("Client stream", "Yes" if endpoint["clientStreaming"] else "No"),
                    *lifecycle_meta_items(ctx["messages"][endpoint["requestType"]]),
                ],
                schema=request_schema,
            )
        ],
        outputs=[
            ReferencePanel(
                title=short_type_name(endpoint["responseType"]),
                meta_items=[
                    ReferenceMetaItem("Message", endpoint["responseType"]),
                    ReferenceMetaItem("Server stream", "Yes" if endpoint["serverStreaming"] else "No"),
                    *lifecycle_meta_items(ctx["messages"][endpoint["responseType"]]),
                ],
                schema=response_schema,
            )
        ],
        examples=[
            ReferenceExample(
                title="grpcurl",
                body=grpcurl_example(package_name, endpoint, request_body),
                language="bash",
            ),
            ReferenceExample(
                title="OK",
                body=json.dumps(response_body, indent=2, ensure_ascii=False),
                kind="response",
                media_type="application/json",
            ),
        ],
        lifecycle_changes=[] if history_item is not None else [
            ReferenceChange(
                version=str(event["version"]),
                details=", ".join(str(change) for change in event.get("changeTypes", [])) or str(event["kind"]),
            )
            for event in lifecycle.get("history", [])
        ],
        related_schemas=related_schemas,
        history_events=(
            list(
                history_events_for_item(
                    history_item,
                    comparison_versions=comparison_versions,
                )
            )
            if history_item is not None
            else []
        ),
    )


def build_pages(
    report: dict[str, Any],
    *,
    output_dir: Path,
    history_report: SurfaceHistoryReport | None = None,
    overview_name: str = "index.mdx",
) -> tuple[Path, list[Any]]:
    latest = report["latestSnapshot"]
    ctx = {
        "files": latest["files"],
        "services": latest["services"],
        "endpoints": latest["endpoints"],
        "messages": latest["messages"],
        "fields": latest["fields"],
        "enums": latest["enums"],
        "enumValues": latest["enumValues"],
    }
    package_docs = build_package_docs(report)
    endpoint_docs = endpoint_snapshot_map(report)
    lifecycle_map = {entry["id"]: entry for entry in report["endpointLifecycle"]}

    history_items_by_id = history_report.items_by_id() if history_report is not None else {}
    pages = [
        render_collection_page(
            build_overview_page(
                report,
                output_dir=output_dir,
                package_docs=package_docs,
                history_report=history_report,
                overview_name=overview_name,
            )
        )
    ]
    for package_doc in package_docs:
        pages.append(
            render_collection_page(
                build_package_page(
                    package_doc,
                    report,
                    output_dir=output_dir,
                    ctx=ctx,
                    endpoint_docs=endpoint_docs,
                    history_report=history_report,
                    overview_name=overview_name,
                )
            )
        )
        for endpoint_id in package_doc["endpointIds"]:
            pages.append(
                render_operation_page(
                    build_operation_page(
                        package_doc["package"],
                        endpoint_docs[endpoint_id],
                        lifecycle_map[endpoint_id],
                        output_dir=output_dir,
                        ctx=ctx,
                        history_item=history_items_by_id.get(endpoint_id),
                        comparison_versions=(
                            history_report.comparison_versions
                            if history_report is not None
                            else ()
                        ),
                        overview_name=overview_name,
                    )
                )
            )
    return output_dir, pages
