"""Isolated synthetic replay and opt-in real-model evaluation runner."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from .dataset import load_suite
from .scorers import evaluate_case, evidence_for_case
from stockman.model_providers import provider_of


DEFAULT_SUITE = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "v1.json"


def replay(case: dict) -> dict:
    """Replay recorded *synthetic* output, without an LLM or any network I/O."""
    record = dict(case["replay"])
    return {
        "output": record["output"],
        "tools": record.get("tools", []),
        "status": record.get("status", "completed"),
        "model_calls": record.get("model_calls", 0),
        "tool_calls": record.get("tool_calls", len(record.get("tools", []))),
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost_usd": None,
        "cost_basis": "not_measured_replay",
    }


def _live_daily(case: dict, model, directory: Path) -> dict:
    """Run the production daily research role using fixed synthetic evidence."""
    from stockman.briefing.budget import Budget
    from stockman.briefing.config import BriefingConfig
    from stockman.storage import Store
    from stockman.stock_briefing.agent import ResearchAgent

    name = getattr(model,"model",None) or getattr(model,"model_name",None)
    config = BriefingConfig(budget=2.0,max_calls=4,max_events=5,price_model=name)
    store = Store(directory / "daily")
    try:
        items, aliases = evidence_for_case(case)
        task = store.create_task("eval", case["request"])
        budget = Budget(store, config)
        rows, status = ResearchAgent(store, config, model=model).explain(
            task, items, budget, float(case["now"])
        )
        reverse = {identity: alias for alias, identity in aliases.items()}
        # The scorer expands aliases back to the real Evidence.identity.
        output = {"events": [{**row, "id": reverse.get(row["id"], row["id"])} for row in rows]}
        budget_rows = store.execute("SELECT state,input_tokens,output_tokens FROM brief_budget")
        known = all(row["state"] == "usage_reported" for row in budget_rows) and bool(budget_rows)
        day = datetime.fromtimestamp(float(case["now"]), timezone.utc).date().isoformat()
        # Day keys use config.timezone, not UTC.
        from zoneinfo import ZoneInfo
        day = datetime.fromtimestamp(float(case["now"]), ZoneInfo(config.timezone)).date().isoformat()
        bill = budget.status(day)
        return {
            "output": output,
            "tools": [],
            "status": status,
            "model_calls": bill["calls"],
            "tool_calls": 0,
            "input_tokens": sum(r["input_tokens"] for r in budget_rows) if known else None,
            "output_tokens": sum(r["output_tokens"] for r in budget_rows) if known else None,
            "estimated_cost_usd": bill["estimated_usd"],
            "cost_basis": "rate_estimate" if known else "reservation_upper_bound_unknown_usage",
        }
    finally:
        store.close()


def _live_agent(case: dict, model, directory: Path) -> dict:
    """Use the actual Supervisor + child agents, but NO external tools."""
    from stockman.agents import AgentRuntime

    def canned(role):
        def tool(request: str):
            return json.dumps(case["tool_results"].get(role, {
                "status": "unavailable", "missing": "fixed test tool has no data"
            }), ensure_ascii=False)
        return tool

    services = {role: canned(role) for role in ("portfolio", "research", "monitor")}
    runtime = AgentRuntime(directory, model=model, services=services)
    try:
        result = runtime.run(case["request"], session="eval-" + case["id"])
        traces = runtime.store.traces(result["task_id"])
        tool_names = [
            row["tool"] for row in traces
            if row["phase"] == "start" and row["tool"] != "model"
        ]
        return {
            "output": result["text"],
            "tools": tool_names,
            "status": result["status"],
            "model_calls": result["model_calls"],
            "tool_calls": result["tool_calls"],
            "input_tokens": None,
            "output_tokens": None,
            "estimated_cost_usd": None,
            "cost_basis": "unknown_on_demand_usage_issue_17",
        }
    finally:
        runtime.close()


def live(case: dict, model) -> dict:
    """Calls a real LLM; its only available business tools return fixed test data."""
    if os.getenv("STOCKMAN_OFFLINE") != "1" or not os.getenv("STOCKMAN_TEST_PG_SOCKET"):
        raise ValueError("run synthetic live evaluation with scripts/test_postgres.py")
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="stockman-eval-") as temporary:
        directory = Path(temporary)
        try:
            if case["kind"] == "daily":
                result = _live_daily(case, model, directory)
            else:
                result = _live_agent(case, model, directory)
        except Exception as exc:
            # Never include provider exception messages: they may contain secrets.
            result = {
                "output": {"events": []} if case["kind"] == "daily" else "",
                "tools": [],
                "status": "failed",
                "model_calls": None,
                "tool_calls": None,
                "input_tokens": None,
                "output_tokens": None,
                "estimated_cost_usd": None,
                "cost_basis": "unknown_failed",
                "error_type": type(exc).__name__,
            }
    result["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return result


def create_model(name: str | None):
    from stockman.model_providers import ModelEndpoint
    return ModelEndpoint.from_environment(name=name).create()


def execute(
    suite_path: str | Path = DEFAULT_SUITE,
    mode: str = "replay",
    model_name: str | None = None,
    max_cases: int = 5,
    repeats: int = 1,
    selected_ids: set[str] | None = None,
) -> dict:
    if mode not in ("replay", "live"):
        raise ValueError("mode must be replay or live")
    if not 1 <= max_cases <= 100 or not 1 <= repeats <= 3:
        raise ValueError("max_cases must be 1..100 and repeats 1..3")
    suite_version, cases = load_suite(suite_path)
    if selected_ids:
        unknown = selected_ids - {case["id"] for case in cases}
        if unknown:
            raise ValueError("unknown case id(s): " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case["id"] in selected_ids]
    if mode == "live":
        cases = [case for case in cases if case["live"]][:max_cases]
        model = create_model(model_name) if cases else None
    else:
        model = None
    results = []
    for case in cases:
        for attempt in range(repeats if mode == "live" else 1):
            observation = live(case, model) if mode == "live" else replay(case)
            score = evaluate_case(case, observation)
            outcome = {
                "case_id": case["id"],
                "category": case["category"],
                "attempt": attempt + 1,
                "score": score.as_dict(),
                "status": observation.get("status"),
                "tools": observation.get("tools", []),
                "model_calls": observation.get("model_calls"),
                "tool_calls": observation.get("tool_calls"),
                "latency_ms": observation.get("latency_ms"),
                "input_tokens": observation.get("input_tokens"),
                "output_tokens": observation.get("output_tokens"),
                "estimated_cost_usd": observation.get("estimated_cost_usd"),
                "cost_basis": observation.get("cost_basis"),
            }
            if observation.get("error_type"):
                outcome["error_type"] = observation["error_type"]
            if mode == "replay":
                outcome["replay_expected_pass"] = case["expected_replay_pass"]
                outcome["replay_oracle_matches"] = score.passed == case["expected_replay_pass"]
            results.append(outcome)

    summary = {
        "cases": len(cases),
        "runs": len(results),
        "automated_checks_passed": sum(row["score"]["passed"] for row in results),
        "needs_human_review": sum(row["score"]["needs_human_review"] for row in results),
        "latency_measured_runs": sum(row["latency_ms"] is not None for row in results),
        "usage_known_runs": sum(row["input_tokens"] is not None for row in results),
    }
    if mode == "replay":
        summary["replay_oracles_matched"] = sum(row["replay_oracle_matches"] for row in results)
    return {
        "run_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "suite_version": suite_version,
        "mode": mode,
        "provenance": "synthetic_recording_no_llm" if mode == "replay" else "real_model_fixed_synthetic_tools",
        "provider": provider_of(model),
        "model": None if mode == "replay" else model_name or "configured_default",
        "semantic_accuracy": "NOT_ASSESSED_requires_human_or_independent_annotated_judge",
        "summary": summary,
        "results": results,
    }


def compare(baseline: dict, current: dict) -> dict:
    """Never compare replay correctness with actual model quality."""
    if baseline["suite_version"] != current["suite_version"] or baseline["mode"] != current["mode"]:
        raise ValueError("cannot compare different suites or replay/live modes")
    def keyed(report):
        return {
            (row["case_id"], row["attempt"]): row["score"]["passed"]
            for row in report["results"]
        }
    old, new = keyed(baseline), keyed(current)
    common = sorted(old.keys() & new.keys())
    return {
        "mode": current["mode"],
        "baseline_provider": baseline.get("provider"),
        "current_provider": current.get("provider"),
        "baseline_model": baseline.get("model"),
        "current_model": current.get("model"),
        "common_runs": len(common),
        "regressions": [list(key) for key in common if old[key] and not new[key]],
        "improvements": [list(key) for key in common if not old[key] and new[key]],
        "missing_from_current": [list(key) for key in sorted(old.keys() - new.keys())],
        "semantic_accuracy": "NOT_ASSESSED",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="StockMan isolated evaluation; no real broker tools")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--suite", default=str(DEFAULT_SUITE))
    run.add_argument("--mode", choices=["replay", "live"], default="replay")
    run.add_argument("--model", help="Model name; live only")
    run.add_argument("--max-cases", type=int, default=5, help="maximum live cases (default 5)")
    run.add_argument("--repeats", type=int, default=1, help="1..3 real-model repetitions")
    run.add_argument("--case", action="append", help="repeat to select particular case IDs")
    run.add_argument("--output", help="optional JSON report path; otherwise stdout")
    diff = commands.add_parser("compare")
    diff.add_argument("--baseline", required=True)
    diff.add_argument("--current", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            report = compare(
                json.loads(Path(args.baseline).read_text(encoding="utf-8")),
                json.loads(Path(args.current).read_text(encoding="utf-8")),
            )
        else:
            report = execute(
                args.suite, args.mode, args.model,
                args.max_cases, args.repeats,
                set(args.case) if args.case else None,
            )
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if args.command == "run" and args.output:
            location = Path(args.output)
            location.parent.mkdir(parents=True, exist_ok=True)
            location.write_text(payload + "\n", encoding="utf-8")
            print("Evaluation report:", location)
        else:
            print(payload)
        if args.command == "run" and args.mode == "replay":
            return 0 if report["summary"]["replay_oracles_matched"] == report["summary"]["runs"] else 1
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
