"""Build descriptor-backed protobuf history reports from local snapshot manifests."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from x2mdx.protobuf.models import ProtobufSourceSnapshot, ProtobufSources

try:
    from google.protobuf import descriptor_pb2
except ImportError as exc:  # pragma: no cover - handled at runtime
    IMPORT_ERROR: Exception | None = exc
    descriptor_pb2 = None  # type: ignore[assignment]
else:
    IMPORT_ERROR = None

STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
FILE_MESSAGE_FIELD_NUMBER = 4
FILE_ENUM_FIELD_NUMBER = 5
FILE_SERVICE_FIELD_NUMBER = 6
MESSAGE_FIELD_FIELD_NUMBER = 2
MESSAGE_NESTED_FIELD_NUMBER = 3
MESSAGE_ENUM_FIELD_NUMBER = 4
MESSAGE_ONEOF_FIELD_NUMBER = 8
ENUM_VALUE_FIELD_NUMBER = 2
SERVICE_METHOD_FIELD_NUMBER = 2

SCALAR_TYPE_NAMES: dict[int, str] = {}
LABEL_NAMES: dict[int, str] = {}


def ensure_runtime_dependencies() -> None:
    if IMPORT_ERROR is not None:
        raise RuntimeError("Missing protobuf runtime dependency: install the `protobuf` package") from IMPORT_ERROR


def ensure_descriptor_constants() -> None:
    ensure_runtime_dependencies()
    if SCALAR_TYPE_NAMES:
        return
    for enum_value in descriptor_pb2.FieldDescriptorProto.Type.values():
        name = descriptor_pb2.FieldDescriptorProto.Type.Name(enum_value)
        if name.startswith("TYPE_"):
            SCALAR_TYPE_NAMES[enum_value] = name.removeprefix("TYPE_").lower()
    for enum_value in descriptor_pb2.FieldDescriptorProto.Label.values():
        name = descriptor_pb2.FieldDescriptorProto.Label.Name(enum_value)
        if name.startswith("LABEL_"):
            LABEL_NAMES[enum_value] = name.removeprefix("LABEL_").lower()


def version_sort_key(version: str) -> tuple[Any, ...]:
    if m := STABLE_VERSION_RE.fullmatch(version):
        major, minor, patch = m.groups()
        return (0, int(major), int(minor), int(patch))
    return (1, version)


def to_release_line(version: str) -> str:
    if m := STABLE_VERSION_RE.fullmatch(version):
        return f"{m.group(1)}.{m.group(2)}"
    return version


def normalize_comment(raw: str) -> str:
    lines = [line.rstrip() for line in raw.strip("\n").splitlines()]
    return "\n".join(lines).strip()


def strip_leading_dot(value: str) -> str:
    return value[1:] if value.startswith(".") else value


def join_full_name(package: str, parts: list[str]) -> str:
    return ".".join([part for part in [package, *parts] if part])


def source_url(repo_web_url: str | None, tag: str, repo_path: str, line: int | None) -> str | None:
    if not repo_web_url:
        return None
    base = f"{repo_web_url}/blob/{tag}/{repo_path}"
    return f"{base}#L{line}" if line is not None else base


def build_location_map(file_proto: Any) -> dict[tuple[int, ...], Any]:
    return {tuple(location.path): location for location in file_proto.source_code_info.location}


def location_comment(location: Any | None) -> str:
    if location is None:
        return ""
    parts: list[str] = []
    for detached in location.leading_detached_comments:
        normalized = normalize_comment(detached)
        if normalized:
            parts.append(normalized)
    if location.leading_comments:
        normalized = normalize_comment(location.leading_comments)
        if normalized:
            parts.append(normalized)
    elif location.trailing_comments:
        normalized = normalize_comment(location.trailing_comments)
        if normalized:
            parts.append(normalized)
    return "\n\n".join(parts).strip()


def location_line(location: Any | None) -> int | None:
    if location is None or not location.span:
        return None
    return location.span[0] + 1


def comment_and_line(location_map: dict[tuple[int, ...], Any], path: tuple[int, ...]) -> tuple[str, int | None]:
    location = location_map.get(path)
    return location_comment(location), location_line(location)


def metadata_for(overlay: dict[str, Any], kind: str, entity_id: str) -> dict[str, Any]:
    value = overlay.get(kind, {}).get(entity_id, {})
    return value if isinstance(value, dict) else {}


def load_descriptor_set_from_image(image_path: str) -> Any:
    ensure_runtime_dependencies()
    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.ParseFromString(gzip.decompress(Path(image_path).read_bytes()))
    return descriptor_set


def collect_type_indexes(descriptor_set: Any) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    message_index: dict[str, dict[str, Any]] = {}
    enum_index: dict[str, dict[str, Any]] = {}

    def walk_enum(file_proto: Any, package: str, enum_proto: Any, path: tuple[int, ...], parents: list[str]) -> None:
        full_name = join_full_name(package, parents + [enum_proto.name])
        enum_index[full_name] = {
            "descriptor": enum_proto,
            "file": file_proto,
            "path": path,
        }

    def walk_message(
        file_proto: Any,
        package: str,
        message_proto: Any,
        path: tuple[int, ...],
        parents: list[str],
    ) -> None:
        full_name = join_full_name(package, parents + [message_proto.name])
        message_index[full_name] = {
            "descriptor": message_proto,
            "file": file_proto,
            "path": path,
            "mapEntry": bool(message_proto.options.map_entry),
        }
        child_parents = parents + [message_proto.name]
        for idx, nested in enumerate(message_proto.nested_type):
            walk_message(file_proto, package, nested, path + (MESSAGE_NESTED_FIELD_NUMBER, idx), child_parents)
        for idx, enum_proto in enumerate(message_proto.enum_type):
            walk_enum(file_proto, package, enum_proto, path + (MESSAGE_ENUM_FIELD_NUMBER, idx), child_parents)

    for file_proto in descriptor_set.file:
        package = file_proto.package
        for idx, message_proto in enumerate(file_proto.message_type):
            walk_message(file_proto, package, message_proto, (FILE_MESSAGE_FIELD_NUMBER, idx), [])
        for idx, enum_proto in enumerate(file_proto.enum_type):
            walk_enum(file_proto, package, enum_proto, (FILE_ENUM_FIELD_NUMBER, idx), [])
    return message_index, enum_index


class DescriptorSnapshotBuilder:
    def __init__(
        self,
        *,
        source: ProtobufSourceSnapshot,
        repo_web_url: str | None,
        descriptor_set: Any,
        metadata_overlay: dict[str, Any],
    ) -> None:
        ensure_descriptor_constants()
        self.source = source
        self.repo_web_url = repo_web_url
        self.descriptor_set = descriptor_set
        self.import_to_repo_path = source.import_to_repo_path
        self.owned_import_paths = set(self.import_to_repo_path)
        self.metadata_overlay = metadata_overlay
        self.location_maps = {file_proto.name: build_location_map(file_proto) for file_proto in descriptor_set.file}
        self.message_index, self.enum_index = collect_type_indexes(descriptor_set)
        self.files: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self.endpoints: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, dict[str, Any]] = {}
        self.fields: dict[str, dict[str, Any]] = {}
        self.enums: dict[str, dict[str, Any]] = {}
        self.enum_values: dict[str, dict[str, Any]] = {}

    def metadata(self, kind: str, entity_id: str) -> dict[str, Any]:
        return metadata_for(self.metadata_overlay, kind, entity_id)

    def repo_path(self, import_path: str) -> str:
        return self.import_to_repo_path[import_path]

    def file_source_url(self, import_path: str, line: int | None) -> str | None:
        return source_url(self.repo_web_url, self.source.tag, self.repo_path(import_path), line)

    def file_locmap(self, import_path: str) -> dict[tuple[int, ...], Any]:
        return self.location_maps[import_path]

    def message_full_name(self, file_proto: Any, parent_message_id: str | None, name: str) -> str:
        if parent_message_id:
            return f"{parent_message_id}.{name}"
        return join_full_name(file_proto.package, [name])

    def enum_full_name(self, file_proto: Any, parent_message_id: str | None, name: str) -> str:
        if parent_message_id:
            return f"{parent_message_id}.{name}"
        return join_full_name(file_proto.package, [name])

    def real_oneof_indexes(self, message_proto: Any) -> set[int]:
        field_indexes_by_oneof: dict[int, list[int]] = defaultdict(list)
        for idx, field in enumerate(message_proto.field):
            if field.HasField("oneof_index"):
                field_indexes_by_oneof[field.oneof_index].append(idx)
        real_indexes: set[int] = set()
        for oneof_idx, _oneof in enumerate(message_proto.oneof_decl):
            field_indexes = field_indexes_by_oneof.get(oneof_idx, [])
            if len(field_indexes) == 1 and message_proto.field[field_indexes[0]].proto3_optional:
                continue
            real_indexes.add(oneof_idx)
        return real_indexes

    def resolve_type_ref(self, field_proto: Any) -> dict[str, Any]:
        type_name = strip_leading_dot(field_proto.type_name)
        if field_proto.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE:
            entry = self.message_index.get(type_name)
            if entry and entry["mapEntry"]:
                map_descriptor = entry["descriptor"]
                key_info = self.resolve_type_ref(map_descriptor.field[0])
                value_info = self.resolve_type_ref(map_descriptor.field[1])
                return {
                    "kind": "map",
                    "displayType": f"map<{key_info['displayType']}, {value_info['displayType']}>",
                    "fullName": type_name,
                    "keyType": key_info["displayType"],
                    "valueType": value_info["displayType"],
                }
            return {
                "kind": "message",
                "displayType": type_name,
                "fullName": type_name,
            }
        if field_proto.type == descriptor_pb2.FieldDescriptorProto.TYPE_ENUM:
            return {
                "kind": "enum",
                "displayType": type_name,
                "fullName": type_name,
            }
        return {
            "kind": "scalar",
            "displayType": SCALAR_TYPE_NAMES.get(field_proto.type, str(field_proto.type).lower()),
            "fullName": None,
        }

    def build_field_shape(self, field_doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "number": field_doc["number"],
            "name": field_doc["name"],
            "jsonName": field_doc["jsonName"],
            "label": field_doc["label"],
            "type": field_doc["type"],
            "typeKind": field_doc["typeKind"],
            "typeName": field_doc["typeName"],
            "oneof": field_doc["oneof"],
            "map": field_doc["map"],
            "keyType": field_doc["keyType"],
            "valueType": field_doc["valueType"],
            "proto3Optional": field_doc["proto3Optional"],
            "defaultValue": field_doc["defaultValue"],
        }

    def build_enum_value_shape(self, enum_value_doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": enum_value_doc["name"],
            "number": enum_value_doc["number"],
        }

    def build_field(
        self,
        *,
        file_proto: Any,
        message_proto: Any,
        message_full_name: str,
        message_path: tuple[int, ...],
        field_idx: int,
        real_oneof_indexes: set[int],
    ) -> dict[str, Any]:
        locmap = self.file_locmap(file_proto.name)
        path = message_path + (MESSAGE_FIELD_FIELD_NUMBER, field_idx)
        description, line = comment_and_line(locmap, path)
        field_proto = message_proto.field[field_idx]
        field_id = f"{message_full_name}#{field_proto.name}"
        resolved = self.resolve_type_ref(field_proto)
        oneof_name = None
        if field_proto.HasField("oneof_index") and field_proto.oneof_index in real_oneof_indexes:
            oneof_name = message_proto.oneof_decl[field_proto.oneof_index].name

        field_doc = {
            "id": field_id,
            "name": field_proto.name,
            "package": file_proto.package,
            "messageId": message_full_name,
            "file": self.repo_path(file_proto.name),
            "importPath": file_proto.name,
            "number": field_proto.number,
            "label": LABEL_NAMES.get(field_proto.label, str(field_proto.label).lower()),
            "jsonName": field_proto.json_name,
            "type": resolved["displayType"],
            "typeKind": resolved["kind"],
            "typeName": resolved["fullName"],
            "map": resolved["kind"] == "map",
            "keyType": resolved.get("keyType"),
            "valueType": resolved.get("valueType"),
            "oneof": oneof_name,
            "proto3Optional": bool(field_proto.proto3_optional),
            "defaultValue": field_proto.default_value or None,
            "description": description,
            "line": line,
            "sourceUrl": self.file_source_url(file_proto.name, line),
            "metadata": self.metadata("fields", field_id),
        }
        self.fields[field_id] = field_doc
        return field_doc

    def build_enum_value(
        self,
        *,
        file_proto: Any,
        enum_full_name: str,
        enum_path: tuple[int, ...],
        value_idx: int,
        value_proto: Any,
    ) -> dict[str, Any]:
        locmap = self.file_locmap(file_proto.name)
        description, line = comment_and_line(locmap, enum_path + (ENUM_VALUE_FIELD_NUMBER, value_idx))
        value_id = f"{enum_full_name}#{value_proto.name}"
        value_doc = {
            "id": value_id,
            "name": value_proto.name,
            "package": file_proto.package,
            "enumId": enum_full_name,
            "file": self.repo_path(file_proto.name),
            "number": value_proto.number,
            "description": description,
            "line": line,
            "sourceUrl": self.file_source_url(file_proto.name, line),
            "metadata": self.metadata("enumValues", value_id),
        }
        self.enum_values[value_id] = value_doc
        return value_doc

    def build_enum(
        self,
        *,
        file_proto: Any,
        enum_proto: Any,
        enum_path: tuple[int, ...],
        parent_message_id: str | None,
    ) -> dict[str, Any]:
        locmap = self.file_locmap(file_proto.name)
        description, line = comment_and_line(locmap, enum_path)
        enum_full_name = self.enum_full_name(file_proto, parent_message_id, enum_proto.name)
        values = [
            self.build_enum_value(
                file_proto=file_proto,
                enum_full_name=enum_full_name,
                enum_path=enum_path,
                value_idx=value_idx,
                value_proto=value_proto,
            )
            for value_idx, value_proto in enumerate(enum_proto.value)
        ]
        enum_doc = {
            "id": enum_full_name,
            "name": enum_proto.name,
            "package": file_proto.package,
            "file": self.repo_path(file_proto.name),
            "importPath": file_proto.name,
            "parentMessageId": parent_message_id,
            "description": description,
            "line": line,
            "sourceUrl": self.file_source_url(file_proto.name, line),
            "metadata": self.metadata("enums", enum_full_name),
            "valueIds": [value["id"] for value in values],
            "valueShape": [self.build_enum_value_shape(value) for value in values],
        }
        self.enums[enum_full_name] = enum_doc
        return enum_doc

    def build_message(
        self,
        *,
        file_proto: Any,
        message_proto: Any,
        message_path: tuple[int, ...],
        parent_message_id: str | None,
    ) -> dict[str, Any]:
        locmap = self.file_locmap(file_proto.name)
        description, line = comment_and_line(locmap, message_path)
        message_full_name = self.message_full_name(file_proto, parent_message_id, message_proto.name)
        real_oneof_indexes = self.real_oneof_indexes(message_proto)
        fields = [
            self.build_field(
                file_proto=file_proto,
                message_proto=message_proto,
                message_full_name=message_full_name,
                message_path=message_path,
                field_idx=field_idx,
                real_oneof_indexes=real_oneof_indexes,
            )
            for field_idx, _field_proto in enumerate(message_proto.field)
        ]
        oneofs: list[dict[str, Any]] = []
        for oneof_idx, oneof_proto in enumerate(message_proto.oneof_decl):
            if oneof_idx not in real_oneof_indexes:
                continue
            oneof_description, oneof_line = comment_and_line(locmap, message_path + (MESSAGE_ONEOF_FIELD_NUMBER, oneof_idx))
            oneofs.append(
                {
                    "name": oneof_proto.name,
                    "description": oneof_description,
                    "line": oneof_line,
                    "fieldIds": [field["id"] for field in fields if field["oneof"] == oneof_proto.name],
                }
            )

        nested_message_ids: list[str] = []
        for nested_idx, nested_proto in enumerate(message_proto.nested_type):
            if nested_proto.options.map_entry:
                continue
            nested_doc = self.build_message(
                file_proto=file_proto,
                message_proto=nested_proto,
                message_path=message_path + (MESSAGE_NESTED_FIELD_NUMBER, nested_idx),
                parent_message_id=message_full_name,
            )
            nested_message_ids.append(nested_doc["id"])

        enum_ids: list[str] = []
        for enum_idx, enum_proto in enumerate(message_proto.enum_type):
            enum_doc = self.build_enum(
                file_proto=file_proto,
                enum_proto=enum_proto,
                enum_path=message_path + (MESSAGE_ENUM_FIELD_NUMBER, enum_idx),
                parent_message_id=message_full_name,
            )
            enum_ids.append(enum_doc["id"])

        message_doc = {
            "id": message_full_name,
            "name": message_proto.name,
            "package": file_proto.package,
            "file": self.repo_path(file_proto.name),
            "importPath": file_proto.name,
            "parentMessageId": parent_message_id,
            "description": description,
            "line": line,
            "sourceUrl": self.file_source_url(file_proto.name, line),
            "metadata": self.metadata("messages", message_full_name),
            "fieldIds": [field["id"] for field in fields],
            "fieldShape": [self.build_field_shape(field) for field in fields],
            "oneofs": oneofs,
            "nestedMessageIds": nested_message_ids,
            "enumIds": enum_ids,
        }
        self.messages[message_full_name] = message_doc
        return message_doc

    def build_method(self, *, file_proto: Any, service_doc: dict[str, Any], service_idx: int, method_idx: int, method_proto: Any) -> dict[str, Any]:
        locmap = self.file_locmap(file_proto.name)
        description, line = comment_and_line(locmap, (FILE_SERVICE_FIELD_NUMBER, service_idx, SERVICE_METHOD_FIELD_NUMBER, method_idx))
        endpoint_id = f"{service_doc['id']}/{method_proto.name}"
        metadata_id = f"{service_doc['id']}.{method_proto.name}"
        endpoint_doc = {
            "id": endpoint_id,
            "name": method_proto.name,
            "package": file_proto.package,
            "service": service_doc["name"],
            "serviceFullName": service_doc["id"],
            "file": self.repo_path(file_proto.name),
            "importPath": file_proto.name,
            "description": description,
            "line": line,
            "sourceUrl": self.file_source_url(file_proto.name, line),
            "metadata": self.metadata("endpoints", metadata_id),
            "requestType": strip_leading_dot(method_proto.input_type),
            "responseType": strip_leading_dot(method_proto.output_type),
            "clientStreaming": bool(method_proto.client_streaming),
            "serverStreaming": bool(method_proto.server_streaming),
        }
        self.endpoints[endpoint_id] = endpoint_doc
        return endpoint_doc

    def build_service(self, *, file_proto: Any, service_idx: int, service_proto: Any) -> dict[str, Any]:
        locmap = self.file_locmap(file_proto.name)
        description, line = comment_and_line(locmap, (FILE_SERVICE_FIELD_NUMBER, service_idx))
        service_full_name = join_full_name(file_proto.package, [service_proto.name])
        service_doc = {
            "id": service_full_name,
            "name": service_proto.name,
            "package": file_proto.package,
            "file": self.repo_path(file_proto.name),
            "importPath": file_proto.name,
            "description": description,
            "line": line,
            "sourceUrl": self.file_source_url(file_proto.name, line),
            "metadata": self.metadata("services", service_full_name),
            "endpointIds": [],
        }
        self.services[service_full_name] = service_doc
        service_doc["endpointIds"] = [
            self.build_method(
                file_proto=file_proto,
                service_doc=service_doc,
                service_idx=service_idx,
                method_idx=method_idx,
                method_proto=method_proto,
            )["id"]
            for method_idx, method_proto in enumerate(service_proto.method)
        ]
        return service_doc

    def build_packages(self) -> list[dict[str, Any]]:
        package_map: dict[str, dict[str, Any]] = {}
        for collection_name, source in (
            ("fileIds", self.files),
            ("serviceIds", self.services),
            ("endpointIds", self.endpoints),
            ("messageIds", self.messages),
            ("fieldIds", self.fields),
            ("enumIds", self.enums),
            ("enumValueIds", self.enum_values),
        ):
            for entity in source.values():
                package = entity["package"] or "(no package)"
                bucket = package_map.setdefault(
                    package,
                    {"package": package, "fileIds": [], "serviceIds": [], "endpointIds": [], "messageIds": [], "fieldIds": [], "enumIds": [], "enumValueIds": []},
                )
                bucket[collection_name].append(entity["id"])

        packages: list[dict[str, Any]] = []
        for package_name in sorted(package_map):
            bucket = package_map[package_name]
            for key, value in bucket.items():
                if key.endswith("Ids"):
                    bucket[key] = sorted(value)
            packages.append(
                {
                    "package": package_name,
                    "fileIds": bucket["fileIds"],
                    "fileCount": len(bucket["fileIds"]),
                    "serviceIds": bucket["serviceIds"],
                    "serviceCount": len(bucket["serviceIds"]),
                    "endpointIds": bucket["endpointIds"],
                    "endpointCount": len(bucket["endpointIds"]),
                    "messageIds": bucket["messageIds"],
                    "messageCount": len(bucket["messageIds"]),
                    "enumIds": bucket["enumIds"],
                    "enumCount": len(bucket["enumIds"]),
                }
            )
        return packages

    def build(self) -> dict[str, Any]:
        for file_proto in self.descriptor_set.file:
            if file_proto.name not in self.owned_import_paths:
                continue
            locmap = self.file_locmap(file_proto.name)
            description, line = comment_and_line(locmap, ())
            repo_path = self.repo_path(file_proto.name)
            file_doc = {
                "id": repo_path,
                "importPath": file_proto.name,
                "repoPath": repo_path,
                "package": file_proto.package,
                "syntax": file_proto.syntax or "proto2",
                "description": description,
                "line": line,
                "sourceUrl": self.file_source_url(file_proto.name, line),
                "metadata": self.metadata("files", repo_path),
                "dependencies": list(file_proto.dependency),
                "serviceIds": [],
                "messageIds": [],
                "enumIds": [],
                "hash": hashlib.sha256(file_proto.SerializeToString(deterministic=True)).hexdigest(),
            }

            for idx, service_proto in enumerate(file_proto.service):
                service_doc = self.build_service(file_proto=file_proto, service_idx=idx, service_proto=service_proto)
                file_doc["serviceIds"].append(service_doc["id"])
            for idx, message_proto in enumerate(file_proto.message_type):
                if message_proto.options.map_entry:
                    continue
                message_doc = self.build_message(
                    file_proto=file_proto,
                    message_proto=message_proto,
                    message_path=(FILE_MESSAGE_FIELD_NUMBER, idx),
                    parent_message_id=None,
                )
                file_doc["messageIds"].append(message_doc["id"])
            for idx, enum_proto in enumerate(file_proto.enum_type):
                enum_doc = self.build_enum(
                    file_proto=file_proto,
                    enum_proto=enum_proto,
                    enum_path=(FILE_ENUM_FIELD_NUMBER, idx),
                    parent_message_id=None,
                )
                file_doc["enumIds"].append(enum_doc["id"])
            self.files[repo_path] = file_doc

        packages = self.build_packages()
        return {
            "tag": self.source.tag,
            "version": self.source.version,
            "date": self.source.date,
            "packages": packages,
            "files": self.files,
            "services": self.services,
            "endpoints": self.endpoints,
            "messages": self.messages,
            "fields": self.fields,
            "enums": self.enums,
            "enumValues": self.enum_values,
            "stats": {
                "protoFiles": len(self.files),
                "packages": len(packages),
                "services": len(self.services),
                "endpoints": len(self.endpoints),
                "messages": len(self.messages),
                "fields": len(self.fields),
                "enums": len(self.enums),
                "enumValues": len(self.enum_values),
            },
        }


def entity_signature(entity: dict[str, Any], *, keys: tuple[str, ...]) -> str:
    return json.dumps({key: entity.get(key) for key in keys}, sort_keys=True, separators=(",", ":"))


def endpoint_signature(entity: dict[str, Any]) -> str:
    return entity_signature(entity, keys=("file", "requestType", "responseType", "clientStreaming", "serverStreaming"))


def service_signature(entity: dict[str, Any]) -> str:
    return entity_signature(entity, keys=("file", "endpointIds"))


def message_signature(entity: dict[str, Any]) -> str:
    return entity_signature(entity, keys=("file", "fieldShape", "oneofs"))


def enum_signature(entity: dict[str, Any]) -> str:
    return entity_signature(entity, keys=("file", "valueShape"))


def diff_file_maps(previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]) -> dict[str, list[Any]]:
    previous_ids = set(previous)
    current_ids = set(current)
    modified = sorted(key for key in previous_ids & current_ids if previous[key]["hash"] != current[key]["hash"])
    return {
        "added": sorted(current_ids - previous_ids),
        "removed": sorted(previous_ids - current_ids),
        "modified": modified,
    }


def diff_keyed_entities(
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    *,
    signature_fn,
    change_type_fn,
) -> dict[str, list[Any]]:
    previous_ids = set(previous)
    current_ids = set(current)
    added = [current[key] for key in sorted(current_ids - previous_ids)]
    removed = [previous[key] for key in sorted(previous_ids - current_ids)]
    modified: list[dict[str, Any]] = []
    for key in sorted(previous_ids & current_ids):
        prev_entity = previous[key]
        cur_entity = current[key]
        if signature_fn(prev_entity) == signature_fn(cur_entity):
            continue
        modified.append(
            {
                "id": key,
                "previous": prev_entity,
                "current": cur_entity,
                "changeTypes": change_type_fn(prev_entity, cur_entity),
            }
        )
    return {"added": added, "removed": removed, "modified": modified}


def endpoint_change_types(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if previous["requestType"] != current["requestType"]:
        out.append("request")
    if previous["responseType"] != current["responseType"]:
        out.append("response")
    if previous["clientStreaming"] != current["clientStreaming"] or previous["serverStreaming"] != current["serverStreaming"]:
        out.append("streaming")
    if previous["file"] != current["file"]:
        out.append("file")
    return out or ["signature"]


def service_change_types(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if previous["endpointIds"] != current["endpointIds"]:
        return ["endpoints"]
    return ["service"]


def message_change_types(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if previous["fieldShape"] != current["fieldShape"]:
        out.append("fields")
    if previous["oneofs"] != current["oneofs"]:
        out.append("oneofs")
    return out or ["message"]


def enum_change_types(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if previous["valueShape"] != current["valueShape"]:
        return ["values"]
    return ["enum"]


def build_release_diffs(releases: list[dict[str, Any]]) -> None:
    previous_release: dict[str, Any] | None = None
    for release in releases:
        if previous_release is None:
            release["previousTag"] = None
            release["changes"] = {
                "files": diff_file_maps({}, release["snapshot"]["files"]),
                "services": diff_keyed_entities({}, release["snapshot"]["services"], signature_fn=service_signature, change_type_fn=service_change_types),
                "endpoints": diff_keyed_entities({}, release["snapshot"]["endpoints"], signature_fn=endpoint_signature, change_type_fn=endpoint_change_types),
                "messages": diff_keyed_entities({}, release["snapshot"]["messages"], signature_fn=message_signature, change_type_fn=message_change_types),
                "enums": diff_keyed_entities({}, release["snapshot"]["enums"], signature_fn=enum_signature, change_type_fn=enum_change_types),
            }
        else:
            release["previousTag"] = previous_release["tag"]
            release["changes"] = {
                "files": diff_file_maps(previous_release["snapshot"]["files"], release["snapshot"]["files"]),
                "services": diff_keyed_entities(previous_release["snapshot"]["services"], release["snapshot"]["services"], signature_fn=service_signature, change_type_fn=service_change_types),
                "endpoints": diff_keyed_entities(previous_release["snapshot"]["endpoints"], release["snapshot"]["endpoints"], signature_fn=endpoint_signature, change_type_fn=endpoint_change_types),
                "messages": diff_keyed_entities(previous_release["snapshot"]["messages"], release["snapshot"]["messages"], signature_fn=message_signature, change_type_fn=message_change_types),
                "enums": diff_keyed_entities(previous_release["snapshot"]["enums"], release["snapshot"]["enums"], signature_fn=enum_signature, change_type_fn=enum_change_types),
            }
        release["changes"]["counts"] = {
            "files": {kind: len(release["changes"]["files"][kind]) for kind in ("added", "removed", "modified")},
            "services": {kind: len(release["changes"]["services"][kind]) for kind in ("added", "removed", "modified")},
            "endpoints": {kind: len(release["changes"]["endpoints"][kind]) for kind in ("added", "removed", "modified")},
            "messages": {kind: len(release["changes"]["messages"][kind]) for kind in ("added", "removed", "modified")},
            "enums": {kind: len(release["changes"]["enums"][kind]) for kind in ("added", "removed", "modified")},
        }
        previous_release = release


def build_endpoint_lifecycle(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lifecycle: dict[str, dict[str, Any]] = {}
    for release in releases:
        version = release["version"]
        changes = release["changes"]["endpoints"]
        for endpoint in changes["added"]:
            lifecycle[endpoint["id"]] = {
                "id": endpoint["id"],
                "package": endpoint["package"],
                "service": endpoint["service"],
                "serviceFullName": endpoint["serviceFullName"],
                "name": endpoint["name"],
                "introducedIn": version,
                "lastChangedIn": version,
                "removedIn": None,
                "current": True,
                "sourceUrl": endpoint["sourceUrl"],
                "history": [{"version": version, "kind": "introduced"}],
            }
        for change in changes["modified"]:
            entry = lifecycle.setdefault(
                change["id"],
                {
                    "id": change["id"],
                    "package": change["current"]["package"],
                    "service": change["current"]["service"],
                    "serviceFullName": change["current"]["serviceFullName"],
                    "name": change["current"]["name"],
                    "introducedIn": version,
                    "lastChangedIn": version,
                    "removedIn": None,
                    "current": True,
                    "sourceUrl": change["current"]["sourceUrl"],
                    "history": [],
                },
            )
            entry["lastChangedIn"] = version
            entry["current"] = True
            entry["sourceUrl"] = change["current"]["sourceUrl"]
            entry["history"].append({"version": version, "kind": "modified", "changeTypes": change["changeTypes"]})
        for endpoint in changes["removed"]:
            entry = lifecycle.setdefault(
                endpoint["id"],
                {
                    "id": endpoint["id"],
                    "package": endpoint["package"],
                    "service": endpoint["service"],
                    "serviceFullName": endpoint["serviceFullName"],
                    "name": endpoint["name"],
                    "introducedIn": version,
                    "lastChangedIn": version,
                    "removedIn": version,
                    "current": False,
                    "sourceUrl": endpoint["sourceUrl"],
                    "history": [],
                },
            )
            entry["removedIn"] = version
            entry["current"] = False
            entry["history"].append({"version": version, "kind": "removed"})

    latest_endpoints = releases[-1]["snapshot"]["endpoints"]
    for endpoint_id, endpoint in latest_endpoints.items():
        entry = lifecycle.setdefault(
            endpoint_id,
            {
                "id": endpoint_id,
                "package": endpoint["package"],
                "service": endpoint["service"],
                "serviceFullName": endpoint["serviceFullName"],
                "name": endpoint["name"],
                "introducedIn": releases[-1]["version"],
                "lastChangedIn": releases[-1]["version"],
                "removedIn": None,
                "current": True,
                "sourceUrl": endpoint["sourceUrl"],
                "history": [],
            },
        )
        entry["current"] = entry["removedIn"] is None
        entry["sourceUrl"] = endpoint["sourceUrl"]
    return [lifecycle[key] for key in sorted(lifecycle)]


def build_protobuf_history_report_from_sources(
    sources: ProtobufSources,
    *,
    source_name: str,
    version_filter: str,
) -> dict[str, Any]:
    ordered_sources = sorted(sources.snapshots, key=lambda snapshot: version_sort_key(snapshot.version))
    releases: list[dict[str, Any]] = []
    metadata_overlay = sources.metadata_overlay or {}
    for snapshot in ordered_sources:
        descriptor_set = load_descriptor_set_from_image(snapshot.descriptor_image_path)
        builder = DescriptorSnapshotBuilder(
            source=snapshot,
            repo_web_url=sources.repo_web_url,
            descriptor_set=descriptor_set,
            metadata_overlay=metadata_overlay,
        )
        release = {
            "tag": snapshot.tag,
            "version": snapshot.version,
            "releaseLine": to_release_line(snapshot.version),
            "date": snapshot.date,
            "snapshot": builder.build(),
        }
        releases.append(release)

    build_release_diffs(releases)
    latest_release = releases[-1]
    return {
        "sourceName": source_name,
        "versionFilter": version_filter,
        "repo": {
            "remote": sources.repo_remote,
            "webUrl": sources.repo_web_url,
        },
        "latestRelease": latest_release["tag"],
        "latestSnapshot": latest_release["snapshot"],
        "releases": releases,
        "endpointLifecycle": build_endpoint_lifecycle(releases),
    }
