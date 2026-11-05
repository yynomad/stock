"""Versioned synthetic golden-case loader; no credentials or account snapshots."""
from __future__ import annotations

import json
from pathlib import Path


def load_suite(path: str | Path) -> tuple[str, list[dict]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != "1":
        raise ValueError("unknown evaluation suite version")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation suite must contain cases")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError("invalid evaluation case")
        if not re_case_id(case["id"]) or case["id"] in ids:
            raise ValueError("invalid or repeated case id")
        ids.add(case["id"])
        if case.get("kind") not in ("daily", "agent"):
            raise ValueError("invalid case kind")
        if not isinstance(case.get("request"), str) or not case["request"].strip():
            raise ValueError("case request required")
        if not isinstance(case.get("expect"), dict):
            raise ValueError("case expectations required")
        if not isinstance(case.get("replay"), dict) or "output" not in case["replay"]:
            raise ValueError("recorded replay output required")
        if type(case.get("expected_replay_pass")) is not bool:
            raise ValueError("replay oracle required")
        if type(case.get("live")) is not bool:
            raise ValueError("live applicability required")
        if case["kind"] == "daily":
            if not isinstance(case.get("evidence"), list) or "now" not in case:
                raise ValueError("daily case requires fixed evidence and time")
        if case["kind"] == "agent" and not isinstance(case.get("tool_results"), dict):
            raise ValueError("agent case requires fixed tool results")
    return data["version"], cases


def re_case_id(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"[a-z][a-z0-9-]{2,79}", value))
