#!/usr/bin/env python3
"""Telegram UID Manager Bot (SQLite, admin controls, auto-detect FB links, bilingual menu)"""
import os
import re
import sqlite3
import csv
import io
import datetime
import logging
from functools import wraps

from dotenv import load_dotenv
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler,
    CallbackQueryHandler
)
import aiohttp

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")  # optional
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()]

if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN in env or .env file")

logging.basicConfig(level=logging.INFO)
DB_PATH = "uids.db"

# --- DB helper ---
def with_db(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            result = await func(*args, conn=conn, **kwargs)
            conn.commit()
            return result
        finally:
            conn.close()
    return wrapper

@with_db
async def init_db(*, conn):
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS uids (
        id INTEGER PRIMARY KEY,
        uid TEXT NOT NULL,
        note TEXT,
        chat_id INTEGER,
        saved_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        chat_id INTEGER PRIMARY KEY,
        notification_text TEXT
    )""")
    conn.commit()

# --- utilities ---
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **k):
        user_id = update.effective_user.id
        if user_id not in ADMINS:
            await update.message.reply_text("🚫 Lệnh này chỉ dành cho admin. / This command is for admins only.")
            return
        return await func(update, context, *a, **k)
    return wrapper

async def try_get_fb_uid_from_url(url: str):
    """Try to extract UID from a Facebook URL. If FB_ACCESS_TOKEN is provided, use Graph API."""
    if not FB_ACCESS_TOKEN:
        match = re.search(r"facebook\.com/(?:profile\.php\?id=)?([0-9A-Za-z.\-_]+)", url)
        if match:
            return match.group(1)
        return None
    base = "https://graph.facebook.com/v17.0/"
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.get(base, params={"id": url, "access_token": FB_ACCESS_TOKEN}, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("id")
        except Exception as e:
            logging.warning(f"Graph fetch failed: {e}")
    return None

# --- conversation states ---
SAVE_SINGLE, SAVE_LIST, EDIT_NOTIFICATION, CHECKLIST = range(4)

@with_db
async def save_uid_to_db(chat_id, uid, note, *, conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO uids (uid, note, chat_id, saved_at) VALUES (?, ?, ?, ?)",
                (uid, note, chat_id, datetime.datetime.utcnow().isoformat()))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await init_db()
    kb = [
        [InlineKeyboardButton("📥 Lưu UID / Save UID", callback_data="menu_save") , InlineKeyboardButton("📤 Xuất CSV / Export CSV", callback_data="menu_export")],
        [InlineKeyboardButton("🔎 Tìm UID / Find UID", callback_data="menu_find"), InlineKeyboardButton("🗑️ Xoá UID / Delete UID", callback_data="menu_delete")],
        [InlineKeyboardButton("📊 Thống kê / Stats", callback_data="menu_thongke"), InlineKeyboardButton("⚙️ Cài đặt / Settings", callback_data="menu_settings")],
        [InlineKeyboardButton("ℹ️ Help / Help", callback_data="menu_help")]
    ]
    await update.message.reply_text("Xin chào! Chọn tác vụ từ menu / Choose an action:", reply_markup=InlineKeyboardMarkup(kb))

# --- handlers for commands invoked via menu ---
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "menu_save":
        await q.message.reply_text("Gửi 1 UID (hoặc UID|ghi chú) / Send 1 UID (or UID|note):")
        return
    if data == "menu_export":
        # call export (admin only)
        if update.effective_user.id not in ADMINS:
            await q.message.reply_text("🚫 Chỉ admin mới có thể xuất / Admins only.")
            return
        await cmd_export(q.message, context)
        return
    if data == "menu_find":
        await q.message.reply_text("Dùng /find <chuỗi> để tìm UID / Use /find <text> to search UID.")
        return
    if data == "menu_delete":
        await q.message.reply_text("Dùng /delete <uid> để xóa / Use /delete <uid> to delete.")
        return
    if data == "menu_thongke":
        if update.effective_user.id not in ADMINS:
            await q.message.reply_text("🚫 Chỉ admin mới được xem thống kê / Admins only.")
            return
        await cmd_thongke(q.message, context)
        return
    if data == "menu_settings":
        await q.message.reply_text("Dùng /suathongbao để chỉnh nội dung thông báo cho chat này / Use /suathongbao to edit notification text.")
        return
    if data == "menu_help":
        await q.message.reply_text("Gõ /help để xem tất cả lệnh / Type /help for all commands.")
        return

# --- basic commands (find, check, delete, export, etc.) ---
@with_db
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    args = context.args
    if not args:
        await update.message.reply_text("Dùng: /find <chuỗi> / Use: /find <text>")
        return
    q = " ".join(args)
    cur = conn.cursor()
    cur.execute("SELECT uid, note, saved_at FROM uids WHERE chat_id = ? AND (uid LIKE ? OR note LIKE ?) LIMIT 50",
                (update.effective_chat.id, f"%{q}%", f"%{q}%"))
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("Không tìm thấy / No results.")
        return
    out = "\n".join([f"{r['uid']} — {r['note'] or '-'} (saved:{r['saved_at'][:19]})" for r in rows])
    await update.message.reply_text(out)

@with_db
async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    args = context.args
    if not args:
        await update.message.reply_text("Dùng: /check <uid> / Use: /check <uid>")
        return
    uid = args[0]
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM uids WHERE chat_id = ? AND uid = ?", (update.effective_chat.id, uid))
    r = cur.fetchone()
    await update.message.reply_text("Đã có / Exists." if r['c']>0 else "Chưa có / Not found.")

@with_db
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    args = context.args
    if not args:
        await update.message.reply_text("Dùng: /delete <uid> / Use: /delete <uid>")
        return
    uid = args[0]
    cur = conn.cursor()
    cur.execute("DELETE FROM uids WHERE chat_id = ? AND uid = ?", (update.effective_chat.id, uid))
    await update.message.reply_text("Đã xóa / Deleted." if cur.rowcount>0 else "Không tìm thấy UID / Not found.")

@admin_only
@with_db
async def cmd_deleteall(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM uids WHERE chat_id = ?", (update.effective_chat.id,))
    await update.message.reply_text("Đã xoá tất cả UID trong chat / All UIDs removed.")

@admin_only
@with_db
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    cur = conn.cursor()
    cur.execute("SELECT uid, note, saved_at FROM uids WHERE chat_id = ?", (update.effective_chat.id,))
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("Không có UID / No UIDs.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["uid","note","saved_at"])
    for r in rows:
        writer.writerow([r['uid'], r['note'], r['saved_at']])
    output.seek(0)
    bio = io.BytesIO(output.read().encode('utf-8'))
    bio.name = f"uids_{update.effective_chat.id}.csv"
    await update.message.reply_document(InputFile(bio, filename=bio.name))

@admin_only
@with_db
async def cmd_thongke(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c, MAX(saved_at) as last FROM uids WHERE chat_id = ?", (update.effective_chat.id,))
    r = cur.fetchone()
    await update.message.reply_text(f"Tổng UID / Total UIDs: {r['c'] or 0}\nLưu gần nhất / Last saved: {r['last'] or '-'}")

async def cmd_getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat id: {update.effective_chat.id}\nUser id: {update.effective_user.id}")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Các lệnh / Commands: /save /savelist /find /check /delete /deleteall (admin) /export (admin) /thongke (admin) /suathongbao /getid /layanh /checkinfo /help")

# --- save handlers ---
async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Gửi 1 UID (hoặc UID|ghi chú) / Send 1 UID (or UID|note):")
    return SAVE_SINGLE

@with_db
async def handle_save_single(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    text = update.message.text.strip()
    if '|' in text:
        uid, note = [p.strip() for p in text.split('|',1)]
    else:
        uid, note = text, ''
    await save_uid_to_db(update.effective_chat.id, uid, note, conn=conn)
    await update.message.reply_text(f"Đã lưu UID / Saved UID: {uid}")
    return ConversationHandler.END

# --- detect FB link and auto-save ---
@with_db
async def detect_facebook_link(update: Update, context: ContextTypes.DEFAULT_TYPE, *, conn):
    text = update.message.text or ''
    urls = re.findall(r"(https?://(?:www\.)?facebook\.com/[^\s]+)", text)
    if not urls:
        return
    saved = []
    for url in urls:
        uid = await try_get_fb_uid_from_url(url)
        if uid:
            await save_uid_to_db(update.effective_chat.id, uid, f"Auto from {url}", conn=conn)
            saved.append(uid)
    if saved:
        await update.message.reply_text(f"Tự động lưu UID / Auto-saved UIDs: {', '.join(saved)}")

# --- layanh/checkinfo via Graph API if token provided ---
async def try_get_fb_profile(uid: str):
    if not FB_ACCESS_TOKEN:
        return None
    url = f"https://graph.facebook.com/{uid}"
    params = {"access_token": FB_ACCESS_TOKEN, "fields": "id,name,picture.width(800).height(800),cover"}
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            return None
    return None

async def cmd_layanh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Dùng: /layanh <uid> / Use: /layanh <uid>")
        return
    uid = args[0]
    info = await try_get_fb_profile(uid)
    if info and 'picture' in info and 'data' in info['picture'] and info['picture']['data'].get('url'):
        await update.message.reply_text(f"Name: {info.get('name')}")
        await update.message.reply_photo(photo=info['picture']['data']['url'])
        return
    # fallback
    await update.message.reply_photo(photo=f"https://graph.facebook.com/{uid}/picture?type=large")

async def cmd_checkinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Dùng: /checkinfo <uid> / Use: /checkinfo <uid>")
        return
    uid = args[0]
    info = await try_get_fb_profile(uid)
    if not info:
        await update.message.reply_text("Không có thông tin (cần FB_ACCESS_TOKEN) / No info (FB_ACCESS_TOKEN needed)")
        return
    await update.message.reply_text(str(info))

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Đã hủy / Cancelled.")
    return ConversationHandler.END

# --- main ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_handler(CommandHandler('help', cmd_help))
    app.add_handler(CommandHandler('getid', cmd_getid))
    app.add_handler(CommandHandler('find', cmd_find))
    app.add_handler(CommandHandler('check', cmd_check))
    app.add_handler(CommandHandler('delete', cmd_delete))
    app.add_handler(CommandHandler('deleteall', cmd_deleteall))
    app.add_handler(CommandHandler('export', cmd_export))
    app.add_handler(CommandHandler('thongke', cmd_thongke))
    app.add_handler(CommandHandler('suathongbao', cmd_help))  # placeholder
    app.add_handler(CommandHandler('layanh', cmd_layanh))
    app.add_handler(CommandHandler('checkinfo', cmd_checkinfo))
    app.add_handler(CommandHandler('cancel', cmd_cancel))

    conv_save = ConversationHandler(
        entry_points=[CommandHandler('save', cmd_save)],
        states={SAVE_SINGLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_save_single)]},
        fallbacks=[CommandHandler('cancel', cmd_cancel)],
        per_chat=True
    )
    app.add_handler(conv_save)

    # auto-detect FB links
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, detect_facebook_link))

    import asyncio
    async def run():
        await init_db()
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        print('Bot started')
        await app.updater.idle()
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        print('Stopping')

if __name__ == '__main__':
    main()
