# AmoCRM Manager Assistant

AI-ассистент менеджера для AmoCRM: за секунды даёт **готовый ответ клиенту** по базе знаний и **подсказку по допродажам** (или деэскалацию, если клиент недоволен).

![Демо интерфейса](docs/demo.png)

> **Вам нужно:** заменить `docs/demo.png` на реальный скриншот или GIF интерфейса и при желании добавить ссылку на видео ниже.

<!-- Видео-демо: https://… -->

## Что делает

Менеджер в диалоге AmoCRM вставляет обращение клиента и получает:

1. Вежливый ответ на русском — только факты из БЗ, без выдумок.
2. Краткую подсказку для себя: 2–4 идеи допродаж + фраза-скрипт (или шаги деэскалации при жалобе).

Работает **с ключом Anthropic** (режим LLM) и **без него** (шаблонный fallback). Сервис не падает с 500 из-за сбоев LLM.

## Быстрый старт

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # или: cp .env.example .env
uvicorn app.main:app --reload
```

Откройте http://127.0.0.1:8000/

```bash
pytest -q
python -m eval.run
```

## Переменные окружения

Скопируйте `.env.example` → `.env` (файл `.env` в git не попадает):

| Переменная | Описание |
|---|---|
| `ANTHROPIC_API_KEY` | Ключ Anthropic. Пусто → всегда `fallback` |
| `LLM_MODEL` | Имя модели Claude |
| `AMO_SUBDOMAIN` | Поддомен `xxx` из `xxx.amocrm.ru` |
| `AMO_TOKEN` | Bearer-токен AmoCRM API v4 |

## Примеры запросов

```bash
# Ответ ассистента
curl -s http://127.0.0.1:8000/api/assist ^
  -H "Content-Type: application/json" ^
  -d "{\"client_name\":\"Анна\",\"message\":\"Сколько стоит доставка?\",\"history\":[]}"

# Вебхук AmoCRM
curl -s http://127.0.0.1:8000/webhook/amocrm ^
  -H "Content-Type: application/json" ^
  -d "{\"client_name\":\"Игорь\",\"message\":\"Какая гарантия?\",\"lead_id\":123}"

# База знаний
curl -s http://127.0.0.1:8000/api/kb
```

Ответ `/api/assist`: `{reply, upsell_tip, topics, sentiment, intent, confidence, mode, matched_ids}`  
`mode`: `llm` | `fallback` | `no_match`

### Схема интеграции с AmoCRM

1. Исходящий вебхук на входящее сообщение → `https://<host>/webhook/amocrm`.
2. Сервис извлекает текст, имя, `lead_id` (JSON или form-urlencoded).
3. Считает ответ той же логикой, что `/api/assist`.
4. Примечание с подсказкой пишется в фоне: `POST /api/v4/leads/{id}/notes`.
5. Без `AMO_TOKEN` результат всё равно возвращается.

## Как устроено

1. Поиск по БЗ: нормализация → стемминг (`snowballstemmer`) → скоринг → порог → топ-3.
2. Нет совпадений → `no_match` без LLM.
3. Есть совпадения → Anthropic **tool use** `compose_answer` (таймаут 15 с, 1 ретрай).
4. Нет ключа / ошибка → шаблонный `fallback` + словарный sentiment/intent.
5. Негатив / жалоба / `avoid_upsell_if` → деэскалация вместо допродаж.

## Ограничения и идеи развития

- History передаёт клиент API; отдельного стора диалогов нет.
- Стемминг не чинит сильные опечатки корня.
- OAuth refresh AmoCRM не реализован (только long-lived Bearer).
- Идеи: эмбеддинги для поиска, кэш LLM, метрики «ответ без правок», мультиязычность.

## Как собрано: инструменты ИИ

Прототип собран в **Cursor** с помощью AI-агента (Composer):

- генерация структуры FastAPI-проекта, БЗ и UI;
- стемминг-поиск, tool use Anthropic, fallback и вебхук AmoCRM;
- pytest + офлайн-eval на 10 кейсах;
- итерации по ложным срабатываниям поиска и деэскалации при негативе.

Код и решения ревьюились вручную; секреты в репозиторий не попадали.

## Структура

```
app/          # FastAPI, KB, LLM, fallback, AmoCRM, UI
data/         # knowledge_base.json
eval/         # cases.json + run.py
tests/
docs/demo.png
```

## Лицензия

MIT © Карина
