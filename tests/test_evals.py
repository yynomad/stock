"""No-network evaluation regression tests. Requires existing StockMan dependencies."""
import json
from pathlib import Path
import unittest
from stockman.evals.dataset import load_suite
from stockman.evals.runner import DEFAULT_SUITE, execute, compare
from stockman.evals.scorers import evaluate_case


class EvaluationTests(unittest.TestCase):
    def test_versioned_suite_has_40_cases_and_every_category(self):
        version, cases = load_suite(DEFAULT_SUITE)
        self.assertEqual(version, "1")
        self.assertEqual(len(cases), 40)
        self.assertEqual(
            {c["category"] for c in cases},
            {"evidence", "citation", "tool_call", "failure", "freshness", "safety"},
        )

    def test_all_synthetic_replay_oracles_match(self):
        report = execute(DEFAULT_SUITE, mode="replay")
        self.assertEqual(report["summary"]["runs"], 40)
        self.assertEqual(report["summary"]["replay_oracles_matched"], 40)
        self.assertEqual(report["provenance"], "synthetic_recording_no_llm")
        self.assertEqual(report["summary"]["latency_measured_runs"], 0)
        self.assertEqual(report["summary"]["usage_known_runs"], 0)

    def test_regression_comparison_is_mode_scoped(self):
        baseline = execute(DEFAULT_SUITE, mode="replay")
        current = json.loads(json.dumps(baseline))
        self.assertEqual(compare(baseline, current)["regressions"], [])
        current["mode"] = "live"
        with self.assertRaises(ValueError):
            compare(baseline, current)

    def test_live_requires_real_key_and_is_never_automatic(self):
        _, cases = load_suite(DEFAULT_SUITE)
        self.assertTrue(any(c["live"] for c in cases))
        self.assertFalse(any("IBKR_ACCOUNT" in json.dumps(c) for c in cases))

    def test_invalid_case_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            execute(DEFAULT_SUITE, selected_ids={"does-not-exist"})
