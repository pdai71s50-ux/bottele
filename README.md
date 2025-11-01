# Telegram UID Manager Bot (Replit-ready) — FINAL

Bot: @huuan2x6_bot
Admins: 7958084514

This package is Replit-ready. It contains a Telegram bot that:
- Saves Facebook UIDs into SQLite (uids.db)
- Auto-detects Facebook links and attempts to extract & save the UID
- Role-based admin commands (ADMINS in .env)
- Inline menu with Vietnamese + English labels
- Export, delete, statistics, notification settings, getid, layanh/checkinfo (FB)

## Quick start on Replit
1. Create a new Repl (Python).
2. Upload the ZIP contents or upload this repo.
3. The `.env` already contains your TELEGRAM_TOKEN and ADMINS as provided.
4. Optionally set `FB_ACCESS_TOKEN` in `.env` or Replit Secrets for better FB lookups.
5. Click "Run".
6. Open Telegram and message @huuan2x6_bot — send /start.

## Security notes
- TOKEN is included for convenience in this package. For production, remove it and use Replit Secrets.
- ADMINS is set to the numeric id provided: 7958084514.
- Keep `.env` private.

If you want me to remove the token from `.env` (safer) after you test, say so and I will send an updated ZIP.
