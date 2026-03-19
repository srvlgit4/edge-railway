#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
import asyncio
import edge_tts
import nest_asyncio
import re
import random
import tempfile
import shutil
import logging
from pathlib import Path
from threading import Thread

# Apply nest_asyncio
nest_asyncio.apply()

# ==========================================
# CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = "8712072214:AAEJl5SW1TPisPZb7tiQbYolv-QlDvo_tTU"
VOICE = "hi-IN-MadhurNeural"
RATE = "+30%"
VOLUME = "+20%"
MAX_CONCURRENT_DOWNLOADS = 5 # Reduced slightly for cloud stability
CHUNK_SIZE = 2500
EPISODE_SIZE = 35000

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- RAILWAY HEALTH CHECK SERVER ---
try:
    from flask import Flask
    server = Flask(__name__)
    @server.route('/')
    def health(): return "Bot is Online", 200
except ImportError:
    logger.error("Flask not installed. Please add it to requirements.txt")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)

# ==========================================
# CORE FUNCTIONS (Your Original Logic)
# ==========================================
def clean_text(text):
    if not text: return ""
    text = text.replace("\n", " ").replace("अध्याय", "\nअध्याय").replace(",\n", " ")
    text = re.sub(r'[^\w\s\.\,\!\?\"\'।\u200C\u200D\u0900-\u097F\-]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def split_text_by_length(text, max_chars):
    sentences = re.split(r'(?<=[।?!.\n])\s+', text)
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_chars:
            current_chunk += sentence + " "
        else:
            chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    if current_chunk: chunks.append(current_chunk.strip())
    return chunks

async def tts_chunk(text, filename, timeout=120):
    try:
        communicate = edge_tts.Communicate(text=text, voice=VOICE, rate=RATE, volume=VOLUME)
        await asyncio.wait_for(communicate.save(filename), timeout=timeout)
        return True
    except Exception as e:
        logger.warning(f"TTS error: {e}")
        return False

async def process_episode_strict_dealer(chunk_data_list, status_msg, episode_num, total_episodes):
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    tasks = []
    completed_chunks = 0
    async def strict_worker(chunk_txt, chunk_mp3, chunk_idx, total_chunks):
        nonlocal completed_chunks
        async with semaphore:
            for attempt in range(1, 4):
                try:
                    if completed_chunks % 3 == 0: # Update every 3 chunks to avoid ban
                        await status_msg.edit_text(f"🎙️ Ep {episode_num}/{total_episodes}: {completed_chunks}/{total_chunks} chunks done...")
                    if await tts_chunk(chunk_txt, chunk_mp3):
                        completed_chunks += 1
                        return True
                except: await asyncio.sleep(5)
            return False

    for index, (text, filename, idx, total) in enumerate(chunk_data_list):
        tasks.append(asyncio.create_task(strict_worker(text, filename, idx, total)))
        await asyncio.sleep(random.uniform(1, 3))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [chunk_data_list[i][1] for i, res in enumerate(results) if res is True]

# ==========================================
# TELEGRAM BOT
# ==========================================
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot is Ready on Railway!\n📤 Send me a .txt file.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if not doc.file_name.lower().endswith('.txt'): return
        status_msg = await update.message.reply_text("📥 Processing...")
        file = await context.bot.get_file(doc.file_id)
        file_content = await file.download_as_bytearray()
        text = file_content.decode('utf-8', errors='ignore')
        episodes = split_text_by_length(clean_text(text), EPISODE_SIZE)
        temp_dir = tempfile.mkdtemp()
        try:
            for ep_idx, episode_text in enumerate(episodes):
                ep_num = ep_idx + 1
                network_chunks = split_text_by_length(episode_text, CHUNK_SIZE)
                chunk_data = [(t, os.path.join(temp_dir, f"e{ep_num}_p{j}.mp3"), j+1, len(network_chunks)) for j, t in enumerate(network_chunks)]
                successful_chunks = await process_episode_strict_dealer(chunk_data, status_msg, ep_num, len(episodes))
                
                list_f = os.path.join(temp_dir, f"l_{ep_num}.txt")
                final_mp3 = os.path.join(temp_dir, f"E_{ep_num}.mp3")
                with open(list_f, "w", encoding="utf-8") as f:
                    for cf in successful_chunks: f.write(f"file '{cf}'\n")
                
                p = await asyncio.create_subprocess_exec("ffmpeg", "-f", "concat", "-safe", "0", "-i", list_f, "-c", "copy", final_mp3, "-y", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(p.communicate(), timeout=300)
                
                if os.path.exists(final_mp3):
                    with open(final_mp3, 'rb') as audio:
                        await context.bot.send_audio(chat_id=update.message.chat_id, audio=audio, title=f"Episode {ep_num}")
                
                for cf in successful_chunks: (os.remove(cf) if os.path.exists(cf) else None)
                if os.path.exists(list_f): os.remove(list_f)
                if os.path.exists(final_mp3): os.remove(final_mp3)
            await status_msg.edit_text("✅ All Episodes Sent!")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:
        logger.error(f"Error: {e}")

def main():
    # Start Railway Health Check
    Thread(target=run_health_server, daemon=True).start()

    # Start Bot
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    logger.info("🤖 Bot is starting on Railway...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
