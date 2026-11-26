from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from sqlalchemy import select
from database import User, Match, Prediction

router = Router()

@router.message(Command("add_match"))
async def add_match(message: Message, session):
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Используй формат: <code>/add_match Команда1 Команда2</code>")
        return
    
    team1, team2 = args[1], args[2]
    new_match = Match(team1=team1, team2=team2)
    session.add(new_match)
    await session.commit()
    await message.answer(f"✅ Матч добавлен!\nID: {new_match.id}\n{team1} vs {team2}")

@router.message(Command("announce"))
async def announce_match(message: Message, session):
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Используй формат: <code>/announce ID_матча</code>")
        return
    
    match_id = int(args[1])
    match = await session.scalar(select(Match).where(Match.id == match_id))
    if not match:
        await message.answer("Матч не найден.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"🔵 {match.team1}", callback_data=f"vote_{match.id}_{match.team1}"),
            InlineKeyboardButton(text=f"🔴 {match.team2}", callback_data=f"vote_{match.id}_{match.team2}")
        ]
    ])
    
    await message.answer(
        f"⚔️ <b>Сегодняшний матч!</b>\n\n"
        f"Кто победит: <b>{match.team1}</b> или <b>{match.team2}</b>?\n"
        f"За правильный ответ — <b>100 монет</b>!",
        reply_markup=kb
    )

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
