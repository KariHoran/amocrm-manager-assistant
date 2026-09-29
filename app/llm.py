"""Генерация ответа через Anthropic tool use."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from anthropic import Anthropic
from pydantic import ValidationError

from app.models import ComposeAnswerArgs, HistoryItem
from app.prompts import COMPOSE_ANSWER_TOOL, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SEC = 15.0
MAX_RETRIES = 1


def _api_key() -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return key or None


def _model() -> str:
    model = os.getenv("LLM_MODEL", "").strip()
    if not model:
        raise RuntimeError("LLM_MODEL не задан")
    return model


def _format_history(history: list[HistoryItem] | list[dict[str, Any]]) -> str:
    if not history:
        return "нет"
    lines: list[str] = []
    for item in history[-6:]:
        if isinstance(item, HistoryItem):
            role, text = item.role, item.text
        else:
            role, text = str(item.get("role", "client")), str(item.get("text", ""))
        lines.append(f"- {role}: {text}")
    return "\n".join(lines)


def _format_kb(entries: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for entry in entries:
        upsell_lines = []
        for up in entry.get("upsell") or []:
            upsell_lines.append(
                f"  - idea: {up.get('idea')}; trigger: {up.get('trigger')}; "
                f"script: {up.get('script')}"
            )
        avoid = ", ".join(entry.get("avoid_upsell_if") or []) or "—"
        blocks.append(
            f"id: {entry.get('id')}\n"
            f"Тема: {entry.get('topic')}\n"
            f"Факты: {entry.get('answer')}\n"
            f"Идеи допродаж:\n" + ("\n".join(upsell_lines) or "  —") + "\n"
            f"avoid_upsell_if: {avoid}"
        )
    return "\n\n".join(blocks)


def build_user_prompt(
    client_name: str,
    message: str,
    entries: list[dict[str, Any]],
    history: list[HistoryItem] | list[dict[str, Any]] | None = None,
) -> str:
    name = (client_name or "").strip() or "клиент"
    return (
        f"Имя клиента: {name}\n"
        f"Текущее сообщение клиента: {message}\n\n"
        f"История диалога (до 6 реплик):\n{_format_history(history or [])}\n\n"
        f"Фрагменты базы знаний (единственный источник фактов):\n{_format_kb(entries)}"
    )


def _extract_tool_args(response: Any) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "compose_answer":
            raw = getattr(block, "input", None) or {}
            if not isinstance(raw, dict):
                raise RuntimeError("tool input не dict")
            return raw
    raise RuntimeError("LLM не вызвала tool compose_answer")


def _call_once(client: Anthropic, user_prompt: str) -> dict[str, Any]:
    response = client.messages.create(
        model=_model(),
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        tools=[COMPOSE_ANSWER_TOOL],
        tool_choice={"type": "tool", "name": "compose_answer"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw_args = _extract_tool_args(response)
    try:
        parsed = ComposeAnswerArgs.model_validate(raw_args)
    except ValidationError as exc:
        raise RuntimeError("Невалидные аргументы tool") from exc
    return parsed.model_dump()


def generate_with_llm(
    client_name: str,
    message: str,
    entries: list[dict[str, Any]],
    history: list[HistoryItem] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Сгенерировать ответ через tool use. Ретрай 1 раз при ошибке."""
    key = _api_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан")

    client = Anthropic(api_key=key, timeout=LLM_TIMEOUT_SEC)
    user_prompt = build_user_prompt(client_name, message, entries, history)

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            parsed = _call_once(client, user_prompt)
            if not parsed.get("reply") or not parsed.get("upsell_tip"):
                raise RuntimeError("Пустой reply/upsell_tip от LLM")
            parsed["mode"] = "llm"
            parsed["matched_ids"] = [str(e.get("id", "")) for e in entries if e.get("id")]
            if not parsed.get("topics"):
                parsed["topics"] = [str(e.get("topic", "")) for e in entries if e.get("topic")]
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "LLM attempt=%s failed error=%s",
                attempt + 1,
                type(exc).__name__,
            )
            if attempt < MAX_RETRIES:
                time.sleep(0.4)

    raise RuntimeError(f"Ошибка LLM: {type(last_error).__name__}") from last_error
