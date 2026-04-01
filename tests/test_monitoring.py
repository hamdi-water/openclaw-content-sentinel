import os
import unittest
from pathlib import Path

from openclaw_content_sentinel.monitoring import MonitoringManager


class TestMonitoring(unittest.TestCase):
    def setUp(self):
        self.test_prom = Path("test_metrics.prom")
        self.monitor = MonitoringManager(metrics_path=str(self.test_prom))
        # Reset registry for clean tests if possible, but singleton makes it tricky
        # In a real SRE context, we'd use a fresh registry for each test

    def tearDown(self):
        if self.test_prom.exists():
            os.remove(self.test_prom)

    def test_singleton(self):
        m1 = MonitoringManager()
        m2 = MonitoringManager()
        self.assertIs(m1, m2)

    def test_metric_increment(self):
        # We use a dummy status to avoid interfering with production metrics if shared
        self.monitor.toggle_run(status="test_started", trigger="unit_test")
        self.monitor.record_rag_failure()
        self.monitor.record_scraping_error(source="test_source", error_type="TestError")
        self.monitor.record_latency(1.23)
        self.monitor.export()

        self.assertTrue(self.test_prom.exists())
        content = self.test_prom.read_text()

        self.assertIn('sentinel_runs_total{status="test_started",trigger="unit_test"}', content)
        self.assertIn('sentinel_rag_groundedness_failures_total', content)
        self.assertIn('sentinel_scraping_errors_total{error_type="TestError",source="test_source"}', content)
        self.assertIn('sentinel_pipeline_duration_seconds_sum', content)

    def test_export_creates_directory(self):
        nested_prom = Path("tmp_metrics/metrics.prom")
        m = MonitoringManager(metrics_path=str(nested_prom))
        m.export()
        self.assertTrue(nested_prom.exists())
        # Cleanup
        os.remove(nested_prom)
        os.rmdir(nested_prom.parent)

if __name__ == "__main__":
    unittest.main()
