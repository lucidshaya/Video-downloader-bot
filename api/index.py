from flask import Flask, request
import telebot
import os
import sys

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import existing bot logic components if possible, 
# but for Vercel it is safer to redefine the handler in a clean way 
# or import a refactored module. 
# To avoid complex refactoring steps, I will reimplement the handler setup here
# importing the necessary libraries.

import config
import yt_dlp
import re
import time
import datetime
import requests
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import logging

# Configure logging to stdout
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Vercel freezes the process immediately after response. 
# Threading MUST be disabled so handlers run synchronously before we return.
bot = telebot.TeleBot(config.token, threaded=False)

# --- Re-use Logic (Simplified for Vercel Timeouts) ---
# Vercel has strict timeouts (10s on Free). Large downloads WILL fail.
# We modify logic to be as fast as possible.

def format_duration(seconds):
    if not seconds: return "Unknown"
    return str(datetime.timedelta(seconds=seconds))

def download_video_vercel(message, url, mode='best'):
    chat_id = message.chat.id
    message_id = message.message_id # This might be the button callback message
    
    # Check for vercel temp folder
    output_dir = "/tmp" 
    timestamp = int(time.time())
    
    # Quick Status
    try:
        if hasattr(message, 'message_id'):
            # It's a message or callback message
            bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption="⏳ *Vercel: Processing...*\n_Note: Large videos may timeout._", parse_mode="Markdown")
    except:
        pass

    ydl_opts = {
        'outtmpl': f'{output_dir}/{timestamp}_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'max_filesize': 45000000, 
        'format': 'best[ext=mp4]/best',
        'cache_dir': '/tmp/yt-dlp-cache', # Fix for Read-only file system
        'noplaylist': True
    }
    
    if mode == 'audio':
        ydl_opts['format'] = 'bestaudio/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Send status
            status_msg = bot.send_message(chat_id, "⏳ Finding video...")
            
            info = ydl.extract_info(url, download=True)
            
            # Find file
            target_file = None
            for f in os.listdir(output_dir):
                if f.startswith(str(timestamp)):
                    target_file = os.path.join(output_dir, f)
                    break
            
            if not target_file:
                 bot.edit_message_text("❌ Download failed: File not found.", chat_id, status_msg.message_id)
                 return

            # Upload
            bot.edit_message_text("📤 Uploading...", chat_id, status_msg.message_id)
            
            with open(target_file, 'rb') as f:
                if mode == 'audio':
                    bot.send_audio(chat_id, f, caption=f"🎵 {info.get('title')}")
                else:
                    bot.send_video(chat_id, f, caption=f"🎬 {info.get('title')}")
            
            # Cleanup
            bot.delete_message(chat_id, status_msg.message_id)
            os.remove(target_file)

    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {str(e)}")

# --- Routes ---

@app.route('/')
def home():
    return "Bot is running on Vercel!"

@app.route('/api/webhook', methods=['POST'])
def webhook():
    try:
        if request.headers.get('content-type') == 'application/json':
            json_string = request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_string)
            bot.process_new_updates([update])
            return ''
        else:
            return 'error', 403
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return 'error', 500

# --- Bot Handlers (Copied/Adapted) ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "👋 *Vercel Bot Ready*\nSend a link (TikTok/Reels work best due to timeout limits).", parse_mode="Markdown")

url_storage = {}

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    text = message.text
    if not text: return
    
    url_match = re.search(r'(https?://\S+)', text)
    if not url_match:
        bot.reply_to(message, "Send a video link.")
        return
    
    url = url_match.group(0)
    
    # Direct download for speed on Vercel (Skip menu to save time? Or keep menu?)
    # Let's keep menu but minimal.
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Download Fast", callback_data=f"dl|{url[:15]}"))
    
    bot.reply_to(message, "👇 Tap to download", reply_markup=markup)
    
    # Store full URL
    # Hack: We use the text of the message or just start download immediately?
    # On Serverless, we can't easily share state between the request that sent the menu and the callback request (different instances).
    # We MUST encode the data in the button or use a stateless DB (Redis).
    # Since we lack Redis here, we will try to just DOWNLOAD IMMEDIATELY or use a Trick.
    # Trick: We can't use `url_storage` global variable on Vercel efficiently across requests.
    # So for Vercel version, let's just download immediately to avoid state issues.
    
    download_video_vercel(message, url, mode='best')


# Note: Callbacks with state won't work well on serverless without a DB.
# above handle_message calls download immediately to simulate "Auto" mode.

