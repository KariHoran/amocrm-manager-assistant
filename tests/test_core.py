"""Тесты: поиск, порог, fallback, sentiment, вебхук, ошибка LLM."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import fallback, kb
from app.amocrm import extract_webhook_payload
from app.main import app, build_assist

ROOT = Path(__file__).resolve().parent.parent
KB_PATH = ROOT / "data" / "knowledge_base.json"


@pytest.fixture(scope="module")
def knowledge_base() -> list[dict]:
    return kb.load_kb(KB_PATH)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_kb_search_cases_and_stems(knowledge_base: list[dict]) -> None:
    # падежи / словоформы
    results = kb.search("Нужна доставка холодильника", knowledge_base)
    assert any(r["id"] == "delivery" for r in results)

    results = kb.search("Какая гарантия на стиральные машины?", knowledge_base)
    assert any(r["id"] == "warranty" for r in results)

    results = kb.search("оформить рассрочку на двенадцать месяцев", knowledge_base)
    assert any(r["id"] == "payment" for r in results)


def test_kb_search_typo_endings(knowledge_base: list[dict]) -> None:
    # окончания: стеммер должен свести к одному корню
    a = {r["id"] for r in kb.search("доставки за мкад", knowledge_base)}
    b = {r["id"] for r in kb.search("доставку за мкад", knowledge_base)}
    assert "delivery" in a
    assert "delivery" in b


def test_kb_relevance_threshold(knowledge_base: list[dict]) -> None:
    weak = kb.search("просто привет как дела", knowledge_base, threshold=0.18)
    assert weak == []

    strong = kb.search("статус моего заказа где трек", knowledge_base, threshold=0.18)
    assert any(r["id"] == "order_status" for r in strong)


def test_kb_no_match(knowledge_base: list[dict]) -> None:
    assert kb.search("выгул собак и флористика к празднику", knowledge_base) == []


def test_fallback_upsell(knowledge_base: list[dict]) -> None:
    entries = kb.search("доставка и рассрочка", knowledge_base)
    result = fallback.build_fallback("Анна", "доставка и рассрочка", entries)
    assert result["mode"] == "fallback"
    assert "Анна" in result["reply"]
    assert result["matched_ids"]
    assert "•" in result["upsell_tip"]


def test_fallback_angry_customer_no_upsell(knowledge_base: list[dict]) -> None:
    msg = "Это безобразие! Машина сломалась, верните деньги!"
    entries = kb.search(msg, knowledge_base) or kb.search("гарантия сломалась", knowledge_base)
    result = fallback.build_fallback("Игорь", msg, entries)
    assert result["sentiment"] == "negative"
    assert result["intent"] == "complaint"
    assert "деэскалац" in result["upsell_tip"].lower() or "Признать проблему" in result["upsell_tip"]
    assert "Расширенная гарантия" not in result["upsell_tip"]


def test_detect_sentiment_intent() -> None:
    assert fallback.detect_sentiment("Какой ужас и безобразие") == "negative"
    assert fallback.detect_intent("Верните деньги, это претензия") == "complaint"
    assert fallback.detect_intent("Хочу купить холодильник") == "buy"


def test_webhook_json_and_form_extract() -> None:
    json_payload = {
        "message": "Какая гарантия?",
        "client_name": "Олег",
        "lead_id": "42",
    }
    parsed = extract_webhook_payload(json_payload)
    assert parsed["message"] == "Какая гарантия?"
    assert parsed["client_name"] == "Олег"
    assert parsed["lead_id"] == 42

    form_payload = {
        "message[add][0][text]": "Нужна установка",
        "message[add][0][author_name]": "Мария",
        "leads[status][0][id]": "99",
    }
    parsed_form = extract_webhook_payload(form_payload)
    assert parsed_form["message"] == "Нужна установка"
    assert parsed_form["client_name"] == "Мария"
    assert parsed_form["lead_id"] == 99


def test_api_assist_fallback(client: TestClient) -> None:
    resp = client.post(
        "/api/assist",
        json={"client_name": "Анна", "message": "Сколько стоит доставка?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] in ("fallback", "llm")
    assert "reply" in data
    assert "matched_ids" in data


def test_api_webhook_json(client: TestClient) -> None:
    resp = client.post(
        "/webhook/amocrm",
        json={"message": "Какая гарантия?", "client_name": "Игорь", "lead_id": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["note_scheduled"] is True


def test_api_webhook_form(client: TestClient) -> None:
    resp = client.post(
        "/webhook/amocrm",
        data={
            "message": "Нужна рассрочка",
            "client_name": "Лена",
            "lead_id": "7",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_llm_error_uses_fallback(knowledge_base: list[dict]) -> None:
    entries = kb.search("доставка холодильника", knowledge_base)
    assert entries
    with patch("app.llm.generate_with_llm", side_effect=RuntimeError("boom")):
        # build_assist ловит ошибку внутри — патчим на уровне вызова
        with patch("app.main.llm.generate_with_llm", side_effect=RuntimeError("boom")):
            result = build_assist("Анна", "доставка холодильника")
    assert result["mode"] == "fallback"
    assert result["reply"]


def test_llm_module_retries_then_raises() -> None:
    from app import llm as llm_mod

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("api down")

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "LLM_MODEL": "test-model"}):
        with patch("app.llm.Anthropic", return_value=fake_client):
            with pytest.raises(RuntimeError):
                llm_mod.generate_with_llm("Анна", "доставка", [{"id": "delivery", "topic": "Доставка", "answer": "x", "upsell": []}])
    assert fake_client.messages.create.call_count == 2  # 1 + retry
