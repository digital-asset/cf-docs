from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from google.protobuf import descriptor_pb2

from tests.minimal_characterization.helpers import (
    assert_contains_all,
    assert_contains_none,
    assert_text_tree_matches_fixture,
    mdx_file_set,
    read_mdx,
    run_x2mdx,
)


def make_field(
    name: str,
    number: int,
    *,
    type_name: str = "",
    scalar_type: int | None = None,
    repeated: bool = False,
) -> descriptor_pb2.FieldDescriptorProto:
    field = descriptor_pb2.FieldDescriptorProto(
        name=name,
        number=number,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED if repeated else descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
    )
    if type_name:
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
        field.type_name = type_name
    else:
        field.type = scalar_type or descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    return field


def make_message(name: str, fields: list[descriptor_pb2.FieldDescriptorProto]) -> descriptor_pb2.DescriptorProto:
    message = descriptor_pb2.DescriptorProto(name=name)
    message.field.extend(fields)
    return message


def make_method(name: str, request_type: str, response_type: str) -> descriptor_pb2.MethodDescriptorProto:
    return descriptor_pb2.MethodDescriptorProto(name=name, input_type=request_type, output_type=response_type)


class ProtobufMinimalLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_descriptor_image(self, relative_path: str, file_proto: descriptor_pb2.FileDescriptorProto) -> Path:
        path = self.root / "fixtures" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor_set = descriptor_pb2.FileDescriptorSet()
        descriptor_set.file.extend([file_proto])
        path.write_bytes(gzip.compress(descriptor_set.SerializeToString()))
        return path

    def _write_manifest(self, *, metadata_overlay: dict[str, object] | None = None) -> Path:
        import_path = "com/example/payments/v1/payments.proto"
        repo_path = "community/example/src/main/protobuf/com/example/payments/v1/payments.proto"
        package = "com.example.payments.v1"

        v1 = descriptor_pb2.FileDescriptorProto(name=import_path, package=package, syntax="proto3")
        v1.message_type.extend(
            [
                make_message("CreatePaymentRequest", [make_field("payment_id", 1)]),
                make_message("PaymentResult", [make_field("payment_id", 1)]),
                make_message("LegacyPaymentRequest", [make_field("legacy_id", 1)]),
                make_message("LegacyPaymentResult", [make_field("legacy_id", 1)]),
            ]
        )
        service_v1 = descriptor_pb2.ServiceDescriptorProto(name="PaymentService")
        service_v1.method.extend(
            [
                make_method("CreatePayment", f".{package}.CreatePaymentRequest", f".{package}.PaymentResult"),
                make_method("LegacyPayment", f".{package}.LegacyPaymentRequest", f".{package}.LegacyPaymentResult"),
            ]
        )
        v1.service.extend([service_v1])

        v2 = descriptor_pb2.FileDescriptorProto(name=import_path, package=package, syntax="proto3")
        v2.message_type.extend(
            [
                make_message("CreatePaymentRequest", [make_field("payment_id", 1)]),
                make_message(
                    "PaymentResultV2",
                    [
                        make_field("payment_id", 1),
                        make_field("amount", 2, scalar_type=descriptor_pb2.FieldDescriptorProto.TYPE_INT64),
                    ],
                ),
                make_message("ListPaymentsRequest", [make_field("party", 1)]),
                make_message(
                    "ListPaymentsResponse",
                    [make_field("payments", 1, type_name=f".{package}.PaymentResultV2", repeated=True)],
                ),
            ]
        )
        service_v2 = descriptor_pb2.ServiceDescriptorProto(name="PaymentService")
        service_v2.method.extend(
            [
                make_method("CreatePayment", f".{package}.CreatePaymentRequest", f".{package}.PaymentResultV2"),
                make_method("ListPayments", f".{package}.ListPaymentsRequest", f".{package}.ListPaymentsResponse"),
            ]
        )
        v2.service.extend([service_v2])

        image_v1 = self._write_descriptor_image("1.0.0/image.bin.gz", v1)
        image_v2 = self._write_descriptor_image("1.1.0/image.bin.gz", v2)
        metadata_path = self.root / "fixtures" / "metadata.json"
        metadata_path.write_text(
            json.dumps(
                metadata_overlay
                or {
                    "schemaVersion": 1,
                    "files": {},
                    "services": {},
                    "endpoints": {},
                    "messages": {},
                    "fields": {},
                    "enums": {},
                    "enumValues": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path = self.root / "fixtures" / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "source": "minimal protobuf lifecycle fixtures",
                    "repo": {
                        "remote": "https://github.com/example/repo.git",
                        "web_url": "https://github.com/example/repo",
                    },
                    "metadata_path": str(metadata_path),
                    "versions": [
                        {
                            "version": "1.0.0",
                            "tag": "v1.0.0",
                            "date": "2026-01-01",
                            "descriptor_image_path": str(image_v1),
                            "import_to_repo_path": {import_path: repo_path},
                        },
                        {
                            "version": "1.1.0",
                            "tag": "v1.1.0",
                            "date": "2026-02-01",
                            "descriptor_image_path": str(image_v2),
                            "import_to_repo_path": {import_path: repo_path},
                        },
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return manifest_path

    def _render_pages(self, relative_output_dir: str = "protobuf-history") -> Path:
        manifest_path = self._write_manifest()
        output_dir = self.root / "out" / relative_output_dir
        run_x2mdx(
            [
                "protobuf",
                "build-api-pages-from-manifest",
                "--manifest",
                str(manifest_path),
                "--output-dir",
                str(output_dir),
                "--source-name",
                "minimal protobuf lifecycle fixtures",
                "--version-filter",
                "minimal versions",
            ]
        )
        return output_dir

    def test_cli_renders_minimal_source_contract(self) -> None:
        output_dir = self._render_pages()

        self.assertEqual(
            mdx_file_set(output_dir),
            {
                "index.mdx",
                "packages/com-example-payments-v1.mdx",
                "operations/com-example-payments-v1/paymentservice/createpayment.mdx",
                "operations/com-example-payments-v1/paymentservice/listpayments.mdx",
            },
        )
        assert_text_tree_matches_fixture(output_dir, "protobuf/default")

        overview = read_mdx(output_dir, "index.mdx")
        package = read_mdx(output_dir, "packages/com-example-payments-v1.mdx")
        create_payment = read_mdx(output_dir, "operations/com-example-payments-v1/paymentservice/createpayment.mdx")
        list_payments = read_mdx(output_dir, "operations/com-example-payments-v1/paymentservice/listpayments.mdx")

        assert_contains_all(
            overview,
            [
                "Canton Protobuf Reference",
                "Release Summary",
                "minimal protobuf lifecycle fixtures",
                "minimal versions",
                "com.example.payments.v1",
                "1 / 1 / 1",
            ],
        )
        assert_contains_all(
            package,
            [
                "PaymentService.CreatePayment",
                "PaymentService.ListPayments",
                "Since 1.0.0",
                "Changed 1.1.0",
                "Since 1.1.0",
                "Type Inventory",
                "PaymentResultV2",
            ],
        )
        assert_contains_none(package, ["LegacyPayment"])
        assert_contains_all(
            create_payment,
            [
                "Protocol Details",
                "Since 1.0.0",
                "Changed 1.1.0",
                "/com.example.payments.v1.PaymentService/CreatePayment",
                "grpcurl",
                "<HOST:PORT>",
                "Related Schemas",
                "CreatePaymentRequest",
                "PaymentResultV2",
                "amount",
                '"amount": "0"',
            ],
        )
        assert_contains_all(list_payments, ["Since 1.1.0", "ListPaymentsRequest", "ListPaymentsResponse", "payments"])
        assert_contains_none(
            overview + package + create_payment + list_payments,
            ["FileDescriptorSet", "descriptor_image_path", "TYPE_MESSAGE", "x-state", "x-replaced-by", "x-replaces"],
        )

    def test_cli_renders_explicit_lifecycle_states(self) -> None:
        package = "com.example.payments.v1"
        manifest_path = self._write_manifest(
            metadata_overlay={
                "schemaVersion": 1,
                "files": {},
                "services": {},
                "endpoints": {
                    f"{package}.PaymentService.CreatePayment": {
                        "lifecycle": {"state": "beta"},
                    },
                    f"{package}.PaymentService.ListPayments": {
                        "lifecycle": {"state": "stable"},
                    },
                },
                "messages": {
                    f"{package}.CreatePaymentRequest": {
                        "lifecycle": {"state": "alpha"},
                    },
                    f"{package}.PaymentResultV2": {
                        "lifecycle": {"state": "deprecated"},
                    },
                },
                "fields": {},
                "enums": {},
                "enumValues": {},
            },
        )
        output_dir = self.root / "out" / "protobuf-lifecycle-states"

        run_x2mdx(
            [
                "protobuf",
                "build-api-pages-from-manifest",
                "--manifest",
                str(manifest_path),
                "--output-dir",
                str(output_dir),
            ]
        )

        package_page = read_mdx(output_dir, "packages/com-example-payments-v1.mdx")
        create_payment = read_mdx(output_dir, "operations/com-example-payments-v1/paymentservice/createpayment.mdx")
        list_payments = read_mdx(output_dir, "operations/com-example-payments-v1/paymentservice/listpayments.mdx")
        assert_text_tree_matches_fixture(output_dir, "protobuf/lifecycle")
        assert_contains_all(package_page, ["Lifecycle", "Beta", "Stable"])
        assert_contains_all(create_payment, ["Lifecycle", "Beta", "Alpha", "Deprecated"])
        assert_contains_all(list_payments, ["Lifecycle", "Stable"])

    def test_cli_renders_replacement_metadata(self) -> None:
        package = "com.example.payments.v1"
        manifest_path = self._write_manifest(
            metadata_overlay={
                "schemaVersion": 1,
                "files": {},
                "services": {},
                "endpoints": {
                    f"{package}.PaymentService.ListPayments": {
                        "lifecycle": {
                            "state": "stable",
                            "replaces": f"{package}.PaymentService.LegacyPayment",
                        },
                    },
                },
                "messages": {},
                "fields": {},
                "enums": {},
                "enumValues": {},
            },
        )
        output_dir = self.root / "out" / "protobuf-replacements"

        run_x2mdx(
            [
                "protobuf",
                "build-api-pages-from-manifest",
                "--manifest",
                str(manifest_path),
                "--output-dir",
                str(output_dir),
            ]
        )

        list_payments = read_mdx(output_dir, "operations/com-example-payments-v1/paymentservice/listpayments.mdx")
        assert_text_tree_matches_fixture(output_dir, "protobuf/replacements")
        assert_contains_all(
            list_payments,
            [
                "Replaces",
                f"{package}.PaymentService.LegacyPayment",
            ],
        )

    def test_cli_prunes_stale_output(self) -> None:
        manifest_path = self._write_manifest()
        output_dir = self.root / "out" / "protobuf-prune"
        stale_file = output_dir / "operations" / "stale" / "index.mdx"
        stale_file.parent.mkdir(parents=True, exist_ok=True)
        stale_file.write_text("stale\n", encoding="utf-8")

        run_x2mdx(
            [
                "protobuf",
                "build-api-pages-from-manifest",
                "--manifest",
                str(manifest_path),
                "--output-dir",
                str(output_dir),
            ]
        )

        self.assertFalse(stale_file.exists())
        assert_text_tree_matches_fixture(output_dir, "protobuf/prune")

    @unittest.skip("protobuf CLI does not expose docs-json navigation flags")
    def test_cli_updates_docs_json_navigation_idempotently(self) -> None:
        pass
