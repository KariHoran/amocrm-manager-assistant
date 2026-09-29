"""Интеграция с AmoCRM: разбор вебхука и запись примечания."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _amo_credentials() -> tuple[str | None, str | None]:
    subdomain = os.getenv("AMO_SUBDOMAIN", "").strip() or None
    token = os.getenv("AMO_TOKEN", "").strip() or None
    return subdomain, token


def _dig(data: Any, *keys: str) -> Any:
    cur = data
    for key in keys:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and cur:
            cur = cur[0]
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                return None
        else:
            return None
    return cur


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def extract_webhook_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Извлечь текст, имя контакта и id сделки из вебхука."""
    message = _first_str(
        data.get("message"),
        data.get("text"),
        data.get("msg"),
        _dig(data, "message", "add", "text"),
        _dig(data, "message", "add", "message"),
        data.get("message[add][0][text]"),
        data.get("message[add][0][message]"),
    )
    client_name = _first_str(
        data.get("client_name"),
        data.get("contact_name"),
        data.get("name"),
        _dig(data, "contacts", "name"),
        _dig(data, "contact", "name"),
        data.get("contacts[name]"),
        data.get("message[add][0][author_name]"),
    ) or "Клиент"

    lead_raw = _first_str(
        data.get("lead_id"),
        data.get("leads_id"),
        data.get("entity_id"),
        _dig(data, "leads", "id"),
        _dig(data, "leads", "status", "id"),
        _dig(data, "message", "add", "entity_id"),
        data.get("leads[status][0][id]"),
        data.get("leads[add][0][id]"),
        data.get("message[add][0][element_id]"),
    )
    lead_id: int | None = None
    if lead_raw and str(lead_raw).isdigit():
        lead_id = int(lead_raw)

    return {
        "message": message or "",
        "client_name": client_name,
        "lead_id": lead_id,
    }


def add_note_to_lead(lead_id: int, text: str) -> bool:
    """Добавить примечание к сделке через API v4."""
    subdomain, token = _amo_credentials()
    if not subdomain or not token:
        logger.warning("AmoCRM credentials missing — skip note lead_id=%s", lead_id)
        return False

    url = f"https://{subdomain}.amocrm.ru/api/v4/leads/{lead_id}/notes"
    payload = [{"note_type": "common", "params": {"text": text}}]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        logger.info("AmoCRM note added lead_id=%s", lead_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("AmoCRM note failed lead_id=%s error=%s", lead_id, type(exc).__name__)
        return False


def format_manager_note(result: dict[str, Any]) -> str:
    topics = ", ".join(result.get("topics") or []) or "—"
    return (
        f"[Ассистент | mode={result.get('mode')} | "
        f"sentiment={result.get('sentiment')} | intent={result.get('intent')} | "
        f"confidence={result.get('confidence')} | темы: {topics}]\n\n"
        f"Подсказка менеджеру:\n{result.get('upsell_tip', '')}\n\n"
        f"Черновик ответа клиенту:\n{result.get('reply', '')}"
    )


def post_note_background(lead_id: int, result: dict[str, Any]) -> None:
    """Тяжёлая часть для BackgroundTasks: записать upsell_tip в сделку."""
    tip = result.get("upsell_tip") or ""
    if not tip:
        logger.info("Empty upsell_tip — skip note lead_id=%s", lead_id)
        return
    note = format_manager_note(result)
    add_note_to_lead(lead_id, note)
