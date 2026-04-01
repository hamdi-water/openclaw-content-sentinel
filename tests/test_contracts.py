import unittest

from openclaw_content_sentinel.contracts import (
    ContractValidator,
    validate_run_record,
)
from openclaw_content_sentinel.models import (
    CanonicalDoc,
    DailyInput,
    QualityGateMetrics,
    RunRecord,
)


class TestContracts(unittest.TestCase):
    def test_canonical_envelope_wrapping(self):
        doc = CanonicalDoc(
            id="doc-123",
            source_url="https://example.com",
            title="Test Doc",
            raw_content="Raw",
            clean_text="Clean",
            published_at="2024-01-01",
            ingested_at="2024-01-01"
        )
        envelope = ContractValidator.wrap_envelope(
            payload=doc,
            payload_kind="CanonicalDoc",
            metadata={"priority": "high"}
        )
        self.assertEqual(envelope.payload_kind, "CanonicalDoc")
        self.assertEqual(envelope.payload.id, "doc-123")
        self.assertEqual(envelope.metadata["priority"], "high")
        self.assertIn("message_id", envelope.model_dump())

    def test_run_record_strict_validation(self):
        data = {
            "run_id": "run-456",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
            "prompt": "Test prompt",
            "competitor_url": "https://test.com",
            "keywords": ["test"],
            "platform_targets": ["x"],
            "schema_version": "v6.1",
            "daily_input": {
                "topic": "AI",
                "business_angle": "Efficiency"
            }
        }
        record = validate_run_record(data)
        self.assertIsInstance(record, RunRecord)
        self.assertIsInstance(record.daily_input, DailyInput)
        self.assertEqual(record.daily_input.topic, "AI")
        # Ensure default PostResult is created for 'x'
        self.assertEqual(record.post_results["x"].status, "pending")

    def test_migration_bridge_v60_to_v61(self):
        legacy_data = {
            "run_id": "run-old",
            "created_at": "2023-12-01",
            "updated_at": "2023-12-01",
            "prompt": "Old prompt",
            "competitor_url": "https://old.com",
            "keywords": [],
            "platform_targets": ["linkedin"],
            "schema_version": "v6.0",
            "status": "posted"
        }
        migrated = validate_run_record(legacy_data)
        self.assertEqual(migrated.schema_version, "v6.1")
        self.assertEqual(migrated.approval_state, "approved")
        self.assertIsInstance(migrated.quality_gate, QualityGateMetrics)

    def test_contract_violation_raises(self):
        invalid_data = {
            "run_id": "run-fail",
            "created_at": "now",
            "updated_at": "now",
            "prompt": "minimal",
            "competitor_url": "none",
            "keywords": [],
            "platform_targets": [],
            "schema_version": "v6.2" # Invalid version
        }
        with self.assertRaises(ValueError) as cm:
            validate_run_record(invalid_data)
        self.assertIn("Unsupported schema version", str(cm.exception))

    def test_nested_model_defaults(self):
        minimal_data = {
            "run_id": "run-min",
            "created_at": "now",
            "updated_at": "now",
            "prompt": "minimal",
            "competitor_url": "none",
            "keywords": [],
            "platform_targets": []
        }
        record = validate_run_record(minimal_data)
        self.assertEqual(record.analysis.risk_level, "low")
        self.assertTrue(record.quality_gate.safety_pass)

if __name__ == "__main__":
    unittest.main()
