import logging
import os
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from typing import Callable, Dict, Any, Awaitable
from sqlalchemy import select
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import config
from database import init_db, async_session, User, Match, Admin
from handlers import shop, matches

logging.basicConfig(level=logging.INFO)

# ИСПРАВЛЕНИЕ 1: Добавлен parse_mode для работы HTML (жирный текст)
bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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

# ИСПРАВЛЕНИЕ 2: Используем ReplyKeyboardMarkup для кнопок ВНИЗУ экрана
def get_main_menu():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚔️ Ближайшие матчи"), KeyboardButton(text="🛒 Магазин")],
            [KeyboardButton(text="💰 Получить 100 монет")]
        ],
        resize_keyboard=True
    )
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
        
    await message.answer("Выбери действие на клавиатуре снизу:", reply_markup=get_main_menu())

@dp.message(F.text == "💰 Получить 100 монет")
async def check_subscription(message: Message, session):
    user_id = message.from_user.id
    if not config.channel_id:
        await message.answer("Ошибка: Канал не настроен.")
        return

    user = await session.scalar(select(User).where(User.id == user_id))
    
    if user.last_coin_reward is not None:
        await message.answer("⏳ Ты уже получал этот бонус! Он дается только один раз.")
        return

    try:
        member = await bot.get_chat_member(chat_id=config.channel_id, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            user.balance += 100
            user.last_coin_reward = datetime.utcnow()
            await session.commit()
            await message.answer(f"✅ Ты подписан!\nНачислено <b>100 монет</b>!\nБаланс: {user.balance}", reply_markup=get_main_menu())
        else:
            chat = await bot.get_chat(config.channel_id)
            invite_link = chat.invite_link or "Ссылка скрыта"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться", url=invite_link)]
            ])
            await message.answer("❌ Ты не подписан на канал! Подпишись и нажми кнопку еще раз.", reply_markup=kb)
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        await message.answer("Ошибка. Убедись, что бот админ в канале.")

async def fetch_pandascore_matches():
    if not config.pandascore_token:
        logging.warning("PANDASCORE_TOKEN не указан. Авто-парсинг отключен.")
        return
        
    logging.info("Загрузка матчей с PandaScore...")
    headers = {"Authorization": f"Bearer {config.pandascore_token}"}
    url = "https://api.pandascore.co/csgo/matches/upcoming?per_page=10"
    
    async with aiohttp.ClientSession() as client:
        try:
            async with client.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    async with async_session() as session:
                        new_matches = 0
                        for m in data:
                            exists = await session.scalar(select(Match).where(Match.pandascore_id == m['id']))
                            if not exists and m['opponents'] and len(m['opponents']) == 2:
                                team1 = m['opponents'][0]['opponent']['name']
                                team2 = m['opponents'][1]['opponent']['name']
                                start_time = datetime.strptime(m['begin_at'], "%Y-%m-%dT%H:%M:%SZ")
                                stream = "Ссылка появится позже"
                                if m.get('streams_list'):
                                    stream = m['streams_list'][0]['raw_url']
                                
                                new_match = Match(
                                    pandascore_id=m['id'],
                                    team1=team1,
                                    team2=team2,
                                    start_time=start_time,
                                    stream_link=stream
                                )
                                session.add(new_match)
                                new_matches += 1
                        await session.commit()
                        if new_matches > 0:
                            logging.info(f"Добавлено {new_matches} новых матчей.")
                else:
                    logging.error(f"Ошибка PandaScore: {response.status}")
        except Exception as e:
            logging.error(f"Ошибка при запросе к PandaScore: {e}")

async def check_matches():
    async with async_session() as session:
        now = datetime.utcnow()
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

async def check_admins():
    async with async_session() as session:
        now = datetime.utcnow()
        expired_admins = await session.scalars(
            select(Admin).where(Admin.is_active == True, Admin.expires_at <= now)
        )
        
        for admin in expired_admins.all():
            if config.target_chat_id:
                try:
                    await bot.promote_chat_member(
                        chat_id=config.target_chat_id,
                        user_id=admin.user_id,
                        can_change_info=False,
                        can_delete_messages=False,
                        can_invite_users=False,
                        can_restrict_members=False,
                        can_pin_messages=False,
                        can_promote_members=False
                    )
                    await bot.send_message(chat_id=admin.user_id, text="⏳ Твой срок админки истек. Права сняты.")
                except Exception:
                    pass
            admin.is_active = False
            await session.commit()

async def on_startup(app: web.Application):
    await init_db()
    await bot.set_webhook(url=config.webhook_url, secret_token=config.webhook_secret, drop_pending_updates=True)
    
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(check_matches, 'interval', minutes=1)
    scheduler.add_job(fetch_pandascore_matches, 'interval', hours=1) 
    scheduler.add_job(check_admins, 'interval', minutes=1) 
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
