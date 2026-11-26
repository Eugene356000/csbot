from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from sqlalchemy import select
from datetime import datetime, timezone
from database import User, Match, Prediction

router = Router()

@router.callback_query(F.data == "upcoming_matches")
async def show_upcoming_matches(callback: CallbackQuery, session):
    matches = await session.scalars(select(Match).where(Match.winner == None))
    matches_list = matches.all()

    if not matches_list:
        await callback.message.edit_text(
            "Пока нет предстоящих матчей. Загляни позже!", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="main_menu")]])
        )
        await callback.answer()
        return

    text = "⚔️ <b>Ближайшие матчи:</b>\n\n"
    for match in matches_list:
        time_str = match.start_time.strftime('%Y-%m-%d %H:%M') if match.start_time else "Время уточняется"
        text += f"🆔 ID: {match.id} | <b>{match.team1}</b> vs <b>{match.team2}</b>\n⏰ {time_str} (UTC)\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="main_menu")]])
    )
    await callback.answer()

@router.message(Command("add_match"))
async def add_match(message: Message, session):
    args = message.text.split()
    if len(args) < 5:
        await message.answer("Формат: <code>/add_match Команда1 Команда2 2024-12-01 20:00 Ссылка_на_стрим</code>")
        return
    
    team1, team2 = args[1], args[2]
    time_str = f"{args[3]} {args[4]}"
    stream_link = args[5] if len(args) > 5 else "Ссылка появится позже"
    
    try:
        match_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        await message.answer("Ошибка формата времени. Используй: YYYY-MM-DD HH:MM (по UTC)")
        return

    new_match = Match(team1=team1, team2=team2, start_time=match_time, stream_link=stream_link)
    session.add(new_match)
    await session.commit()
    
    await message.answer(f"✅ Матч добавлен!\nID: {new_match.id}\n{team1} vs {team2}\nВремя (UTC): {match_time}\nСтрим: {stream_link}")

@router.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: CallbackQuery, session):
    _, match_id_str, predicted_team = callback.data.split("_")
    match_id = int(match_id_str)
    user_id = callback.from_user.id

    existing = await session.scalar(select(Prediction).where(Prediction.user_id == user_id, Prediction.match_id == match_id))
    if existing:
        await callback.answer("Ты уже сделал выбор!", show_alert=True)
        return

    new_prediction = Prediction(user_id=user_id, match_id=match_id, predicted_team=predicted_team)
    session.add(new_prediction)
    await session.commit()

    await callback.answer(f"Твой выбор: {predicted_team}. Ждем результатов!", show_alert=True)
    await callback.message.edit_text(f"⚔️ Матч!\nТы выбрал: <b>{predicted_team}</b>")

@router.message(Command("set_winner"))
async def set_winner(message: Message, session):
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Используй формат: <code>/set_winner ID_матча Победитель</code>")
        return

    match_id = int(args[1])
    winner_team = args[2]
    
    match = await session.scalar(select(Match).where(Match.id == match_id))
    if not match:
        await message.answer("Матч не найден.")
        return
        
    match.winner = winner_team
    await session.commit()

    correct_predictions = await session.scalars(select(Prediction).where(Prediction.match_id == match_id, Prediction.predicted_team == winner_team))
    
    winners_count = 0
    for pred in correct_predictions:
        user = await session.scalar(select(User).where(User.id == pred.user_id))
        if user:
            user.balance += 100
            winners_count += 1
            try:
                await message.bot.send_message(chat_id=user.id, text=f"🎉 Ты угадал победителя!\nНачислено <b>100 монет</b>.\nБаланс: {user.balance}")
            except Exception:
                pass
                
    await session.commit()
    await message.answer(f"✅ Матч завершен! Победитель: {winner_team}.\nМонеты начислены {winners_count} игрокам.")
