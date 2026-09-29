"""FastAPI: ассистент менеджера для AmoCRM."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import amocrm, fallback, kb, llm
from app.models import AssistRequest, AssistResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Ассистент менеджера AmoCRM", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def build_assist(
    client_name: str,
    message: str,
    history: list[Any] | None = None,
) -> dict[str, Any]:
    """Поиск БЗ → LLM или fallback / no_match. Никогда не бросает из-за LLM."""
    logger.info("assist request message_len=%s history_len=%s", len(message or ""), len(history or []))

    entries = kb.search(message)
    if not entries:
        result = fallback.no_match_response(client_name, message)
        logger.info("assist mode=%s", result["mode"])
        return result

    try:
        result = llm.generate_with_llm(client_name, message, entries, history=history)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM unavailable → fallback: %s", type(exc).__name__)
        result = fallback.build_fallback(client_name, message, entries)

    logger.info(
        "assist mode=%s sentiment=%s intent=%s ids=%s",
        result.get("mode"),
        result.get("sentiment"),
        result.get("intent"),
        result.get("matched_ids"),
    )
    return result


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error type=%s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервиса"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/assist", response_model=AssistResponse)
def api_assist(body: AssistRequest) -> AssistResponse:
    result = build_assist(body.client_name, body.message, history=body.history)
    return AssistResponse(**result)


@app.get("/api/kb")
def get_kb() -> list[dict[str, Any]]:
    return kb.load_kb()


@app.put("/api/kb")
def put_kb(entries: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        validated = kb.save_kb(entries)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("kb updated entries=%s", len(validated))
    return {"ok": True, "count": len(validated)}


@app.post("/webhook/amocrm")
async def webhook_amocrm(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        raw = payload if isinstance(payload, dict) else {"message": str(payload)}
    else:
        form = await request.form()
        raw = {k: form.get(k) for k in form.keys()}

    extracted = amocrm.extract_webhook_payload(raw)
    message = extracted["message"]
    client_name = extracted["client_name"]
    lead_id = extracted["lead_id"]

    logger.info(
        "amocrm webhook message_len=%s has_lead=%s",
        len(message or ""),
        lead_id is not None,
    )

    if not message:
        return {
            "ok": False,
            "error": "Не удалось извлечь текст сообщения",
            "note_scheduled": False,
            "reply": "",
            "upsell_tip": "",
            "topics": [],
            "sentiment": "neutral",
            "intent": "other",
            "confidence": 0.0,
            "mode": "no_match",
            "matched_ids": [],
        }

    result = build_assist(client_name, message)
    note_scheduled = False
    if lead_id is not None:
        background_tasks.add_task(amocrm.post_note_background, lead_id, result)
        note_scheduled = True
    else:
        logger.warning("amocrm webhook: lead_id missing, skip note")

    return {"ok": True, "lead_id": lead_id, "note_scheduled": note_scheduled, **result}
