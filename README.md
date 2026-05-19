# visa-tracker

A personal service that monitors the U.S. State Department visa bulletin (F2B category, "All Chargeability Areas" column — applies to Armenia) and sends Telegram notifications when new bulletins are published or when the priority date `2018-07-03` becomes current.

## Quick start

```bash
git clone <repo>
cd visa-tracker
cp .env.example .env       # optional: fill in Telegram bot token + chat ID
docker compose up -d
open http://localhost:8080
```

The dashboard ships with 12 months of historical data and starts polling for new bulletins automatically.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | empty | Bot token from @BotFather. Leave empty to disable Telegram. |
| `TELEGRAM_CHAT_ID` | empty | Your chat ID (see "Telegram setup" below). |
| `TZ` | `UTC` | Used for cron interpretation. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for diagnosis. |
| `WEB_BIND` | `0.0.0.0:8080` | Listen address inside the container. |
| `PRIORITY_DATE` | `2018-07-03` | Hardcoded priority date to track. |

## Telegram setup

1. Talk to `@BotFather` in Telegram, run `/newbot`, get the bot token.
2. Send any message to your new bot.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and find your `chat.id`.
4. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
5. Verify: `docker compose exec visa-tracker python -m visa_tracker.cli test-telegram`

## CLI utilities

All run inside the container via `docker compose exec visa-tracker python -m visa_tracker.cli <cmd>`:

- `check-now` — force one scrape cycle now (writes + notifies as normal).
- `check-now --dry-run` — preview what *would* happen, no writes/sends.
- `simulate-bulletin --month 2026-07 --final-action 2017-10-22 --filing 2018-04-15 [--dry-run]` — inject a hypothetical bulletin.
- `render-message <YYYY-MM>` — print the Telegram message body for an existing bulletin.
- `test-telegram` — send a connectivity test.

## Development

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```
