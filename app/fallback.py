"""Шаблонная генерация без LLM: ответ, допродажи, sentiment/intent."""

from __future__ import annotations

from typing import Any

from app import kb
from app.models import Intent, Sentiment

NEGATIVE_MARKERS = (
    "ужас",
    "безобразие",
    "отвратительно",
    "возмущен",
    "возмущена",
    "верните деньги",
    "требую",
    "жалоб",
    "претензи",
    "обман",
    "развод",
    "надоело",
    "достали",
    "хамств",
    "недоволен",
    "недовольна",
    "ужасн",
    "кошмар",
    "вранье",
    "плохо",
    "ненавиж",
)

COMPLAINT_MARKERS = (
    "жалоб", "претензи", "верните", "возврат денег", "роспотреб",
    "сломал", "не работает", "брак", "не привезли", "задерж", "потеряли",
)

BUY_MARKERS = (
    "хочу купить", "оформить", "заказать", "возьму", "беру", "купить",
    "оплат", "рассрочк", "в корзину",
)

POSITIVE_MARKERS = (
    "спасибо", "отлично", "супер", "замечательно", "хорошо", "благодар",
)


def detect_sentiment(message: str) -> Sentiment:
    text = kb.normalize(message)
    if any(m in text for m in NEGATIVE_MARKERS):
        return "negative"
    if any(m in text for m in POSITIVE_MARKERS):
        return "positive"
    return "neutral"


def detect_intent(message: str) -> Intent:
    text = kb.normalize(message)
    if any(m in text for m in COMPLAINT_MARKERS):
        return "complaint"
    if any(m in text for m in BUY_MARKERS):
        return "buy"
    if "?" in message or any(
        w in text for w in ("как", "сколько", "когда", "можно ли", "подскажите", "есть ли")
    ):
        return "question"
    return "other"


def _greeting(client_name: str) -> str:
    name = (client_name or "").strip()
    return f"{name}, здравствуйте!" if name else "Здравствуйте!"


def no_match_response(client_name: str, message: str = "") -> dict[str, Any]:
    sentiment = detect_sentiment(message) if message else "neutral"
    intent = detect_intent(message) if message else "other"
    reply = (
        f"{_greeting(client_name)} Спасибо за обращение. "
        "По этому вопросу мне нужно уточнить информацию у специалиста — "
        "мы вернёмся к вам в ближайшее время с точным ответом."
    )
    upsell_tip = (
        "• Передайте вопрос профильному специалисту\n"
        "• Задайте клиенту уточняющий вопрос: бюджет и задача\n\n"
        "«Подскажите, пожалуйста, какой у вас бюджет и какую задачу "
        "хотите решить — я уточню у специалиста и вернусь с ответом.»"
    )
    return {
        "reply": reply,
        "upsell_tip": upsell_tip,
        "topics": [],
        "sentiment": sentiment,
        "intent": intent,
        "confidence": 0.35,
        "mode": "no_match",
        "matched_ids": [],
    }


def _deescalation_tip() -> str:
    return (
        "• Признать проблему и извиниться за неудобства\n"
        "• Предложить конкретное решение (замена / ремонт / возврат / приоритетный слот)\n"
        "• Назвать срок ответа или следующего шага (сегодня / 1 рабочий день)\n"
        "• Не предлагать допродажи, пока проблема не закрыта\n\n"
        "«Понимаю ваше раздражение — это неприятно. Давайте зафиксируем проблему "
        "и в течение рабочего дня вернусь с решением.»"
    )


def _upsell_tip_from_entries(
    message: str,
    entries: list[dict[str, Any]],
    *,
    avoid: bool,
    confidence: float,
) -> str:
    if avoid:
        tip = _deescalation_tip()
    else:
        items: list[tuple[str, str]] = []
        for entry in entries:
            for up in entry.get("upsell") or []:
                idea = str(up.get("idea", "")).strip()
                script = str(up.get("script", "")).strip()
                if idea:
                    items.append((idea, script))
                if len(items) >= 4:
                    break
            if len(items) >= 4:
                break
        bullets = "\n".join(f"• {idea}" for idea, _ in items[:4])
        script = next((s for _, s in items if s), "Могу предложить удобный вариант в комплекте — рассказать?")
        tip = f"{bullets}\n\n«{script}»" if bullets else f"«{script}»"

    if confidence < 0.6:
        tip += "\n\n⚠ проверить перед отправкой"
    _ = message
    return tip


def build_fallback(
    client_name: str,
    message: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    if not entries:
        return no_match_response(client_name, message)

    sentiment = detect_sentiment(message)
    intent = detect_intent(message)
    avoid = (
        sentiment == "negative"
        or intent == "complaint"
        or kb.should_avoid_upsell(message, entries)
    )
    confidence = 0.55 if len(entries) == 1 else 0.62

    topics = [str(e.get("topic", "")) for e in entries if e.get("topic")]
    matched_ids = [str(e.get("id", "")) for e in entries if e.get("id")]
    facts = " ".join(str(e.get("answer", "")).strip() for e in entries if e.get("answer"))

    if avoid and (sentiment == "negative" or intent == "complaint"):
        reply = (
            f"{_greeting(client_name)} Понимаю вашу ситуацию и сожалею о неудобствах. "
            f"{facts} "
            "Давайте зафиксируем детали — я передам приоритетно и вернусь с решением в ближайшее время."
        )
    else:
        reply = (
            f"{_greeting(client_name)} По вашему вопросу могу сообщить следующее. "
            f"{facts} "
            "Подскажите, пожалуйста, модель или адрес — уточню детали под ваш случай."
        )

    return {
        "reply": reply,
        "upsell_tip": _upsell_tip_from_entries(
            message, entries, avoid=avoid, confidence=confidence
        ),
        "topics": topics,
        "sentiment": sentiment,
        "intent": intent,
        "confidence": confidence,
        "mode": "fallback",
        "matched_ids": matched_ids,
    }
