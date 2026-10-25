"""Explicit one-shot commands: no scheduler, broker keys, payments or live orders."""

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import DeepSeek, Ledger, Limits, Rates, Rejected, run, utcnow


def demo(path):
    now = utcnow()
    market = {
        "id": "SYNTHETIC-NY-DEMO",
        "city": "ny",
        "date": now.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
        "quoted_at": now.isoformat(),
        "closes_at": (now + timedelta(hours=1)).isoformat(),
        "lower_f": 70,
        "upper_f": 75,
        "yes_ask": 0.2,
        "fee_per_contract": 0.01,
        "station": "KNYC",
        "settlement_source": "NWS_CLI",
        "rules_verified": True,
    }
    limits = Limits(api_budget=1.0, synthetic=True)
    ledger = Ledger(path, limits)
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": '{"approve":true,"reason":"Synthetic offline example, not evidence of profitability"}'
                },
            }
        ],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 30,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 1000,
        },
    }
    client = DeepSeek(
        "not-a-real-key",
        "synthetic-offline",
        Rates(0.3, 0.006, 1.2),
        transport=lambda _: response,
    )
    try:
        with tempfile.TemporaryDirectory() as temp:
            forecast = Path(temp) / "forecast.csv"
            with forecast.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["date", "city", "tmax_predicted", "spread_f", "run_ts"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "date": market["date"],
                        "city": "ny",
                        "tmax_predicted": 73,
                        "spread_f": 2,
                        "run_ts": (now - timedelta(minutes=1)).isoformat(),
                    }
                )
            result = run(ledger, market, forecast, client, now=now)
        return {"synthetic_demo": True, "decision": result, "status": ledger.status()}
    finally:
        ledger.close()


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("demo", "run", "settle", "status"):
        command = commands.add_parser(name)
        command.add_argument(
            "--db",
            required=True,
            help="SQLite ledger path (use a separate demo ledger)",
        )
        if name == "run":
            command.add_argument(
                "--forecast", required=True, help="Weatherbots predictions_history.csv"
            )
            command.add_argument(
                "--market",
                required=True,
                help="Reviewed normalized market snapshot JSON",
            )
            command.add_argument(
                "--api-budget",
                type=float,
                default=0.0,
                help="Real prepaid research USD cap, never replenished by paper P&L",
            )
            command.add_argument("--paper-bankroll", type=float, default=100)
            command.add_argument("--max-stake", type=float, default=2)
            command.add_argument("--daily-cap", type=float, default=10)
            command.add_argument("--loss-cap", type=float, default=10)
        if name == "settle":
            command.add_argument("--market-id", required=True)
            command.add_argument("--outcome", choices=("yes", "no"), required=True)
            command.add_argument(
                "--evidence",
                required=True,
                help="Reviewed NWS CLI outcome citation; manual paper settlement only",
            )
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    ledger = None
    try:
        if args.command == "demo":
            output = demo(args.db)
        elif args.command == "run":
            limits = Limits(
                paper_bankroll=args.paper_bankroll,
                api_budget=args.api_budget,
                max_stake=args.max_stake,
                daily_cap=args.daily_cap,
                loss_cap=args.loss_cap,
            )
            if Path(args.market).stat().st_size > 16_384:
                raise Rejected("market file exceeds 16KiB")
            market = json.loads(Path(args.market).read_text())
            rates = Rates(
                float(os.environ["DEEPSEEK_INPUT_USD_PER_MTOK"]),
                float(os.environ["DEEPSEEK_CACHE_HIT_USD_PER_MTOK"]),
                float(os.environ["DEEPSEEK_OUTPUT_USD_PER_MTOK"]),
            )
            client = DeepSeek(
                os.environ["DEEPSEEK_API_KEY"], os.environ["DEEPSEEK_MODEL"], rates
            )
            ledger = Ledger(args.db, limits)
            output = {
                "decision": run(ledger, market, args.forecast, client),
                "status": ledger.status(),
            }
        else:
            if not Path(args.db).is_file():
                raise Rejected("ledger does not exist")
            ledger = Ledger(args.db)
            output = (
                ledger.status()
                if args.command == "status"
                else ledger.settle(args.market_id, args.outcome, args.evidence)
            )
        print(json.dumps(output, indent=2, allow_nan=False))
        return 0
    except (Rejected, KeyError, OSError, ValueError, TypeError) as exc:
        # Never print raw HTTP bodies, request payloads or credentials.
        message = (
            str(exc)
            if isinstance(exc, Rejected)
            else f"{type(exc).__name__}: invalid configuration, input or provider failure"
        )
        print(
            json.dumps(
                {
                    "error": message,
                    "action": "no_new_paper_position",
                    "note": "If API work began, its reservation remains charged/held; inspect status.",
                }
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
