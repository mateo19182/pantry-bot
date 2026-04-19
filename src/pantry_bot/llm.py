from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from dateutil import parser as dateparser

from .db import Item, ItemChange

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

PARSE_SYSTEM = """You translate a user's pantry message into structured actions.

Return ONLY a JSON object of the form:
{"changes": [{"action": "add"|"remove", "name": string, "quantity": number, "unit": string, "expires_at": "YYYY-MM-DD"|null, "notes": string|null}]}

Rules:
- action defaults to "add" unless the user clearly says remove/used/ate/finished/threw out.
- unit examples: "g", "kg", "ml", "l", "unit", "pack", "can", "bottle". Use "unit" for countable items like eggs.
- quantity must be a number; if the user doesn't specify, use 1.
- Resolve relative dates using today's date: {today}. Output dates as YYYY-MM-DD.
- Use lowercase, singular English names (e.g. "egg", "rice", "milk").
- If you cannot extract anything, return {"changes": []}.
- Do NOT include commentary, markdown, or code fences. Only the JSON object."""

RECIPES_SYSTEM = """You suggest cooking ideas from a list of pantry items.

Return ONLY a JSON object of the form:
{"recipes": [{"title": string, "uses": [string], "missing": [string], "steps": string}]}

Rules:
- Prefer recipes that use items expiring soonest.
- "uses" lists pantry items the recipe actually needs (by name as given).
- "missing" lists any extras not in the pantry (keep short, 0-3 items).
- "steps" is a single short paragraph (2-4 sentences), no bullet list.
- Return 3 recipes unless the pantry is nearly empty.
- Do NOT include commentary, markdown, or code fences."""


@dataclass
class Recipe:
    title: str
    uses: list[str]
    missing: list[str]
    steps: str


class LLMError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/m19182/pantry-bot",
                "X-Title": "pantry-bot",
            },
            timeout=60.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def parse_message(self, text: str) -> list[ItemChange]:
        system = PARSE_SYSTEM.format(today=date.today().isoformat())
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "response_format": {"type": "json_object"},
        }
        data = await self._post(payload)
        return _parse_changes(data)

    async def parse_image(self, image_bytes: bytes, mime: str, caption: str | None) -> list[ItemChange]:
        system = PARSE_SYSTEM.format(today=date.today().isoformat())
        b64 = base64.b64encode(image_bytes).decode("ascii")
        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Extract pantry items from this image (photo of groceries or receipt). "
                    "Assume action='add' unless the caption says otherwise."
                    + (f"\nCaption: {caption}" if caption else "")
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ]
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }
        data = await self._post(payload)
        return _parse_changes(data)

    async def suggest_recipes(self, items: list[Item]) -> list[Recipe]:
        if not items:
            return []
        pantry_lines = [
            f"- {it.name}: {it.quantity}{it.unit}"
            + (f" (expires {it.expires_at.isoformat()})" if it.expires_at else "")
            for it in items
        ]
        user_text = "Pantry:\n" + "\n".join(pantry_lines)
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": RECIPES_SYSTEM},
                {"role": "user", "content": user_text},
            ],
            "response_format": {"type": "json_object"},
        }
        data = await self._post(payload)
        return _parse_recipes(data)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise LLMError(f"network error talking to OpenRouter: {e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected OpenRouter response: {body}") from e
        return _extract_json(content)


def _extract_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    # Strip ```json fences the model might sneak in.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        log.warning("could not parse LLM JSON: %s", content[:500])
        raise LLMError(f"model returned invalid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise LLMError("model returned non-object JSON")
    return parsed


def _parse_changes(data: dict[str, Any]) -> list[ItemChange]:
    raw = data.get("changes", [])
    if not isinstance(raw, list):
        raise LLMError("model JSON missing 'changes' list")
    changes: list[ItemChange] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action", "add")
        if action not in ("add", "remove"):
            action = "add"
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        try:
            quantity = float(entry.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1.0
        unit = str(entry.get("unit") or "unit").strip() or "unit"
        expires_raw = entry.get("expires_at")
        expires_at: date | None = None
        if expires_raw:
            try:
                expires_at = dateparser.parse(str(expires_raw)).date()
            except (ValueError, TypeError):
                expires_at = None
        notes = entry.get("notes")
        notes = str(notes).strip() if notes else None
        changes.append(
            ItemChange(
                action=action,
                name=name,
                quantity=quantity,
                unit=unit,
                expires_at=expires_at,
                notes=notes,
            )
        )
    return changes


def _parse_recipes(data: dict[str, Any]) -> list[Recipe]:
    raw = data.get("recipes", [])
    if not isinstance(raw, list):
        raise LLMError("model JSON missing 'recipes' list")
    recipes: list[Recipe] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        uses = [str(x) for x in entry.get("uses", []) if isinstance(x, (str, int, float))]
        missing = [str(x) for x in entry.get("missing", []) if isinstance(x, (str, int, float))]
        steps = str(entry.get("steps") or "").strip()
        recipes.append(Recipe(title=title, uses=uses, missing=missing, steps=steps))
    return recipes
