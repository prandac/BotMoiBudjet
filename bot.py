import logging
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

import os
TOKEN = os.getenv("TOKEN")
# ────────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)

ASK_AMOUNT, ASK_NOTE = range(2)
ASK_GOAL_NAME, ASK_GOAL_AMOUNT = range(10, 12)

EXPENSE_CATS = [
    ("Еда",         "food"),
    ("Транспорт",   "transport"),
    ("Учёба",       "education"),
    ("Развлечения", "fun"),
    ("Связь",       "phone"),
    ("Одежда",      "clothes"),
    ("Здоровье",    "health"),
    ("Прочее",      "other"),
]
INCOME_CATS = [
    ("Стипендия",  "stipend"),
    ("Родители",   "family"),
    ("Подработка", "work"),
    ("Другое",     "other_inc"),
]
ALL_NAMES = {v: k for k, v in dict(EXPENSE_CATS + INCOME_CATS).items()}

def init_db():
    con = sqlite3.connect("budget.db")
    con.execute("""CREATE TABLE IF NOT EXISTS tx (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, type TEXT, cat TEXT,
        amount REAL, note TEXT, date TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS goals (
        user_id INTEGER PRIMARY KEY,
        name TEXT, target REAL, saved REAL DEFAULT 0)""")
    con.commit(); con.close()

def db_add(uid, type_, cat, amount, note=""):
    con = sqlite3.connect("budget.db")
    con.execute("INSERT INTO tx VALUES (NULL,?,?,?,?,?,?)",
                (uid, type_, cat, amount, note,
                 datetime.now().strftime("%Y-%m-%d")))
    con.commit(); con.close()

def db_stats(uid):
    con = sqlite3.connect("budget.db")
    m = datetime.now().strftime("%Y-%m")
    inc = con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM tx WHERE user_id=? AND type='income' AND date LIKE ?",
        (uid, f"{m}%")).fetchone()[0]
    exp = con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM tx WHERE user_id=? AND type='expense' AND date LIKE ?",
        (uid, f"{m}%")).fetchone()[0]
    cats = con.execute(
        "SELECT cat, SUM(amount) FROM tx WHERE user_id=? AND type='expense' AND date LIKE ? GROUP BY cat ORDER BY 2 DESC",
        (uid, f"{m}%")).fetchall()
    recent = con.execute(
        "SELECT type,cat,amount,note,date FROM tx WHERE user_id=? ORDER BY id DESC LIMIT 5",
        (uid,)).fetchall()
    con.close()
    return inc, exp, cats, recent

def db_goal(uid):
    con = sqlite3.connect("budget.db")
    r = con.execute("SELECT name,target,saved FROM goals WHERE user_id=?", (uid,)).fetchone()
    con.close(); return r

def db_goal_set(uid, name, target):
    con = sqlite3.connect("budget.db")
    con.execute("INSERT OR REPLACE INTO goals VALUES (?,?,?,0)", (uid, name, target))
    con.commit(); con.close()

def db_goal_add(uid, amount):
    con = sqlite3.connect("budget.db")
    con.execute("UPDATE goals SET saved=saved+? WHERE user_id=?", (amount, uid))
    con.commit(); con.close()

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Доход",      callback_data="menu_income"),
         InlineKeyboardButton("💸 Расход",     callback_data="menu_expense")],
        [InlineKeyboardButton("📊 Статистика", callback_data="menu_stats"),
         InlineKeyboardButton("🎯 Цель",       callback_data="menu_goal")],
        [InlineKeyboardButton("📋 История",    callback_data="menu_history")],
    ])

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}!\n\nЯ МОЙ БЮДЖЕТ — твой финансовый дневник.\n\nВыбери действие:",
        reply_markup=main_menu()
    )

async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "menu_income":
        ctx.user_data["action"] = "income"
        kb = [[InlineKeyboardButton(n, callback_data=f"cat_{k}")] for n, k in INCOME_CATS]
        await q.edit_message_text("Выбери категорию дохода:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "menu_expense":
        ctx.user_data["action"] = "expense"
        flat = [InlineKeyboardButton(n, callback_data=f"cat_{k}") for n, k in EXPENSE_CATS]
        kb = [flat[i:i+2] for i in range(0, len(flat), 2)]
        await q.edit_message_text("Выбери категорию расхода:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "menu_stats":
        inc, exp, cats, _ = db_stats(q.from_user.id)
        bal = inc - exp
        sign = "+" if bal >= 0 else ""
        cats_text = ""
        for cat, amt in cats[:5]:
            pct = amt / exp * 100 if exp > 0 else 0
            cats_text += f"  {ALL_NAMES.get(cat, cat)}: {amt:,.0f} тг ({pct:.0f}%)\n"
        if not cats_text:
            cats_text = "  расходов пока нет\n"
        g = db_goal(q.from_user.id)
        goal_text = ""
        if g:
            gn, gt, gs = g
            pct = min(gs / gt * 100, 100) if gt else 0
            goal_text = f"\nЦель: {gn}\n  {gs:,.0f} из {gt:,.0f} тг ({pct:.0f}%)\n"
        month = datetime.now().strftime("%B %Y")
        text = (f"Статистика за {month}\n\n"
                f"Доходы:  {inc:,.0f} тг\n"
                f"Расходы: {exp:,.0f} тг\n"
                f"Баланс:  {sign}{bal:,.0f} тг\n\n"
                f"По категориям:\n{cats_text}{goal_text}")
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Назад", callback_data="menu_back")]]))

    elif data == "menu_history":
        _, _, _, recent = db_stats(q.from_user.id)
        if not recent:
            text = "Записей пока нет. Добавь первый доход!"
        else:
            text = "Последние записи:\n\n"
            for type_, cat, amount, note, date in recent:
                arrow = "+" if type_ == "income" else "-"
                text += f"{arrow} {ALL_NAMES.get(cat, cat)} — {amount:,.0f} тг"
                if note: text += f" ({note})"
                text += f"  [{date}]\n"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Назад", callback_data="menu_back")]]))

    elif data == "menu_goal":
        await q.edit_message_text("Используй команду /goal для постановки цели.")

    elif data == "menu_back":
        await q.edit_message_text("Выбери действие:", reply_markup=main_menu())

async def cat_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data.replace("cat_", "")
    ctx.user_data["category"] = key
    action = ctx.user_data.get("action", "expense")
    label = "доход" if action == "income" else "расход"
    cat_name = ALL_NAMES.get(key, key)
    await q.edit_message_text(
        f"Категория: {cat_name}\n\nНапиши сумму в тенге (например: 1500):"
    )
    return ASK_AMOUNT

async def got_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(" ", "").replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Напиши просто число, например: 1500")
        return ASK_AMOUNT
    ctx.user_data["amount"] = amount
    await update.message.reply_text(
        f"Сумма: {amount:,.0f} тг\n\nДобавь заметку или напиши /skip:"
    )
    return ASK_NOTE

async def got_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await save_tx(update, ctx, update.message.text)

async def skip_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await save_tx(update, ctx, "")

async def save_tx(update: Update, ctx: ContextTypes.DEFAULT_TYPE, note: str):
    uid    = update.effective_user.id
    action = ctx.user_data.get("action", "expense")
    cat    = ctx.user_data.get("category", "other")
    amount = ctx.user_data.get("amount", 0)
    db_add(uid, action, cat, amount, note)
    if action == "income" and db_goal(uid):
        db_goal_add(uid, amount * 0.2)
    inc, exp, _, _ = db_stats(uid)
    bal = inc - exp
    sign = "+" if bal >= 0 else ""
    label = "Доход" if action == "income" else "Расход"
    ctx.user_data.clear()
    await update.message.reply_text(
        f"{label} добавлен!\n\n"
        f"{ALL_NAMES.get(cat, cat)}: {amount:,.0f} тг\n"
        f"Заметка: {note if note else 'нет'}\n\n"
        f"Баланс за месяц: {sign}{bal:,.0f} тг",
        reply_markup=main_menu()
    )
    return ConversationHandler.END

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    inc, exp, cats, _ = db_stats(uid)
    bal = inc - exp
    sign = "+" if bal >= 0 else ""
    cats_text = ""
    for cat, amt in cats[:5]:
        pct = amt / exp * 100 if exp > 0 else 0
        cats_text += f"  {ALL_NAMES.get(cat, cat)}: {amt:,.0f} тг ({pct:.0f}%)\n"
    if not cats_text:
        cats_text = "  расходов пока нет\n"
    g = db_goal(uid)
    goal_text = ""
    if g:
        gn, gt, gs = g
        pct = min(gs / gt * 100, 100) if gt else 0
        goal_text = f"\nЦель: {gn}\n  {gs:,.0f} из {gt:,.0f} тг ({pct:.0f}%)\n"
    month = datetime.now().strftime("%B %Y")
    await update.message.reply_text(
        f"Статистика за {month}\n\n"
        f"Доходы:  {inc:,.0f} тг\n"
        f"Расходы: {exp:,.0f} тг\n"
        f"Баланс:  {sign}{bal:,.0f} тг\n\n"
        f"По категориям:\n{cats_text}{goal_text}",
        reply_markup=main_menu()
    )

async def cmd_goal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    g = db_goal(update.effective_user.id)
    if g:
        n, t, s = g
        pct = min(s / t * 100, 100) if t else 0
        text = (f"Текущая цель: {n}\n"
                f"Накоплено: {s:,.0f} из {t:,.0f} тг ({pct:.0f}%)\n\n"
                f"Напиши новое название чтобы изменить:")
    else:
        text = "Напиши название цели (например: Новый телефон):"
    await update.message.reply_text(text)
    return ASK_GOAL_NAME

async def got_goal_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["goal_name"] = update.message.text
    await update.message.reply_text(f"Цель: {update.message.text}\n\nНапиши сумму в тенге:")
    return ASK_GOAL_AMOUNT

async def got_goal_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(" ", ""))
        assert amount > 0
    except Exception:
        await update.message.reply_text("Напиши число, например: 60000")
        return ASK_GOAL_AMOUNT
    name = ctx.user_data.get("goal_name", "Моя цель")
    db_goal_set(update.effective_user.id, name, amount)
    ctx.user_data.clear()
    await update.message.reply_text(
        f"Цель поставлена!\n\n{name} — {amount:,.0f} тг\n\n"
        f"20% от каждого дохода идёт в накопления.\nПрогресс — /stats",
        reply_markup=main_menu()
    )
    return ConversationHandler.END

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=main_menu())
    return ConversationHandler.END

def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    tx_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cat_selected, pattern="^cat_")],
        states={
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_amount)],
            ASK_NOTE: [
                CommandHandler("skip", skip_note),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_note),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )

    goal_conv = ConversationHandler(
        entry_points=[CommandHandler("goal", cmd_goal)],
        states={
            ASK_GOAL_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_goal_name)],
            ASK_GOAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_goal_amount)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(tx_conv)
    app.add_handler(goal_conv)
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))

    print("Бот запущен! Ctrl+C — остановить.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
