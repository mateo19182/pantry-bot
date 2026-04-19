# Ideas / backlog

Not a commitment — just things worth trying.

## Ingestion

- **Fridge camera.** A cheap cam pointed inside the fridge snapping on door-close, pushing frames to the bot's image pipeline. Diff against the last known state to auto-add/remove items without any typing. Needs a way to ignore noise (hands, half-visible items) and to confirm ambiguous deltas with the user before writing.
- **Recipe reels.** Paste an Instagram / TikTok / YouTube Shorts link and have the bot pull the audio + captions, extract the recipe, and save it. Probably: `yt-dlp` → whisper transcript + OCR of on-video text → LLM → structured recipe.
- **Barcode scan.** Photograph a product barcode; bot looks it up (OpenFoodFacts) and pre-fills name + unit before asking for quantity/expiry.
- **Grocery receipt OCR.** We already accept photos, but a receipt-specific prompt + line-item parsing would make supermarket runs one-shot.

## Recipes

- **Saved recipe book.** A `recipes` table of user-saved dishes. `/cook` first looks for matches against saved recipes before free-styling; tapping a recipe saves it. Recipes saved from reels land here too.
- **"Cook this"** command. `/make <recipe name>` — deduct the recipe's ingredients from the pantry in one go (with confirmation).
- **Auto-populate shopping list** from the `missing` fields of `/cook` suggestions, or from a target recipe.

## Pantry hygiene

- **Expiry reminders.** A daily cron that DMs the whitelisted users what's expiring in the next 2-3 days.
- **Undo.** `/undo` to reverse the most recent action from `action_log`.
- **Per-user view.** Optional scoping if the shared pantry ever gets noisy — e.g. "Mateo's pantry" vs "shared".

## Ops

- **Backups.** Nightly SQLite dump to somewhere off the server (Tribo VPS or an object store).
- **Tests.** At minimum, golden tests on `_parse_changes` / `_parse_recipes` with fixtures of real LLM responses so prompt regressions are caught early.
