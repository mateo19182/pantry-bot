from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import handlers
from .config import load_config
from .db import init_db
from .llm import OpenRouterClient


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Telegram's HTTP layer is noisy at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config()
    conn = init_db(config.db_path)
    llm = OpenRouterClient(config.openrouter_api_key, config.openrouter_model)

    app = Application.builder().token(config.telegram_token).build()
    app.bot_data["db"] = conn
    app.bot_data["llm"] = llm
    app.bot_data["whitelist"] = config.whitelist

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("list", handlers.cmd_list))
    app.add_handler(CommandHandler("add", handlers.cmd_add))
    app.add_handler(CommandHandler("remove", handlers.cmd_remove))
    app.add_handler(CommandHandler("cook", handlers.cmd_cook))
    app.add_handler(CommandHandler("clear", handlers.cmd_clear))
    app.add_handler(CommandHandler("shop", handlers.cmd_shop))
    app.add_handler(CommandHandler("buy", handlers.cmd_buy))
    app.add_handler(CommandHandler("unbuy", handlers.cmd_unbuy))
    app.add_handler(CommandHandler("shopclear", handlers.cmd_shopclear))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))
    app.add_handler(CallbackQueryHandler(handlers.on_callback))
    app.add_error_handler(handlers.on_error)

    async def _shutdown(_app: Application) -> None:
        await llm.aclose()
        conn.close()

    app.post_shutdown = _shutdown

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
