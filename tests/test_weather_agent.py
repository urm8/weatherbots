"""Offline regression tests; no weather/provider/broker/network calls."""

import csv
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from weather_agent.core import (
    DeepSeek,
    Ledger,
    Limits,
    Rates,
    Rejected,
    instant,
    load_forecast,
    probability,
    run,
    validate_market,
)


class WeatherAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 25, 16, tzinfo=UTC)
        self.market = {
            "id": "TEST-NY-1",
            "city": "ny",
            "date": "2026-10-25",
            "quoted_at": self.now.isoformat(),
            "closes_at": (self.now + timedelta(hours=2)).isoformat(),
            "lower_f": 70,
            "upper_f": 75,
            "yes_ask": 0.2,
            "fee_per_contract": 0.01,
            "station": "KNYC",
            "settlement_source": "NWS_CLI",
            "rules_verified": True,
        }
        self.path = Path(self.temp.name) / "forecast.csv"
        self.forecast(self.now - timedelta(minutes=10))
        self.limits = Limits(api_budget=1)
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite", self.limits)
        self.addCleanup(self.ledger.close)
        self.response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"approve":true,"reason":"reviewed"}'},
                }
            ],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_cache_hit_tokens": 200,
                "prompt_cache_miss_tokens": 800,
            },
        }
        self.calls = []
        self.client = DeepSeek(
            "test-key", "test-model", Rates(0.3, 0.006, 1.2), transport=self.transport
        )

    def transport(self, payload):
        self.calls.append(json.loads(payload))
        return self.response

    def forecast(self, timestamp, **changes):
        row = {
            "date": self.market["date"],
            "city": "ny",
            "tmax_predicted": 73,
            "spread_f": 2,
            "run_ts": timestamp.isoformat(),
        }
        row.update(changes)
        with self.path.open("w", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)

    def execute(self):
        return run(self.ledger, self.market, self.path, self.client, now=self.now)

    def test_paper_open_cost_not_realized_and_request_contract(self):
        result = self.execute()
        self.assertEqual(result["action"], "paper_open")
        self.assertEqual(result["contracts"], 9)
        self.assertAlmostEqual(result["cost_usd"], 1.89)
        status = self.ledger.status()
        self.assertEqual(status["realized_paper_profit_usd"], 0)
        self.assertLess(status["paper_net_after_api_usd"], 0)
        self.assertEqual(self.calls[0]["thinking"], {"type": "disabled"})
        self.assertEqual(self.calls[0]["max_tokens"], 256)
        self.assertEqual(self.calls[0]["response_format"], {"type": "json_object"})

    def test_duplicate_prevents_second_paid_request(self):
        self.execute()
        with self.assertRaisesRegex(Rejected, "duplicate"):
            self.execute()
        self.assertEqual(len(self.calls), 1)

    def test_second_connection_cannot_duplicate_claim(self):
        self.ledger.claim(self.market, 0.8, 1000)
        other = Ledger(Path(self.temp.name) / "ledger.sqlite", self.limits)
        try:
            with self.assertRaisesRegex(Rejected, "duplicate"):
                other.claim(self.market, 0.8, 1000)
        finally:
            other.close()

    def test_immutable_funding_configuration(self):
        with self.assertRaisesRegex(Rejected, "immutable"):
            Ledger(Path(self.temp.name) / "ledger.sqlite", Limits(api_budget=999))

    def test_zero_budget_no_paid_call(self):
        empty = Ledger(Path(self.temp.name) / "empty.sqlite", Limits())
        try:
            with self.assertRaisesRegex(Rejected, "budget exhausted"):
                run(empty, self.market, self.path, self.client, self.now)
            self.assertFalse(self.calls)
        finally:
            empty.close()

    def test_timeout_holds_reservation_without_retry(self):
        def timeout(_):
            raise TimeoutError()

        self.client.transport = timeout
        with self.assertRaises(TimeoutError):
            self.execute()
        status = self.ledger.status()
        self.assertGreater(status["api_spend_or_reserved_usd"], 0)
        self.assertEqual(status["uncertain_api_requests"], 1)
        with self.assertRaisesRegex(Rejected, "duplicate"):
            self.execute()

    def test_malformed_verdict_still_charged(self):
        self.response["choices"][0]["message"]["content"] = "not JSON"
        with self.assertRaises(Rejected):
            self.execute()
        self.assertGreater(self.ledger.status()["api_spend_or_reserved_usd"], 0)
        self.assertEqual(self.ledger.status()["open_paper_cost_usd"], 0)
        self.assertEqual(self.ledger.status()["uncertain_api_requests"], 0)

    def test_usage_mismatch_holds_worst_case(self):
        self.response["usage"]["prompt_cache_hit_tokens"] = 999
        with self.assertRaisesRegex(Rejected, "reconcile"):
            self.execute()
        self.assertEqual(self.ledger.status()["uncertain_api_requests"], 1)

    def test_usage_overrun_records_actual_and_freezes(self):
        self.response["usage"]["completion_tokens"] = 10000
        with patch.object(
            self.ledger, "record_charge", wraps=self.ledger.record_charge
        ) as record:
            with self.assertRaisesRegex(Rejected, "frozen"):
                self.execute()
            self.assertEqual(record.call_count, 1)
            self.assertTrue(record.call_args.args[2].startswith("billing_overrun:"))
            self.assertIn("usage", record.call_args.kwargs)
        self.assertGreater(self.ledger.status()["api_spend_or_reserved_usd"], 0.01)
        self.market["id"] = "different"
        with self.assertRaisesRegex(Rejected, "frozen"):
            self.execute()

    def test_veto_never_opens_position(self):
        self.response["choices"][0]["message"]["content"] = (
            '{"approve":false,"reason":"uncertain"}'
        )
        self.assertEqual(self.execute()["action"], "veto")
        self.assertEqual(self.ledger.status()["open_paper_cost_usd"], 0)
        self.assertGreater(self.ledger.status()["api_spend_or_reserved_usd"], 0)

    def test_model_cannot_inflate_probability_or_quantity(self):
        self.response["choices"][0]["message"]["content"] = (
            '{"approve":true,"reason":"ok","probability":1}'
        )
        with self.assertRaisesRegex(Rejected, "schema"):
            self.execute()

    def test_incomplete_response_fails_closed(self):
        self.response["choices"][0]["finish_reason"] = "length"
        with self.assertRaisesRegex(Rejected, "incomplete"):
            self.execute()

    def test_stale_future_and_wrong_city_forecasts(self):
        for stamp in (self.now - timedelta(hours=3), self.now + timedelta(seconds=1)):
            self.forecast(stamp)
            with self.assertRaisesRegex(Rejected, "fresh forecast"):
                self.execute()
        self.forecast(self.now - timedelta(minutes=1), city="tx")
        with self.assertRaisesRegex(Rejected, "fresh forecast"):
            self.execute()
        self.assertFalse(self.calls)

    def test_timestamp_missing_offset(self):
        with self.assertRaises(Rejected):
            instant("2026-10-25T12:00:00")

    def test_invalid_market_values_before_paid_call(self):
        for field, value in [
            ("yes_ask", float("nan")),
            ("yes_ask", "0.2"),
            ("lower_f", "70"),
            ("upper_f", True),
            ("fee_per_contract", -1),
            ("station", "WRONG"),
            ("rules_verified", False),
            ("date", "2026-10-24"),
        ]:
            market = dict(self.market, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(Rejected):
                validate_market(market, self.now, self.limits)
        self.assertFalse(self.calls)

    def test_stale_and_future_quotes(self):
        for change in (-301, 1):
            self.market["quoted_at"] = (
                self.now + timedelta(seconds=change)
            ).isoformat()
            with self.assertRaises(Rejected):
                self.execute()

    def test_invalid_forecast_spread_or_mean(self):
        for field, val in [
            ("spread_f", "NaN"),
            ("spread_f", 4),
            ("tmax_predicted", "inf"),
        ]:
            self.forecast(self.now - timedelta(minutes=1), **{field: val})
            with self.assertRaises(Rejected):
                self.execute()

    def test_integer_bucket_continuity_and_sigma_floor(self):
        forecast = load_forecast(self.path, self.market, self.now, self.limits)
        self.assertAlmostEqual(probability(forecast, self.market), 0.8542910694693275)
        self.forecast(self.now - timedelta(minutes=1), spread_f=0)
        self.assertEqual(
            load_forecast(self.path, self.market, self.now, self.limits)["sigma_f"], 2
        )

    def test_settlement_atomic_and_no_early_or_duplicate_settle(self):
        self.execute()
        with self.assertRaisesRegex(Rejected, "before"):
            self.ledger.settle(self.market["id"], "yes", "NWS reviewed", self.now)
        status = self.ledger.settle(
            self.market["id"], "yes", "NWS reviewed", self.now + timedelta(hours=3)
        )
        self.assertAlmostEqual(status["realized_paper_profit_usd"], 7.11)
        self.assertEqual(status["open_paper_cost_usd"], 0)
        with self.assertRaisesRegex(Rejected, "already settled"):
            self.ledger.settle(
                self.market["id"], "yes", "NWS reviewed", self.now + timedelta(hours=3)
            )

    def test_paper_profit_cannot_refill_api_funding(self):
        self.execute()
        before = self.ledger.status()["api_budget_remaining_usd"]
        status = self.ledger.settle(
            self.market["id"], "yes", "NWS reviewed", self.now + timedelta(hours=3)
        )
        self.assertEqual(status["api_budget_remaining_usd"], before)

    def test_daily_exposure_and_loss_caps(self):
        self.execute()
        self.market["id"] = "new-market"
        tiny = Limits(api_budget=1, max_stake=2, daily_cap=2, loss_cap=2)
        ledger = Ledger(Path(self.temp.name) / "tiny.sqlite", tiny)
        try:
            run(ledger, self.market, self.path, self.client, self.now)
            self.market["id"] = "newer-market"
            with self.assertRaisesRegex(Rejected, "risk budget"):
                run(ledger, self.market, self.path, self.client, self.now)
        finally:
            ledger.close()

    def test_low_edge_no_paid_request(self):
        self.market["yes_ask"] = 0.95
        with self.assertRaisesRegex(Rejected, "edge"):
            self.execute()
        self.assertFalse(self.calls)

    def test_synthetic_ledger_rejects_real_client(self):
        ledger = Ledger(
            Path(self.temp.name) / "synthetic.sqlite",
            Limits(api_budget=1, synthetic=True),
        )
        try:
            real = DeepSeek("fake", "model", Rates(0.3, 0.006, 1.2))
            with self.assertRaisesRegex(Rejected, "synthetic"):
                run(ledger, self.market, self.path, real, self.now)
        finally:
            ledger.close()

    def test_forecast_expiring_during_paid_review_is_rejected(self):
        self.forecast(self.now - timedelta(seconds=7190))
        self.client.live = True
        with (
            patch(
                "weather_agent.core.utcnow",
                return_value=self.now + timedelta(seconds=30),
            ),
            self.assertRaisesRegex(Rejected, "forecast expired"),
        ):
            self.execute()
        self.assertEqual(self.ledger.status()["open_paper_cost_usd"], 0)
        self.assertGreater(self.ledger.status()["api_spend_or_reserved_usd"], 0)

    def test_review_metadata_reproduces_cost_and_prediction(self):
        self.execute()
        metadata = json.loads(
            self.ledger.connection.execute("SELECT metadata FROM reviews").fetchone()[0]
        )
        self.assertEqual(metadata["model"], "test-model")
        self.assertEqual(metadata["usage"], self.response["usage"])
        self.assertEqual(metadata["forecast"]["mean_f"], 73)
        self.assertIn("run_ts", metadata["forecast"])
        self.assertNotIn("test-key", json.dumps(metadata))

    def test_concurrent_budget_reservations_do_not_overspend(self):
        path = Path(self.temp.name) / "concurrent.sqlite"
        setup = Ledger(path, self.limits)
        setup.close()

        def claim(index):
            ledger = Ledger(path, self.limits)
            try:
                ledger.claim(dict(self.market, id=f"market-{index}"), 0.8, 750000)
                return True
            except Rejected:
                return False
            finally:
                ledger.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(claim, range(2))), 1)
        ledger = Ledger(path)
        try:
            self.assertEqual(ledger.status()["api_spend_or_reserved_usd"], 0.75)
        finally:
            ledger.close()

    def test_rates_reject_invalid(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(Rejected):
                Rates(value, 0.006, 1.2)


if __name__ == "__main__":
    unittest.main()
