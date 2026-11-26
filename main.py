import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
from sqlalchemy import select
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import config
from database import init_db, async_session, User, Match
from handlers import shop, matches

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.bot_token)
dp = Dispatcher()

class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        async with async_session() as session:
            data["session"] = session
            return await handler(event, data)

def get_main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Ближайшие матчи", callback_data="upcoming_matches")],
        [InlineKeyboardButton(text="🛒 Магазин", callback_data="shop")],
        [InlineKeyboardButton(text="💰 Получить 100 монет за подписку", callback_data="check_sub")]
    ])
    return kb

@dp.message(CommandStart())
async def cmd_start(message: Message, session):
    user = await session.scalar(select(User).where(User.id == message.from_user.id))
    if not user:
        new_user = User(id=message.from_user.id, username=message.from_user.username)
        session.add(new_user)
        await session.commit()
        await message.answer("Привет! Твой профиль создан в базе данных.")
    else:
        await message.answer(f"С возвращением! Твой баланс: {user.balance} монет.")
        
    await message.answer("Выбери действие:", reply_markup=get_main_menu())

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())

@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: CallbackQuery, session):
    user_id = callback.from_user.id
    if not config.channel_id:
        await callback.answer("Ошибка: Канал не настроен.", show_alert=True)
        return

    try:
        member = await bot.get_chat_member(chat_id=config.channel_id, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            user = await session.scalar(select(User).where(User.id == user_id))
            user.balance += 100
            await session.commit()
            await callback.message.edit_text(f"✅ Ты подписан!\nНачислено <b>100 монет</b>!\nБаланс: {user.balance}", reply_markup=get_main_menu())
        else:
            chat = await bot.get_chat(config.channel_id)
            invite_link = chat.invite_link or "Ссылка скрыта"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться", url=invite_link)],
                [InlineKeyboardButton(text="🔄 Проверить еще раз", callback_data="check_sub")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
            ])
            await callback.message.edit_text("❌ Ты не подписан на канал!", reply_markup=kb)
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        await callback.answer("Ошибка. Убедись, что бот админ в канале.", show_alert=True)
    await callback.answer()

async def check_matches():
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        upcoming = await session.scalars(
            select(Match).where(
                Match.winner == None,
                Match.is_announced == False,
                Match.start_time != None
            )
        )
        
        for match in upcoming.all():
            time_diff = (match.start_time - now).total_seconds() / 60
            if 0 < time_diff <= 60:
                users = await session.scalars(select(User))
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"🔵 {match.team1}", callback_data=f"vote_{match.id}_{match.team1}"),
                     InlineKeyboardButton(text=f"🔴 {match.team2}", callback_data=f"vote_{match.id}_{match.team2}")]
                ])
                
                for user in users.all():
                    try:
                        await bot.send_message(
                            chat_id=user.id,
                            text=f"⚔️ <b>Скоро матч!</b>\n\n<b>{match.team1}</b> vs <b>{match.team2}</b>\nВремя: {match.start_time.strftime('%Y-%m-%d %H:%M')} UTC\n\nУспей сделать ставку!",
                            reply_markup=kb
                        )
                    except Exception:
                        pass
                match.is_announced = True
                await session.commit()

async def on_startup(app: web.Application):
    await init_db()
    await bot.set_webhook(url=config.webhook_url, secret_token=config.webhook_secret, drop_pending_updates=True)
    
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(check_matches, 'interval', minutes=1)
    scheduler.start()
    
    logging.info(f"Вебхук установлен на {config.webhook_url}")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())

    dp.include_router(shop.router)
    dp.include_router(matches.router)
    
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=config.webhook_secret)
    webhook_requests_handler.register(app, path=config.webhook_path)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
