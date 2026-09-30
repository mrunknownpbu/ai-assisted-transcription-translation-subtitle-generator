"""Deterministic tests for the measurement-only QC review priority report."""

import importlib.util
import unittest
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "analyze_qc_review_priority",
    Path(__file__).resolve().parent.parent / "scripts" / "analyze_qc_review_priority.py",
)
priority = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(priority)


class ReviewPriorityTests(unittest.TestCase):
    def test_ranks_existing_qc_signals_and_explains_each_contribution(self):
        report = priority.rank_jobs({"jobs": [
            {
                "id": "beta",
                "status": "completed",
                "needs_review": 3,
                "log": [{"level": "error"}, {"level": "warning"}],
                "qc": {
                    "translation": {
                        "population": 10, "flagged": 4, "unresolved": 1,
                        "findings": [
                            {"category": "entity_error", "confidence": 0.8},
                            {"category": "hallucination", "confidence": 0.5},
                            {"category": "translation_error", "confidence": 0.9},
                            {"category": "readability_error", "confidence": 0.8},
                        ],
                    },
                },
            },
            {
                "id": "alpha",
                "status": "completed",
                "qc": {"translation": {"findings": [{"category": "translation_error", "confidence": 0.5}]}},
            },
            {"id": "ignored", "status": "running", "qc": {"entity": {"findings": [
                {"category": "entity_error", "confidence": 1.0}]}}},
        ]})

        self.assertTrue(report["measurement_only"])
        self.assertEqual(report["completed_jobs_seen"], 2)
        self.assertEqual([row["job_id"] for row in report["review_candidates"]], ["beta", "alpha"])
        self.assertEqual(report["review_candidates"][0]["score"], 19.25)
        self.assertEqual(
            [item["feature"] for item in report["review_candidates"][0]["contributions"]],
            ["entity_finding", "hallucination_finding", "translation_finding",
             "other_high_confidence", "flagged_rate", "unresolved", "needs_review", "log_severity"],
        )

    def test_ties_sort_by_job_id_and_malformed_optional_data_is_safe(self):
        report = priority.rank_jobs([
            {"id": "z-job", "status": "completed", "qc": {"translation": {
                "findings": [{"category": "translation_error", "confidence": 0.5}]}}},
            {"id": "a-job", "status": "completed", "qc": {"translation": {
                "findings": [{"category": "translation_error", "confidence": 0.5}]}}},
            {"id": "bad-data", "status": "completed", "qc": ["not-a-stage"], "log": "not-a-list"},
        ])

        self.assertEqual([row["job_id"] for row in report["review_candidates"]], ["a-job", "z-job"])
        self.assertEqual([row["rank"] for row in report["review_candidates"]], [1, 2])
        self.assertEqual(priority.rank_jobs({"status": "completed", "qc": None})["review_candidates"], [])

    def test_rejects_unsupported_top_level_shape(self):
        with self.assertRaisesRegex(ValueError, "input must be"):
            priority.rank_jobs("not a job export")


if __name__ == "__main__":
    unittest.main()
