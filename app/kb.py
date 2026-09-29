"""Загрузка БЗ, стемминг и поиск с порогом релевантности."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import snowballstemmer

from app.models import KnowledgeEntry

logger = logging.getLogger(__name__)

DEFAULT_KB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.json"
RELEVANCE_THRESHOLD = 0.18
TOP_N = 3

_stemmer = snowballstemmer.stemmer("russian")
_WORD_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)

_STOPWORDS = frozenset(
    {
        "и", "в", "на", "по", "с", "к", "у", "о", "а", "но", "или", "для", "из", "от",
        "до", "не", "что", "как", "это", "ли", "же", "бы", "то", "мне", "вас", "вам",
        "есть", "если", "можно", "нужно", "подскажите", "скажите", "пожалуйста",
        "здравствуйте", "добрый", "день", "вечера",
    }
)


def normalize(text: str) -> str:
    """Нижний регистр, ё→е, без пунктуации."""
    text = text.lower().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def stem_word(word: str) -> str:
    stems = _stemmer.stemWords([word])
    return stems[0] if stems else word


def tokenize(text: str) -> list[str]:
    """Токены после нормализации и фильтрации стоп-слов."""
    tokens = _WORD_RE.findall(normalize(text))
    return [t for t in tokens if len(t) >= 3 and t not in _STOPWORDS]


def stem_tokens(text: str) -> list[str]:
    return [stem_word(t) for t in tokenize(text)]


def load_kb(path: Path | None = None) -> list[dict[str, Any]]:
    kb_path = path or DEFAULT_KB_PATH
    with kb_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("База знаний должна быть JSON-массивом")
    return [KnowledgeEntry.model_validate(item).model_dump() for item in data]


def save_kb(entries: list[dict[str, Any]], path: Path | None = None) -> list[dict[str, Any]]:
    validated = [KnowledgeEntry.model_validate(item).model_dump() for item in entries]
    kb_path = path or DEFAULT_KB_PATH
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    with kb_path.open("w", encoding="utf-8") as fh:
        json.dump(validated, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return validated


def _phrase_stems(phrase: str) -> list[str]:
    """Стемы фразы без фильтра стоп-слов (для keywords)."""
    tokens = _WORD_RE.findall(normalize(phrase))
    return [stem_word(t) for t in tokens if len(t) >= 3]


def _keyword_phrases(entry: dict[str, Any]) -> list[list[str]]:
    phrases: list[list[str]] = []
    for kw in entry.get("keywords", []):
        stems = _phrase_stems(str(kw))
        if stems:
            phrases.append(stems)
    topic = _phrase_stems(str(entry.get("topic", "")))
    if topic:
        phrases.append(topic)
    return phrases


def _score_entry(query_stems: list[str], entry: dict[str, Any]) -> float:
    if not query_stems:
        return 0.0
    query_set = set(query_stems)
    phrases = _keyword_phrases(entry)
    if not phrases:
        return 0.0

    hits = 0.0
    matched_stems: set[str] = set()
    for phrase in phrases:
        inter = query_set.intersection(phrase)
        if not inter:
            continue
        if len(phrase) == 1:
            hits += 1.0
            matched_stems.update(inter)
        elif len(inter) >= 2 or set(phrase).issubset(query_set):
            hits += 1.2
            matched_stems.update(inter)
        # одно общее слово из многословной фразы («заказ» из «статус заказа») — игнор

    if hits <= 0:
        return 0.0

    coverage = len(matched_stems) / max(len(query_set), 1)
    topic_stems = set(_phrase_stems(str(entry.get("topic", ""))))
    topic_bonus = 0.1 if (query_set & topic_stems) else 0.0
    score = min(1.0, 0.22 * hits + 0.5 * coverage + topic_bonus)
    return round(score, 4)


def search(
    text: str,
    kb: list[dict[str, Any]] | None = None,
    top_n: int = TOP_N,
    threshold: float = RELEVANCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Топ-N записей БЗ выше порога релевантности."""
    entries = kb if kb is not None else load_kb()
    query_stems = stem_tokens(text)
    if not query_stems:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in entries:
        score = _score_entry(query_stems, entry)
        if score >= threshold:
            scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)
    results = [entry for _, entry in scored[:top_n]]
    logger.info(
        "kb.search matches=%s ids=%s",
        len(results),
        [e.get("id") for e in results],
    )
    return results


def should_avoid_upsell(message: str, entries: list[dict[str, Any]]) -> bool:
    """Проверка avoid_upsell_if по стеммам сообщения."""
    msg_stems = set(stem_tokens(message))
    msg_norm = normalize(message)
    for entry in entries:
        for phrase in entry.get("avoid_upsell_if") or []:
            phrase_norm = normalize(str(phrase))
            if phrase_norm and phrase_norm in msg_norm:
                return True
            phrase_stems = set(stem_tokens(str(phrase)))
            if phrase_stems and phrase_stems.issubset(msg_stems):
                return True
    return False
