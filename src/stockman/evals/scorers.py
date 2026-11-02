"""Deterministic, independently specified evaluation checks.

Passing these checks is NOT proof of semantic correctness or investing quality.
The live evaluation report explicitly marks that limitation for human review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Score:
    passed: bool
    checks: dict[str, bool]
    needs_human_review: bool

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "needs_human_review": self.needs_human_review,
        }


def evidence_for_case(case: dict):
    """Construct real production Evidence objects; aliases never replace their IDs."""
    from stockman.briefing.models import Evidence

    now = float(case["now"])
    evidence = []
    aliases = {}
    for item in case.get("evidence", []):
        row = Evidence(
            source=item["source"],
            title=item["title"],
            url=item["url"],
            published=float(item["published"]),
            fetched=float(item.get("fetched", now)),
            body=item.get("body", ""),
            entities=tuple(item.get("entities", [])),
            event_key=item.get("event_key", ""),
        )
        row.validate(now)
        if item["alias"] in aliases:
            raise ValueError("duplicate evidence alias")
        aliases[item["alias"]] = row.identity
        evidence.append(row)
    return evidence, aliases


def _normalized_output(output: Any, aliases: dict[str, str]) -> Any:
    """Only fixture aliases are expanded. Invented IDs stay invented."""
    if not isinstance(output, dict) or not isinstance(output.get("events"), list):
        return output
    rows = []
    for event in output["events"]:
        if isinstance(event, dict):
            row = dict(event)
            if isinstance(row.get("id"), str):
                row["id"] = aliases.get(row["id"], row["id"])
            rows.append(row)
        else:
            rows.append(event)
    return {**output, "events": rows}


def _subsequence(required: list[str], actual: list[str]) -> bool:
    iterator = iter(actual)
    return all(any(value == name for value in iterator) for name in required)


def evaluate_case(case: dict, observation: dict) -> Score:
    """Score a recorded output or a live run using the same invariant checks.

    Semantic entailment is deliberately NOT inferred from substring matches.
    """
    expected = case["expect"]
    checks: dict[str, bool] = {}
    kind = case["kind"]
    if kind == "daily":
        try:
            from stockman.stock_briefing.agent import validate_output

            items, aliases = evidence_for_case(case)
            normalized = _normalized_output(observation.get("output"), aliases)
            rows = validate_output(json.dumps(normalized, ensure_ascii=False), items, 5)
            checks["valid_citations_and_schema"] = True
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            rows = []
            aliases = {}
            checks["valid_citations_and_schema"] = False
        checks["event_count"] = (
            int(expected.get("min_events", 0)) <= len(rows)
            <= int(expected.get("max_events", 5))
        )
        required_ids = [aliases.get(alias, alias) for alias in expected.get("required_ids", [])]
        checks["required_evidence_ids"] = all(row["id"] in {r["id"] for r in rows} for row in required_ids)
        rendered = json.dumps(observation.get("output", {}), ensure_ascii=False)
    elif kind == "agent":
        rendered = str(observation.get("output", ""))
        actual = observation.get("tools", [])
        checks["tool_trace_present"] = isinstance(actual, list) and all(isinstance(x, str) for x in actual)
        checks["tool_order"] = _subsequence(expected.get("required_tools", []), actual) if checks["tool_trace_present"] else False
        checks["forbidden_tools"] = not any(name in actual for name in expected.get("forbidden_tools", [])) if checks["tool_trace_present"] else False
    else:
        raise ValueError("unsupported evaluation kind")

    checks["required_text"] = all(
        any(term in rendered for term in group)
        for group in expected.get("contains_any", [])
    )
    checks["forbidden_text"] = all(term not in rendered for term in expected.get("forbidden_text", []))
    allowed = expected.get("statuses")
    if allowed is not None:
        checks["status"] = observation.get("status") in allowed
    if "max_tools" in expected:
        checks["tool_limit"] = observation.get("tool_calls", 0) <= expected["max_tools"]
    if "max_models" in expected:
        checks["model_limit"] = observation.get("model_calls", 0) <= expected["max_models"]
    # A source quote can match text while a model's interpretation is wrong.
    # Human judgement/curated semantic labels are required for that dimension.
    return Score(
        passed=all(checks.values()),
        checks=checks,
        needs_human_review=bool(case.get("semantic_review", kind == "daily")),
    )
