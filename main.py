import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy import select
from config import config
from database import init_db, async_session, User

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.bot_token)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.id == message.from_user.id))
        if not user:
            new_user = User(id=message.from_user.id, username=message.from_user.username)
            session.add(new_user)
            await session.commit()
            await message.answer("Привет! Твой профиль создан в базе данных.")
        else:
            await message.answer(f"С возвращением! Твой баланс: {user.balance} монет.")

async def on_startup(app: web.Application):
    await init_db()
    await bot.set_webhook(
        url=config.webhook_url,
        secret_token=config.webhook_secret,
        drop_pending_updates=True
    )
    logging.info(f"Вебхук установлен на {config.webhook_url}")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=config.webhook_secret
    )
    webhook_requests_handler.register(app, path=config.webhook_path)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
