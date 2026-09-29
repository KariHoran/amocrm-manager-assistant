"""Офлайн-оценка: 10 кейсов → таблица PASS/FAIL."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fallback, kb  # noqa: E402


def _is_deescalation(tip: str) -> bool:
    low = tip.lower()
    return "признать проблему" in tip or "деэскалац" in low or "не предлагать допродажи" in low


def evaluate_case(case: dict, knowledge: list[dict]) -> tuple[bool, str, dict]:
    message = case["message"]
    entries = kb.search(message, knowledge)
    result = (
        fallback.build_fallback("Клиент", message, entries)
        if entries
        else fallback.no_match_response("Клиент", message)
    )

    got_topics = set(result.get("topics") or [])
    expected = set(case.get("expected_topics") or [])

    if not expected:
        topics_ok = result.get("mode") == "no_match" and not got_topics
    else:
        topics_ok = bool(expected & got_topics)

    expect_upsell = bool(case.get("expect_upsell"))
    tip = result.get("upsell_tip") or ""
    if expect_upsell:
        upsell_ok = "•" in tip and not _is_deescalation(tip)
    else:
        # либо no_match-подсказка, либо деэскалация
        upsell_ok = _is_deescalation(tip) or result.get("mode") == "no_match"

    ok = topics_ok and upsell_ok
    detail = (
        f"topics={sorted(got_topics)} mode={result.get('mode')} "
        f"sentiment={result.get('sentiment')} intent={result.get('intent')}"
    )
    return ok, detail, result


def main() -> int:
    cases_path = Path(__file__).resolve().parent / "cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    knowledge = kb.load_kb()

    print(f"{'ID':<6} {'RESULT':<6} {'EXPECT_UPSELL':<14} DETAIL")
    print("-" * 100)

    passed = 0
    for case in cases:
        ok, detail, _ = evaluate_case(case, knowledge)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(
            f"{case['id']:<6} {status:<6} {str(case.get('expect_upsell')):<14} {detail}"
        )

    total = len(cases)
    pct = round(100.0 * passed / total, 1) if total else 0.0
    print("-" * 100)
    print(f"Итого: {passed}/{total} = {pct}%")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
