"""Pydantic-схемы запросов и ответов."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Sentiment = Literal["neutral", "positive", "negative"]
Intent = Literal["buy", "question", "complaint", "other"]
Mode = Literal["llm", "fallback", "no_match"]


class HistoryItem(BaseModel):
    role: Literal["client", "manager", "assistant", "user", "system"] = "client"
    text: str = Field(..., min_length=1)


class AssistRequest(BaseModel):
    client_name: str = Field(default="", description="Имя клиента")
    message: str = Field(..., min_length=1, description="Текст обращения")
    history: list[HistoryItem] = Field(default_factory=list, description="До 6 последних реплик")

    @field_validator("history")
    @classmethod
    def limit_history(cls, value: list[HistoryItem]) -> list[HistoryItem]:
        return value[-6:]


class AssistResponse(BaseModel):
    reply: str
    upsell_tip: str
    topics: list[str]
    sentiment: Sentiment = "neutral"
    intent: Intent = "other"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    mode: Mode
    matched_ids: list[str] = Field(default_factory=list)


class UpsellItem(BaseModel):
    idea: str
    trigger: str = ""
    script: str = ""


class KnowledgeEntry(BaseModel):
    id: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    keywords: list[str] = Field(default_factory=list)
    answer: str = Field(..., min_length=1)
    upsell: list[UpsellItem] = Field(default_factory=list)
    avoid_upsell_if: list[str] = Field(default_factory=list)


class ComposeAnswerArgs(BaseModel):
    """Аргументы tool compose_answer от LLM."""

    reply: str
    upsell_tip: str
    topics: list[str] = Field(default_factory=list)
    sentiment: Sentiment = "neutral"
    intent: Intent = "other"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
