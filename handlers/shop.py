from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from database import User, Admin

router = Router()

PRICES = {
    "1_day": 500,
    "1_week": 1000,
    "1_year": 3000
}

def get_shop_keyboard():
    kb = [
        [InlineKeyboardButton(text="⏱ Админка на 1 день (500 монет)", callback_data="buy_admin_1_day")],
        [InlineKeyboardButton(text="📅 Админка на 1 неделю (1000 монет)", callback_data="buy_admin_1_week")],
        [InlineKeyboardButton(text="🏆 Админка на 1 год (3000 монет)", callback_data="buy_admin_1_year")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

@router.callback_query(F.data == "shop")
async def open_shop(callback: CallbackQuery):
    await callback.message.edit_text(
        "🛒 <b>Добро пожаловать в магазин!</b>\n\n"
        "Здесь ты можешь купить права администратора.\n"
        "Выбери срок:",
        reply_markup=get_shop_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("buy_admin_"))
async def process_buy_admin(callback: CallbackQuery, session):
    duration_key = callback.data.replace("buy_admin_", "")
    price = PRICES.get(duration_key)

    if not price:
        await callback.answer("Ошибка: неизвестный товар.", show_alert=True)
        return

    user = await session.scalar(select(User).where(User.id == callback.from_user.id))
    
    if not user:
        await callback.answer("Сначала напиши /start!", show_alert=True)
        return

    if user.is_blacklisted:
        await callback.answer("⛔ Вы в черном списке и не можете совершать покупки.", show_alert=True)
        return

    if user.balance < price:
        await callback.answer(f"❌ Недостаточно средств! Нужно {price} монет, у тебя {user.balance}.", show_alert=True)
        return

    user.balance -= price
    await session.commit()

    now = datetime.now(timezone.utc)
    if duration_key == "1_day":
        expires = now + timedelta(days=1)
    elif duration_key == "1_week":
        expires = now + timedelta(weeks=1)
    elif duration_key == "1_year":
        expires = now + timedelta(days=365)

    new_admin = Admin(user_id=user.id, expires_at=expires, is_active=True)
    session.add(new_admin)
    await session.commit()

    await callback.message.edit_text(
        f"✅ <b>Успешно!</b>\n\n"
        f"Ты купил админку на срок <b>{duration_key.replace('_', ' ')}</b>.\n"
        f"Она активна до: <code>{expires.strftime('%Y-%m-%d %H:%M')} UTC</code>\n"
        f"Остаток на балансе: <b>{user.balance}</b> монет.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="main_menu")]])
    )
    await callback.answer()
