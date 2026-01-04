import logging
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv('TOKEN')
PORT = int(os.getenv('PORT', 8080))
WEBHOOK_HOST = f"https://{os.getenv('KOYEB_PUBLIC_DOMAIN')}"
WEBHOOK_PATH = '/webhook'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# База данных
conn = sqlite3.connect('finance.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    category TEXT,
    amount REAL,
    description TEXT,
    date TEXT
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    debtor TEXT,
    amount REAL,
    description TEXT,
    date TEXT
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    name TEXT
)
''')
conn.commit()

DEFAULT_INCOME_CATS = ['Зарплата 💰', 'Подарок 🎁', 'Инвестиции 📈']
DEFAULT_EXPENSE_CATS = ['Еда 🍔', 'Транспорт 🚗', 'Развлечения 🎉']

# Состояния FSM
class Form(StatesGroup):
    waiting_category = State()
    waiting_amount = State()
    waiting_category_name = State()
    waiting_debt = State()

def get_categories(user_id: int, cat_type: str):
    cursor.execute("SELECT name FROM categories WHERE user_id = ? AND type = ?", (user_id, cat_type))
    cats = [row[0] for row in cursor.fetchall()]
    if cat_type == 'income':
        return DEFAULT_INCOME_CATS + cats
    return DEFAULT_EXPENSE_CATS + cats

@dp.message(CommandStart())
async def start(message: Message):
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Доходы 💹'), KeyboardButton(text='Расходы 📉')],
                                             [KeyboardButton(text='Долги 🤝'), KeyboardButton(text='Баланс 💼')],
                                             [KeyboardButton(text='Статистика 📊'), KeyboardButton(text='Категории ➕')]],
                                   resize_keyboard=True)
    await message.answer("👋 Привет! Я твой финансовый помощник!\n\n"
                         "📱 Используй кнопки ниже для управления:\n"
                         "• Доходы/Расходы — добавь деньги с категориями\n"
                         "• Долги — учет кто кому должен\n"
                         "• Баланс — общий подсчет (транзакции + долги)\n"
                         "• Статистика — по месяцам\n"
                         "• Категории — добавь свои\n\n"
                         "💡 После выбора категории введи: <сумма> <описание>", reply_markup=keyboard)

@dp.message(F.text.in_(['Доходы 💹', 'Расходы 📉']))
async def select_category(message: Message, state: FSMContext):
    cat_type = 'income' if '💹' in message.text else 'expense'
    await state.update_data(cat_type=cat_type)
    cats = get_categories(message.from_user.id, cat_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=cat, callback_data=f"cat_{cat_type}_{cat}")] for cat in cats[:8]
    ] + [[InlineKeyboardButton(text="Отмена ❌", callback_data="cancel")]])
    await message.answer(f"📂 Выбери категорию {message.text}:", reply_markup=kb)
    await state.set_state(Form.waiting_category)

@dp.callback_query(F.data.startswith("cat_"))
async def process_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, cat_type, category = callback.data.split("_", 2)
    await state.update_data(category=category)
    await callback.message.answer(f"✅ Выбрано: **{category}**\n\n💰 Введи сумму и описание:\n`1000 Еда на неделю`", parse_mode=ParseMode.MARKDOWN)
    await state.set_state(Form.waiting_amount)

@dp.message(Form.waiting_amount)
async def add_transaction(message: Message, state: FSMContext):
    try:
        parts = message.text.split(maxsplit=1)
        amount = float(parts[0])
        desc = parts[1] if len(parts) > 1 else "Без описания"
        data = await state.get_data()
        cat_type = data['cat_type']
        category = data['category']
        user_id = message.from_user.id
        date = datetime.now().strftime('%Y-%m-%d %H:%M')
        sign = 1 if cat_type == 'income' else -1
        cursor.execute("INSERT INTO transactions (user_id, type, category, amount, description, date) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, cat_type, category, sign * amount, desc, date))
        conn.commit()
        await message.answer(f"🎉 **{cat_type.title()}** добавлен!\n"
                             f"💵 {amount} руб. • {category}\n"
                             f"📝 {desc}\n\n"
                             "➡️ Что дальше?", reply_markup=get_main_keyboard())
    except ValueError:
        await message.answer("❌ Ошибка! Формат: `500 Продукты`", parse_mode=ParseMode.MARKDOWN)
    await state.clear()

@dp.message(F.text == 'Долги 🤝')
async def debt_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Я должен кому-то 📉", callback_data="debt_me")],
        [InlineKeyboardButton(text="Мне должны 💹", callback_data="debt_other")],
        [InlineKeyboardButton(text="Отмена ❌", callback_data="cancel")]
    ])
    await message.answer("🤝 **Учет долгов**\nВыбери тип:", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data.startswith("debt_"))
async def process_debt(callback: CallbackQuery):
    await callback.answer()
    debt_type = callback.data.split("_")[1]
    await callback.message.answer(f"💸 **Долг ({'📉 вычесть' if debt_type == 'me' else '💹 добавить'})**\n\n"
                                  f"Формат: `1000 Долг Васе` или `5000 Займ другу`", parse_mode=ParseMode.MARKDOWN)
    # Для простоты используем глобальную переменную или FSM, но пока ответим

@dp.message(F.text == 'Баланс 💼')
async def balance(message: Message):
    user_id = message.from_user.id
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE user_id=?", (user_id,))
    trans = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(amount) FROM debts WHERE user_id=?", (user_id,))
    debts = cursor.fetchone()[0] or 0
    total = trans + debts
    await message.answer(f"💼 **Баланс**\n\n"
                         f"📊 Транзакции: **{trans:.2f}** руб.\n"
                         f"🤝 Долги: **{debts:.2f}** руб.\n"
                         f"🌟 **Итого: {total:.2f}** руб.", parse_mode=ParseMode.MARKDOWN)

@dp.message(F.text == 'Статистика 📊')
async def stats(message: Message):
    user_id = message.from_user.id
    cursor.execute("""
        SELECT strftime('%Y-%m', date) month, 
               SUM(CASE WHEN type='income' THEN amount ELSE 0 END) inc,
               SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) exp
        FROM transactions WHERE user_id=? GROUP BY month ORDER BY month DESC LIMIT 6
    """, (user_id,))
    rows = cursor.fetchall()
    if not rows:
        await message.answer("📊 Нет данных. Добавь транзакции!")
        return
    text = "📊 **Статистика за 6 мес.**\n\n"
    for month, inc, exp in rows:
        bal = inc + exp  # exp отрицательный
        text += f"`{month}`: +{inc:.0f} / {abs(exp):.0f} = **{bal:.0f}**\n"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

def get_main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='Доходы 💹'), KeyboardButton(text='Расходы 📉')],
        [KeyboardButton(text='Долги 🤝'), KeyboardButton(text='Баланс 💼')],
        [KeyboardButton(text='Статистика 📊'), KeyboardButton(text='Категории ➕')]
    ], resize_keyboard=True, one_time_keyboard=False)

@dp.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery):
    await callback.answer("Отменено!")
    await callback.message.answer("🏠 Главное меню:", reply_markup=get_main_keyboard())

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook set to {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()

if __name__ == '__main__':
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host='0.0.0.0', port=PORT)

