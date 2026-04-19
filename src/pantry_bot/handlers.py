from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import date, timedelta
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .auth import whitelisted
from .db import Item, ItemChange, apply_changes, clear_all, list_items
from .llm import LLMError, OpenRouterClient

log = logging.getLogger(__name__)

PENDING_KEY = "pending_changes"
CLEAR_KEY = "pending_clear"

HELP_TEXT = (
    "Pantry bot. I keep track of what's in your kitchen.\n\n"
    "Commands:\n"
    "/list — show current pantry\n"
    "/add <text> — add items (e.g. `2kg rice, 6 eggs`)\n"
    "/remove <text> — remove items (e.g. `3 eggs`)\n"
    "/cook — recipe suggestions from what you have\n"
    "/clear — wipe the pantry (with confirmation)\n\n"
    "You can also just send a plain message or a photo of groceries — "
    "I'll figure out what you mean and ask before saving."
)


# --- slash commands ---------------------------------------------------------

@whitelisted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)


@whitelisted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)


@whitelisted
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn: sqlite3.Connection = context.application.bot_data["db"]
    items = list_items(conn)
    if not items:
        await update.effective_message.reply_text("Pantry is empty.")
        return
    await update.effective_message.reply_text(_format_list(items))


@whitelisted
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text("Usage: /add 2kg rice, 6 eggs")
        return
    await _handle_text(update, context, text, force_action="add")


@whitelisted
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text("Usage: /remove 3 eggs")
        return
    await _handle_text(update, context, text, force_action="remove")


@whitelisted
async def cmd_cook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn: sqlite3.Connection = context.application.bot_data["db"]
    items = list_items(conn)
    if not items:
        await update.effective_message.reply_text("Pantry is empty — nothing to cook with.")
        return

    llm: OpenRouterClient = context.application.bot_data["llm"]
    msg = await update.effective_message.reply_text("Thinking up ideas…")
    try:
        recipes = await llm.suggest_recipes(items)
    except LLMError as e:
        log.warning("recipe suggestion failed: %s", e)
        await msg.edit_text("Couldn't get recipes right now, try again in a moment.")
        return

    if not recipes:
        await msg.edit_text("Couldn't think of anything with this pantry.")
        return

    lines: list[str] = []
    for r in recipes:
        lines.append(f"🍳 *{r.title}*")
        if r.uses:
            lines.append(f"uses: {', '.join(r.uses)}")
        if r.missing:
            lines.append(f"missing: {', '.join(r.missing)}")
        lines.append(r.steps)
        lines.append("")
    await msg.edit_text("\n".join(lines).strip(), parse_mode="Markdown")


@whitelisted
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = uuid.uuid4().hex[:8]
    context.user_data[CLEAR_KEY] = token
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🗑 Yes, wipe it", callback_data=f"clear:yes:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"clear:no:{token}"),
        ]]
    )
    await update.effective_message.reply_text(
        "This will delete *everything* in the pantry. Are you sure?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# --- free-form inputs -------------------------------------------------------

@whitelisted
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    await _handle_text(update, context, text, force_action=None)


@whitelisted
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message.photo:
        return
    photo = message.photo[-1]  # largest
    tg_file = await photo.get_file()
    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    image_bytes = buf.getvalue()

    llm: OpenRouterClient = context.application.bot_data["llm"]
    thinking = await message.reply_text("Looking at the photo…")
    try:
        changes = await llm.parse_image(image_bytes, "image/jpeg", message.caption)
    except LLMError as e:
        log.warning("image parse failed: %s", e)
        await thinking.edit_text("Couldn't read the photo, try again or describe it in text.")
        return

    if not changes:
        await thinking.edit_text("I couldn't spot any pantry items in that photo.")
        return

    await thinking.delete()
    await _propose_changes(update, context, changes)


# --- shared text parsing flow ----------------------------------------------

async def _handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    force_action: str | None,
) -> None:
    llm: OpenRouterClient = context.application.bot_data["llm"]
    try:
        changes = await llm.parse_message(text)
    except LLMError as e:
        log.warning("message parse failed: %s", e)
        await update.effective_message.reply_text("I couldn't understand that, try rephrasing.")
        return

    if force_action in ("add", "remove"):
        changes = [
            ItemChange(
                action=force_action,
                name=c.name,
                quantity=c.quantity,
                unit=c.unit,
                expires_at=c.expires_at,
                notes=c.notes,
            )
            for c in changes
        ]

    if not changes:
        await update.effective_message.reply_text(
            "I didn't find any pantry items in that. Try something like `2kg rice`."
        )
        return

    await _propose_changes(update, context, changes)


async def _propose_changes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    changes: list[ItemChange],
) -> None:
    token = uuid.uuid4().hex[:8]
    pending = context.user_data.setdefault(PENDING_KEY, {})
    pending[token] = changes

    summary_lines = ["I understood:"]
    for c in changes:
        prefix = "+" if c.action == "add" else "-"
        qty = f"{c.quantity:g}{c.unit if c.unit != 'unit' else ''}"
        extra = f" (expires {c.expires_at.isoformat()})" if c.expires_at else ""
        summary_lines.append(f"{prefix} {qty} {c.name}{extra}")
    summary_lines.append("\nProceed?")

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Apply", callback_data=f"apply:yes:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"apply:no:{token}"),
        ]]
    )
    await update.effective_message.reply_text(
        "\n".join(summary_lines),
        reply_markup=keyboard,
    )


# --- callback query ---------------------------------------------------------

@whitelisted
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    kind, choice, token = parts

    if kind == "apply":
        await _resolve_apply(update, context, choice, token)
    elif kind == "clear":
        await _resolve_clear(update, context, choice, token)


async def _resolve_apply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    choice: str,
    token: str,
) -> None:
    pending: dict[str, list[ItemChange]] = context.user_data.get(PENDING_KEY, {})
    changes = pending.pop(token, None)
    query = update.callback_query

    if changes is None:
        await query.edit_message_text("That confirmation is no longer valid.")
        return

    if choice != "yes":
        await query.edit_message_text("Cancelled.")
        return

    conn: sqlite3.Connection = context.application.bot_data["db"]
    user_id = update.effective_user.id
    results = apply_changes(conn, changes, user_id)
    body = "Done:\n" + "\n".join(results) if results else "Done."
    await query.edit_message_text(body)


async def _resolve_clear(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    choice: str,
    token: str,
) -> None:
    expected = context.user_data.pop(CLEAR_KEY, None)
    query = update.callback_query

    if expected != token:
        await query.edit_message_text("That confirmation is no longer valid.")
        return
    if choice != "yes":
        await query.edit_message_text("Cancelled.")
        return

    conn: sqlite3.Connection = context.application.bot_data["db"]
    count = clear_all(conn, update.effective_user.id)
    await query.edit_message_text(f"Wiped {count} items from the pantry.")


# --- formatting -------------------------------------------------------------

def _format_list(items: list[Item]) -> str:
    today = date.today()
    soon = today + timedelta(days=3)
    lines = ["*Pantry:*"]
    for it in items:
        qty = f"{it.quantity:g}{it.unit if it.unit != 'unit' else ''}"
        expiry = ""
        if it.expires_at:
            tag = "⚠️ " if it.expires_at <= soon else ""
            expiry = f" — {tag}expires {it.expires_at.isoformat()}"
        lines.append(f"• {qty} {it.name}{expiry}")
    return "\n".join(lines)
