from __future__ import annotations

import logging
from functools import wraps
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def whitelisted(handler: Handler) -> Handler:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        whitelist: frozenset[int] = context.application.bot_data["whitelist"]
        if user is None or user.id not in whitelist:
            log.info("rejected message from non-whitelisted user %s", user)
            if update.effective_message is not None:
                await update.effective_message.reply_text(
                    "Sorry, you're not authorised to use this bot."
                )
            return
        await handler(update, context)

    return wrapper
