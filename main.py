import asyncio
import datetime
import logging
import os
import re
import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "knowledge_manager.db"

class KnowledgeForm(StatesGroup):
    waiting_for_search_query = State()

router = Router()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_text TEXT NOT NULL,
                markdown_text TEXT NOT NULL,
                tags TEXT NOT NULL,
                note_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Add Quick Note"), KeyboardButton(text="Search Notes")],
            [KeyboardButton(text="Daily Summary"), KeyboardButton(text="My Notes")],
            [KeyboardButton(text="Help")]
        ],
        resize_keyboard=True
    )

def format_text_to_markdown(text: str):
    urls = re.findall(r"https?://[^\s]+", text)
    if urls:
        note_type = "LINK"
        tags = "link, web"
        formatted = f"**Type:** Link Resource\n**URL:** {urls[0]}\n**Details:** {text}\n**Tag:** link"
    elif "```" in text or "def " in text or "function" in text:
        note_type = "CODE"
        tags = "code, snippet"
        formatted = f"**Type:** Code Snippet\n**Code:**\n```\n{text}\n```\n**Tag:** code"
    else:
        note_type = "NOTE"
        tags = "note, text"
        lines = text.strip().split("\n")
        title = lines[0][:50]
        body = "\n".join(lines[1:]) if len(lines) > 1 else text
        formatted = f"**Title:** {title}\n**Body:** {body}\n**Tag:** note"
    return formatted, tags, note_type

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    welcome_text = (
        "Welcome to Personal AI Knowledge Manager!\n\n"
        "I will help you organize scattered text and links into structured markdown format.\n"
        "Send me any message or URL to save it automatically."
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@router.message(Command("help"))
@router.message(F.text == "Help")
async def cmd_help(message: Message):
    help_text = (
        "Knowledge Manager Features:\n\n"
        "1. Save Notes: Simply paste any text or URL in the chat.\n"
        "2. Search Notes: Tap Search Notes or use /search to find items.\n"
        "3. Daily Summary: Tap Daily Summary or use /summary for recent items.\n"
        "4. My Notes: View your saved list of structured markdown notes."
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

@router.message(F.text == "Add Quick Note")
async def prompt_add_note(message: Message):
    await message.answer("Send your text or link here and I will structure it for you.")

@router.message(F.text == "Search Notes")
@router.message(Command("search"))
async def prompt_search(message: Message, state: FSMContext):
    await state.set_state(KnowledgeForm.waiting_for_search_query)
    await message.answer("Type a keyword or category tag to search your notes:")

@router.message(KnowledgeForm.waiting_for_search_query)
async def process_search(message: Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, markdown_text, created_at FROM notes WHERE user_id = ? AND (original_text LIKE ? OR tags LIKE ?) ORDER BY id DESC LIMIT 5",
            (message.from_user.id, f"%{query}%", f"%{query}%")
        ) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await message.answer(f"No notes found matching: {query}", reply_markup=get_main_keyboard())
        return
        
    response = f"Search Results for '{query}':\n\n"
    for row in rows:
        response += f"ID {row[0]} | Date: {row[2][:10]}\n{row[1]}\n---\n"
    await message.answer(response, reply_markup=get_main_keyboard())

@router.message(F.text == "Daily Summary")
@router.message(Command("summary"))
async def cmd_summary(message: Message):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, note_type, markdown_text FROM notes WHERE user_id = ? AND created_at >= DATE('now', '-1 day') ORDER BY id DESC",
            (message.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await message.answer("No notes captured in the last 24 hours.", reply_markup=get_main_keyboard())
        return
        
    report = f"DAILY SUMMARY REPORT ({today})\nTotal Items Saved: {len(rows)}\n\n"
    for idx, row in enumerate(rows, 1):
        report += f"{idx}. [{row[1]}]\n{row[2]}\n---\n"
    await message.answer(report, reply_markup=get_main_keyboard())

@router.message(F.text == "My Notes")
async def list_notes(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, note_type, original_text FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (message.from_user.id,)
        ) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await message.answer("Your knowledge base is empty. Send a message to start!", reply_markup=get_main_keyboard())
        return
        
    keyboard_buttons = []
    for row in rows:
        short_text = row[2][:30].replace("\n", " ")
        keyboard_buttons.append([InlineKeyboardButton(text=f"[{row[1]}] {short_text}", callback_data=f"view_{row[0]}")])
        
    inline_kb = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer("Your recent knowledge items:", reply_markup=inline_kb)

@router.callback_query(F.data.startswith("view_"))
async def view_note_detail(callback: CallbackQuery):
    note_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT markdown_text, created_at FROM notes WHERE id = ? AND user_id = ?",
            (note_id, callback.from_user.id)
        ) as cursor:
            row = await cursor.fetchone()
            
    if row:
        detail_text = f"Note ID: {note_id}\nCreated: {row[1]}\n\n{row[0]}"
        delete_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Delete Note", callback_data=f"del_{note_id}")]
        ])
        await callback.message.edit_text(detail_text, reply_markup=delete_kb)
    else:
        await callback.answer("Note not found.")

@router.callback_query(F.data.startswith("del_"))
async def delete_note(callback: CallbackQuery):
    note_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, callback.from_user.id))
        await db.commit()
    await callback.answer("Note deleted successfully.")
    await callback.message.edit_text("Note has been removed from your knowledge manager.")

@router.message(F.text)
async def save_user_note(message: Message):
    user_id = message.from_user.id
    original_text = message.text
    formatted_md, tags, note_type = format_text_to_markdown(original_text)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO notes (user_id, original_text, markdown_text, tags, note_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, original_text, formatted_md, tags, note_type)
        )
        await db.commit()
        
    response = f"Saved to Knowledge Base!\n\n{formatted_md}"
    await message.answer(response, reply_markup=get_main_keyboard())

async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is missing.")
        
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    logging.info("Starting Knowledge Manager Bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
