# pantry-bot

Telegram bot for managing a shared home pantry. Add, remove, list, and get recipe suggestions via slash commands, free-form messages, or photos of groceries/receipts.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Get an [OpenRouter](https://openrouter.ai) API key.
3. Find your Telegram user ID (e.g. via [@userinfobot](https://t.me/userinfobot)).
4. Copy `.env.example` to `.env` and fill in values.
5. Install deps and run:

```bash
uv sync
uv run pantry-bot
```

## Commands

- `/start`, `/help` — intro.
- `/list` — show current pantry sorted by expiry.
- `/add <text>` — e.g. `/add 2kg rice, 6 eggs expiring friday`.
- `/remove <text>` — e.g. `/remove 3 eggs`.
- `/cook [direction]` — recipe suggestions from current stock; optional free-form direction (e.g. `/cook vegetarian`, `/cook something spicy in 20 minutes`).
- `/clear` — wipe the pantry (confirmation required).

You can also just send a plain message or a photo; the bot parses it with the LLM and asks for confirmation before writing.

## Environment variables

| var | purpose |
| --- | --- |
| `TELEGRAM_TOKEN` | BotFather token |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OPENROUTER_MODEL` | model id (default `google/gemini-3-flash-preview`) |
| `WHITELIST` | comma-separated Telegram user IDs |
| `DB_PATH` | path to SQLite file (default `./data/pantry.db`) |
