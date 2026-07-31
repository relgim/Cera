from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from cera.contracts import EvidenceRecordType
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId
from cera.providers import (
    CodexSDKTransport,
    ProviderTransportError,
    codex_transport_probe_output_schema,
)
from cera.reasoner import (
    ENABLED_MCP_EVIDENCE_TOOLS,
    MCP_SERVER_NAME,
    McpEvidenceBridgeError,
    McpEvidenceBridgeReceipt,
    McpEvidenceDispatcher,
    McpEvidenceToolName,
    ReasonerEvidenceTools,
    RequestBoundMcpEvidenceBridge,
)
from cera.registry import build_schema_registry
from tests.test_provider_qualification import (
    StaticCodexRunner,
    codex_mcp_binding,
    codex_route,
)
import tests.test_reasoner as reasoner_test_support


class McpEvidenceBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = reasoner_test_support.ReasonerTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.prepared = self.fixture.prepared("mcp-bridge")
        self.request = self.fixture.request(self.prepared)
        self.tools = ReasonerEvidenceTools(self.fixture.service, self.request)
        self.dispatcher = McpEvidenceDispatcher(
            self.tools,
            reasoner_request_sha256=self.request.request_sha256,
            snapshot=self.prepared.evidence_snapshot,
        )

    def test_dispatcher_snapshot_search_and_exact_fetch_use_existing_policy(self) -> None:
        snapshot = self.dispatcher.invoke(
            McpEvidenceToolName.GET_TURN_SNAPSHOT.value
        )
        self.assertEqual(
            snapshot["snapshot_token"],
            str(self.prepared.evidence_snapshot.snapshot_token),
        )
        self.assertEqual(
            snapshot["cera_tool_budget"]["remaining_followup_searches"],
            self.fixture.service.limits.maximum_followup_searches,
        )

        search = self.dispatcher.invoke(
            McpEvidenceToolName.SEARCH_EVIDENCE.value,
            {"terms": ["present"], "limit": 1},
        )
        self.assertEqual(len(search["references"]), 1)
        self.assertEqual(
            search["cera_tool_budget"]["remaining_followup_searches"],
            self.fixture.service.limits.maximum_followup_searches - 1,
        )
        self.assertEqual(
            search["cera_tool_budget"]["next_step"],
            "fetch_relevant_exact_references_before_any_new_search",
        )
        evidence_id = search["references"][0]["evidence_id"]
        exact = self.dispatcher.invoke(
            McpEvidenceToolName.FETCH_EVIDENCE.value,
            {
                "evidence_id": evidence_id,
                "sections": ["claim", "provenance"],
            },
        )
        self.assertEqual(len(exact["exact_records"]), 1)
        self.assertEqual(exact["exact_records"][0]["evidence_id"], evidence_id)
        self.assertEqual(
            exact["exact_records"][0]["citation_alias"],
            "evidence:fetch_001",
        )
        self.assertEqual(
            self.dispatcher.citation_aliases,
            {"evidence:fetch_001": TypedId.parse(evidence_id, IdKind.EVIDENCE)},
        )
        self.assertEqual(
            exact["cera_tool_budget"]["remaining_followup_searches"],
            self.fixture.service.limits.maximum_followup_searches - 1,
        )
        self.assertEqual(
            exact["cera_tool_budget"]["next_step"],
            "complete_if_exact_evidence_suffices",
        )
        self.assertEqual(len(self.dispatcher.calls), 3)
        self.assertTrue(all(call.success for call in self.dispatcher.calls))
        self.assertEqual(self.tools.tool_call_count, 3)
        self.assertEqual(len(self.tools.exact_evidence), 1)

    def test_dispatcher_exposes_bounded_query_plan_without_granting_exact_evidence(self) -> None:
        search = self.dispatcher.invoke(
            McpEvidenceToolName.SEARCH_QUERY_PLAN.value,
            {
                "schema_version": "cera.evidence_query_plan.v1",
                "primary_terms": ["not expected"],
                "alternate_term_sets": [["present"]],
                "entity_ids": [],
                "tags": [],
                "record_types": [],
                "limit": 2,
                "maximum_variants": 2,
                "ambiguity_policy": "return_bounded_candidates",
            },
        )
        self.assertEqual(len(search["references"]), 1)
        self.assertEqual(search["exact_records"], [])
        self.assertEqual(search["receipt"]["operation"], "search_query_plan")
        self.assertEqual(search["receipt"]["followup_search_count"], 1)
        self.assertEqual(self.tools.exact_evidence, {})

    def test_failed_query_plan_retains_safe_field_diagnostic_without_values(self) -> None:
        secret_value = "private-phrase-must-not-appear"
        with self.assertRaises(McpEvidenceBridgeError) as caught:
            self.dispatcher.invoke(
                McpEvidenceToolName.SEARCH_QUERY_PLAN.value,
                {
                    "schema_version": "cera.evidence_query_plan.v1",
                    "primary_terms": [],
                    "alternate_term_sets": [[secret_value]],
                    "entity_ids": [],
                    "tags": [],
                    "record_types": [],
                    "limit": 2,
                    "maximum_variants": 2,
                    "ambiguity_policy": "return_bounded_candidates",
                },
            )
        receipt = self.dispatcher.calls[-1]
        self.assertFalse(receipt.success)
        self.assertIsNotNone(receipt.error_field_path)
        self.assertIsNotNone(receipt.error_reason)
        self.assertNotIn(secret_value, str(caught.exception))
        self.assertNotIn(secret_value, repr(receipt))

    def test_domain_decoder_still_rejects_invalid_provider_vocabulary(self) -> None:
        invalid_payloads = (
            {
                "schema_version": "cera.evidence_query_plan.v1",
                "primary_terms": ["present"],
                "alternate_term_sets": [],
                "entity_ids": [],
                "tags": [],
                "record_types": [],
                "limit": 2,
                "maximum_variants": 1,
                "ambiguity_policy": "model-invented-policy",
            },
            {
                "terms": ["present"],
                "entity_ids": [],
                "tags": [],
                "record_types": ["model-invented-record-type"],
                "limit": 2,
            },
        )
        for index, payload in enumerate(invalid_payloads):
            operation = (
                McpEvidenceToolName.SEARCH_QUERY_PLAN
                if index == 0
                else McpEvidenceToolName.SEARCH_EVIDENCE
            )
            with self.subTest(operation=operation):
                with self.assertRaises(McpEvidenceBridgeError):
                    self.dispatcher.invoke(operation.value, payload)
                receipt = self.dispatcher.calls[-1]
                self.assertFalse(receipt.success)
                self.assertEqual(
                    receipt.error_code,
                    ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                )
                self.assertEqual(receipt.error_reason, "invalid_value")

    def test_dispatcher_rejects_unknown_fields_and_wrong_typed_ids(self) -> None:
        invalid_payloads = (
            {
                "terms": ["present"],
                "limit": 1,
                "untrusted_extension": True,
            },
            {
                "entity_ids": ["source:not-a-character"],
                "limit": 1,
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(McpEvidenceBridgeError) as caught:
                    operation = (
                        McpEvidenceToolName.SEARCH_EVIDENCE.value
                        if "terms" in payload
                        else McpEvidenceToolName.RESOLVE_ENTITIES.value
                    )
                    self.dispatcher.invoke(operation, payload)
                self.assertEqual(
                    caught.exception.code,
                    ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                )
        self.assertEqual(len(self.dispatcher.calls), 2)
        self.assertTrue(all(not call.success for call in self.dispatcher.calls))

        with self.assertRaises(McpEvidenceBridgeError) as caught:
            self.dispatcher.invoke(  # type: ignore[arg-type]
                McpEvidenceToolName.GET_TURN_SNAPSHOT.value, []
            )
        self.assertEqual(
            caught.exception.code,
            ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
        )

    def test_dispatcher_preserves_evidence_service_error_code(self) -> None:
        with self.assertRaises(McpEvidenceBridgeError) as caught:
            self.dispatcher.invoke(
                McpEvidenceToolName.SEARCH_EVIDENCE.value,
                {"terms": ["x" * 513], "limit": 1},
            )
        self.assertEqual(caught.exception.code, ErrorCode.EVIDENCE_LIMIT_EXCEEDED)
        self.assertEqual(
            self.dispatcher.calls[-1].error_code,
            ErrorCode.EVIDENCE_LIMIT_EXCEEDED,
        )

    def test_shared_search_budget_names_resolve_and_publishes_zero_remaining(self) -> None:
        maximum = self.fixture.service.limits.maximum_followup_searches
        operations = (
            McpEvidenceToolName.RESOLVE_ENTITIES,
            McpEvidenceToolName.SEARCH_EVIDENCE,
            McpEvidenceToolName.SEARCH_QUERY_PLAN,
            McpEvidenceToolName.SEARCH_EVIDENCE,
        )
        latest = None
        for operation in operations[:maximum]:
            if operation is McpEvidenceToolName.RESOLVE_ENTITIES:
                payload = {
                    "entity_ids": [str(self.prepared.request.protected_user_id)],
                    "limit": 1,
                }
                # The protected user is not a resolvable evidence entity. Use the
                # selected NPC typed ID while preserving the shared-budget probe.
                payload["entity_ids"] = [
                    str(self.prepared.eligible_responding_npc_ids[0])
                ]
            elif operation is McpEvidenceToolName.SEARCH_QUERY_PLAN:
                payload = {
                    "schema_version": "cera.evidence_query_plan.v1",
                    "primary_terms": ["not-present-probe"],
                    "alternate_term_sets": [],
                    "entity_ids": [],
                    "tags": [],
                    "record_types": [],
                    "limit": 1,
                    "maximum_variants": 1,
                    "ambiguity_policy": "return_bounded_candidates",
                }
            else:
                payload = {"terms": ["not-present-probe"], "limit": 1}
            latest = self.dispatcher.invoke(operation.value, payload)
        self.assertIsNotNone(latest)
        budget = latest["cera_tool_budget"]
        self.assertEqual(budget["used_followup_searches"], maximum)
        self.assertEqual(budget["remaining_followup_searches"], 0)
        self.assertIn(
            McpEvidenceToolName.RESOLVE_ENTITIES.value,
            budget["search_budget_shared_by"],
        )
        self.assertFalse(budget["exact_fetch_uses_followup_search_budget"])
        with self.assertRaises(McpEvidenceBridgeError) as caught:
            self.dispatcher.invoke(
                McpEvidenceToolName.SEARCH_EVIDENCE.value,
                {"terms": ["one-too-many"], "limit": 1},
            )
        self.assertEqual(caught.exception.code, ErrorCode.EVIDENCE_LIMIT_EXCEEDED)

    def test_dispatcher_enforces_total_tool_call_ceiling(self) -> None:
        limited = McpEvidenceDispatcher(
            self.tools,
            reasoner_request_sha256=self.request.request_sha256,
            snapshot=self.prepared.evidence_snapshot,
            maximum_tool_calls=1,
        )
        limited.invoke(McpEvidenceToolName.GET_TURN_SNAPSHOT.value)
        with self.assertRaises(McpEvidenceBridgeError) as caught:
            limited.invoke(McpEvidenceToolName.GET_TURN_SNAPSHOT.value)
        self.assertEqual(caught.exception.code, ErrorCode.EVIDENCE_LIMIT_EXCEEDED)
        self.assertEqual(len(limited.calls), 2)
        self.assertFalse(limited.calls[-1].success)

    def test_bridge_receipt_reconciles_independent_provider_observation(self) -> None:
        bridge = RequestBoundMcpEvidenceBridge(
            self.tools,
            reasoner_request_sha256=self.request.request_sha256,
            snapshot=self.prepared.evidence_snapshot,
            minimum_tool_calls=1,
        )
        bridge.dispatcher.invoke(McpEvidenceToolName.GET_TURN_SNAPSHOT.value)
        runner = StaticCodexRunner(
            mcp_server_names=(MCP_SERVER_NAME,),
            mcp_tool_names=(McpEvidenceToolName.GET_TURN_SNAPSHOT.value,),
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = CodexSDKTransport(
                codex_route(), workspace=Path(temporary), runner=runner
            ).invoke(
                "Synthetic bridge receipt probe.",
                output_schema=codex_transport_probe_output_schema(),
                mcp_binding=codex_mcp_binding(),
            )
        receipt = bridge.finalize(result)
        self.assertEqual(receipt.bridge_receipt_id.kind.value, "mcp_bridge_receipt")
        self.assertEqual(receipt.provider_observed_tool_calls, 1)
        self.assertEqual(receipt.authoritative_store_writes, 0)
        self.assertFalse(receipt.credential_retained)
        self.assertFalse(receipt.raw_source_retained)
        self.assertFalse(receipt.story_prose_retained)
        self.assertEqual(receipt.enabled_tools, ENABLED_MCP_EVIDENCE_TOOLS)
        historical = replace(
            receipt,
            tool_contract_version="cera.reasoner_evidence_mcp.v4",
        )
        self.assertEqual(
            historical.tool_contract_version,
            "cera.reasoner_evidence_mcp.v4",
        )

        mismatched = replace(
            result,
            tool_server_names=(),
            tool_names=(),
            tool_call_count=0,
        )
        with self.assertRaises(McpEvidenceBridgeError) as caught:
            bridge.finalize(mismatched)
        self.assertEqual(
            caught.exception.code,
            ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
        )

    def test_bridge_receipt_schema_is_registered(self) -> None:
        self.assertIn(
            McpEvidenceBridgeReceipt.SCHEMA_VERSION,
            build_schema_registry().versions,
        )

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK not installed")
    def test_loopback_server_requires_bearer_and_exposes_only_allow_list(self) -> None:
        async def exercise() -> None:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            import httpx

            bridge = RequestBoundMcpEvidenceBridge(
                self.tools,
                reasoner_request_sha256=self.request.request_sha256,
                snapshot=self.prepared.evidence_snapshot,
                minimum_tool_calls=1,
            )
            with bridge:
                binding = bridge.runtime_binding
                self.assertNotIn(binding.bearer_token, repr(binding))
                async with httpx.AsyncClient() as unauthenticated:
                    denied = await unauthenticated.post(binding.url)
                self.assertEqual(denied.status_code, 401)
                headers = {"Authorization": f"Bearer {binding.bearer_token}"}
                async with httpx.AsyncClient(headers=headers) as client:
                    async with streamable_http_client(
                        binding.url, http_client=client
                    ) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            available = await session.list_tools()
                            self.assertEqual(
                                tuple(tool.name for tool in available.tools),
                                ENABLED_MCP_EVIDENCE_TOOLS,
                            )
                            by_name = {
                                tool.name: tool for tool in available.tools
                            }
                            search_schema_json = json.dumps(
                                by_name[
                                    McpEvidenceToolName.SEARCH_EVIDENCE.value
                                ].inputSchema,
                                sort_keys=True,
                            )
                            self.assertIn('"enum"', search_schema_json)
                            for record_type in EvidenceRecordType:
                                self.assertIn(
                                    f'"{record_type.value}"',
                                    search_schema_json,
                                )
                            query_schema = by_name[
                                McpEvidenceToolName.SEARCH_QUERY_PLAN.value
                            ].inputSchema
                            self.assertNotIn(
                                "ambiguity_policy",
                                query_schema["properties"],
                            )
                            query = await session.call_tool(
                                McpEvidenceToolName.SEARCH_QUERY_PLAN.value,
                                {
                                    "primary_terms": ["present"],
                                    "alternate_term_sets": [],
                                    "record_types": ["memory"],
                                    "limit": 2,
                                    "maximum_variants": 1,
                                },
                            )
                            self.assertFalse(query.isError)
                            result = await session.call_tool(
                                McpEvidenceToolName.GET_TURN_SNAPSHOT.value, {}
                            )
                            self.assertFalse(result.isError)
                            invalid = await session.call_tool(
                                McpEvidenceToolName.FETCH_EVIDENCE.value,
                                {
                                    "evidence_ids": ["evidence:wrong-shape"],
                                    "sections": ["claim"],
                                },
                            )
                            self.assertTrue(invalid.isError)
            self.assertEqual(len(bridge.dispatcher.calls), 3)
            failed = bridge.dispatcher.calls[2]
            self.assertFalse(failed.success)
            self.assertEqual(failed.tool_name, McpEvidenceToolName.FETCH_EVIDENCE)
            self.assertEqual(failed.error_field_path, "input.evidence_id")
            self.assertEqual(failed.error_reason, "missing")

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
