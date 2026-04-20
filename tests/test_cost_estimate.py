"""
dotmd-parser — analyze --dry-run cost estimate tests.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from dotmd_parser.analyze import (
    estimate_cost,
    format_cost_estimate,
    MODEL_PRICING,
)


class TestEstimateCost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.md").write_text("hello world", encoding="utf-8")
        (self.root / "b.md").write_text("a" * 4000, encoding="utf-8")  # ~1000 tokens

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_expected_keys(self):
        est = estimate_cost(str(self.root))
        for key in (
            "model",
            "documents",
            "input_tokens",
            "output_tokens",
            "input_usd",
            "output_usd",
            "total_usd",
        ):
            self.assertIn(key, est)

    def test_counts_documents(self):
        est = estimate_cost(str(self.root))
        self.assertEqual(est["documents"], 2)

    def test_positive_costs(self):
        est = estimate_cost(str(self.root))
        self.assertGreater(est["input_tokens"], 0)
        self.assertGreater(est["total_usd"], 0)

    def test_empty_folder_zero_cost(self):
        with tempfile.TemporaryDirectory() as empty:
            est = estimate_cost(empty)
            self.assertEqual(est["documents"], 0)
            self.assertEqual(est["input_tokens"], 0)
            self.assertEqual(est["total_usd"], 0.0)

    def test_respects_model_override(self):
        est_sonnet = estimate_cost(str(self.root), model="claude-sonnet-4-5")
        est_haiku = estimate_cost(str(self.root), model="claude-haiku-4-5")
        self.assertEqual(est_sonnet["model"], "claude-sonnet-4-5")
        self.assertEqual(est_haiku["model"], "claude-haiku-4-5")
        # Haiku is cheaper than Sonnet
        self.assertLess(est_haiku["total_usd"], est_sonnet["total_usd"])

    def test_unknown_model_uses_fallback(self):
        est = estimate_cost(str(self.root), model="claude-imaginary")
        self.assertGreater(est["total_usd"], 0)
        self.assertIn("pricing_note", est)

    def test_more_files_mean_more_tokens(self):
        est_small = estimate_cost(str(self.root))
        (self.root / "big.md").write_text("x" * 10_000, encoding="utf-8")
        est_big = estimate_cost(str(self.root))
        self.assertGreater(est_big["input_tokens"], est_small["input_tokens"])


class TestModelPricingTable(unittest.TestCase):
    def test_sonnet_present(self):
        self.assertIn("claude-sonnet-4-5", MODEL_PRICING)

    def test_pricing_positive(self):
        for model, pricing in MODEL_PRICING.items():
            self.assertGreater(pricing["input_per_mtok"], 0, model)
            self.assertGreater(pricing["output_per_mtok"], 0, model)

    def test_output_more_expensive_than_input(self):
        """Convention: output tokens cost more than input for all Claude tiers."""
        for model, pricing in MODEL_PRICING.items():
            self.assertGreater(
                pricing["output_per_mtok"], pricing["input_per_mtok"], model
            )


class TestFormatCostEstimate(unittest.TestCase):
    def test_contains_dollar_sign(self):
        est = {
            "model": "claude-sonnet-4-5",
            "documents": 3,
            "input_tokens": 1500,
            "output_tokens": 4096,
            "input_usd": 0.0045,
            "output_usd": 0.0614,
            "total_usd": 0.0659,
        }
        out = format_cost_estimate(est)
        self.assertIn("$", out)
        self.assertIn("claude-sonnet-4-5", out)
        self.assertIn("3", out)


class TestAnalyzeDryRunCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.md").write_text("# A\nhello", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_cli(self, argv):
        from dotmd_parser.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = args.func(args)
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_no_api_key_needed(self):
        import os

        prev = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            rc, stdout, _err = self._run_cli(["analyze", str(self.root), "--dry-run"])
        finally:
            if prev is not None:
                os.environ["ANTHROPIC_API_KEY"] = prev
        self.assertEqual(rc, 0)
        self.assertIn("$", stdout)

    def test_dry_run_shows_token_counts(self):
        rc, stdout, _err = self._run_cli(["analyze", str(self.root), "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("token", stdout.lower())


if __name__ == "__main__":
    unittest.main()
