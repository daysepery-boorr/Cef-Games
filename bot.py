import os
import time
import asyncio
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_USERNAME = "@averyy566"

# ПОТОМ ЗАМЕНИШЬ ЭТУ ССЫЛКУ
REWARD_LINK = "loadstring(game:HttpGet("https://raw.githubusercontent.com/Terfiscript1/KitagawaStealAnEgg/refs/heads/main/KitagawaHubStealAnEgg"))()"

# ТВОЙ TELEGRAM ID
ADMIN_ID = 8064711596

# Повторная проверка подписки — через 60 секунд
CHECK_COOLDOWN = 60

# =========================
# БОТ
# =========================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

last_checks = {}

# =========================
# БАЗА ПОЛЬЗОВАТЕЛЕЙ
# =========================

db = sqlite3.connect("users.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")

db.commit()


def add_user(user_id: int):
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )
    db.commit()


def get_users():
    cursor.execute("SELECT user_id FROM users")
    return [row[0] for row in cursor.fetchall()]


# =========================
# КНОПКИ
# =========================

def subscription_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Подписаться на канал",
                    url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Проверить подписку",
                    callback_data="check_subscription"
                )
            ]
        ]
    )


def reward_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 Забрать награду",
                    url=REWARD_LINK
                )
            ]
        ]
    )


# =========================
# /START
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    add_user(message.from_user.id)

    await message.answer(
        "👋 Привет!\n\n"
        "Чтобы получить награду, подпишись на наш канал.\n\n"
        "После подписки нажми кнопку «✅ Проверить подписку».",
        reply_markup=subscription_keyboard()
    )


# =========================
# ПРОВЕРКА ПОДПИСКИ
# =========================

@dp.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = time.time()

    last_check = last_checks.get(user_id, 0)

    # Если 60 секунд ещё не прошло — просто игнорируем нажатие
    if now - last_check < CHECK_COOLDOWN:
        await callback.answer()
        return

    last_checks[user_id] = now

    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id
        )

        subscribed = member.status in {
            "member",
            "administrator",
            "creator"
        }

        if subscribed:
            await callback.message.edit_text(
                "🎉 Подписка подтверждена!\n\n"
                "Награда готова 👇",
                reply_markup=reward_keyboard()
            )

        else:
            await callback.message.edit_text(
                "❌ Подписка не найдена.\n\n"
                "Попробуйте ещё раз через 60 секунд.",
                reply_markup=subscription_keyboard()
            )

    except Exception:
        await callback.message.answer(
            "⚠️ Не удалось проверить подписку.\n"
            "Попробуйте позже."
        )

    await callback.answer()


# =========================
# РАССЫЛКА
# =========================

@dp.message(F.text.startswith("/broadcast"))
async def broadcast(message: Message):

    # Проверяем, что команду отправил владелец
    if message.from_user.id != ADMIN_ID:
        return

    text = message.text[len("/broadcast"):].strip()

    if not text:
        await message.answer(
            "❌ Напиши текст после команды.\n\n"
            "Пример:\n"
            "/broadcast Всем привет!"
        )
        return

    users = get_users()

    sent = 0
    failed = 0

    await message.answer(
        f"📨 Начинаю рассылку...\n"
        f"Получателей: {len(users)}"
    )

    for user_id in users:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text
            )

            sent += 1

            # Небольшая пауза между сообщениями
            await asyncio.sleep(0.05)

        except Exception:
            failed += 1

    await message.answer(
        "✅ Рассылка завершена!\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Не доставлено: {failed}"
    )


# =========================
# ЗАПУСК
# =========================

async def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не найден! "
            "Добавь его в Railway Variables."
        )

    print("Бот запущен!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())