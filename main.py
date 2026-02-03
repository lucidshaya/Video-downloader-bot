import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import config
import yt_dlp
import re
import os
import time
import datetime
import requests
from urllib.parse import urlparse

# Initialize Bot
bot = telebot.TeleBot(config.token)

# --- Helpers ---

def get_progress_bar(stauts_info):
    """Generates a visual progress bar string."""
    try:
        if 'total_bytes' in stauts_info:
            total = stauts_info['total_bytes']
        elif 'total_bytes_estimate' in stauts_info:
            total = stauts_info['total_bytes_estimate']
        else:
            return "⏳ Processing..."
            
        downloaded = stauts_info.get('downloaded_bytes', 0)
        percentage = downloaded / total
        
        bar_len = 10
        filled_len = int(bar_len * percentage)
        bar = '▓' * filled_len + '░' * (bar_len - filled_len)
        
        percent_str = f"{percentage*100:.1f}%"
        size_str = f"{total / 1024 / 1024:.1f}MB"
        
        return f"Downloading: {bar} {percent_str}\n📦 Size: {size_str}"
    except:
        return "⏳ Downloading..."

def format_duration(seconds):
    if not seconds: return "Unknown"
    return str(datetime.timedelta(seconds=seconds))

def clean_filename(title):
    return "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).strip()

# --- Core Logic ---

last_edited = {}

def download_video_real(call, url, mode='best'):
    """
    Modes:
    - 'best': Best video+audio
    - 'mobile': 480p or lower
    - 'audio': Audio only (mp3)
    """
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Update status
    bot.edit_message_caption(
        chat_id=chat_id, 
        message_id=message_id, 
        caption=f"🚀 *Starting download...*\nMode: _{mode.title()}_", 
        parse_mode="Markdown"
    )
    
    output_dir = config.output_folder
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    timestamp =  int(time.time())
    
    ydl_opts = {
        'outtmpl': f'{output_dir}/{timestamp}_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'max_filesize': config.max_filesize
    }

    # Configure Quality
    if mode == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    elif mode == 'mobile':
        # Try to find mp4 < 720p, else best
        ydl_opts.update({'format': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best'})
    else: # limit to 1080p to valid Telegram upload limits usually
        ydl_opts.update({'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'})

    # Progress Hook
    def progress_hook(d):
        if d['status'] == 'downloading':
            key = f"{chat_id}-{message_id}"
            now = time.time()
            # Edit max every 2 seconds to avoid flood limits
            if key not in last_edited or (now - last_edited[key] > 2):
                try:
                    bar = get_progress_bar(d)
                    bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=f"⬇️ *Downloading...*\n\n{bar}",
                        parse_mode="Markdown"
                    )
                    last_edited[key] = now
                except Exception:
                    pass

    ydl_opts['progress_hooks'] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Find the file
            if mode == 'audio':
                ext = 'mp3'
            else:
                ext = info['ext']
                if mode == 'mobile' or mode == 'best': 
                     # Sometimes merger changes ext, usually mp4/mkv. 
                     # For simplicity, we search for the file created.
                     pass
            
            # Yt-dlp 'entries' check for playlists (not supported fully here, take first)
            if 'entries' in info:
                info = info['entries'][0]

            # Determine filepath. 
            # Note: prepare_filename often gives the pre-merged name. 
            # Simplest way is to look for files starting with timestamp in folder.
            target_file = None
            for f in os.listdir(output_dir):
                if f.startswith(str(timestamp)):
                    target_file = os.path.join(output_dir, f)
                    break
            
            if not target_file:
                raise Exception("File not found after download.")

            # Upload
            bot.edit_message_caption(
                chat_id=chat_id, 
                message_id=message_id, 
                caption="📤 *Uploading to Telegram...*\n_This may take a moment._",
                parse_mode="Markdown"
            )
            
            with open(target_file, 'rb') as f:
                if mode == 'audio':
                    bot.send_audio(
                        chat_id, f, 
                        title=info.get('title', 'Audio'), 
                        performer=info.get('uploader', 'Bot'),
                        caption=f"🎵 *{info.get('title')}*\nVia @{bot.get_me().username}",
                        parse_mode="Markdown"
                    )
                else:
                    # Get dimensions
                    w = info.get('width')
                    h = info.get('height')
                    bot.send_video(
                        chat_id, f,
                        width=w, height=h,
                        caption=f"🎬 *{info.get('title')}*\n✨ Quality: {mode.title()}\nVia @{bot.get_me().username}",
                        parse_mode="Markdown"
                    )
            
            # Cleanup
            bot.delete_message(chat_id, message_id)
            os.remove(target_file)

    except Exception as e:
        error_msg = str(e)
        if "Too Large" in error_msg or "File is too big" in error_msg:
             text = "❌ *File Too Large*\nTelegram bots are limited to 50MB uploads."
        else:
             text = f"❌ *Error Occurred*\n`{str(e)[:100]}...`"
             
        bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text, parse_mode="Markdown")


# --- Handlers ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Developer", url="https://t.me/YourHandle")) # Optional
    
    welcome_text = (
        f"👋 *Welcome, {message.from_user.first_name}!*\n\n"
        "I am a *Premium Media Downloader Bot*. 🚀\n"
        "Send me a link from:\n"
        "• YouTube\n"
        "• Instagram (Reels/Posts)\n"
        "• TikTok\n"
        "• Twitter / X\n"
        "• Facebook\n\n"
        "✨ *Features:*\n"
        "• Select Quality (HD / Data Saver)\n"
        "• Extract Audio (MP3)\n"
        "• Fast & Free\n\n"
        "👇 _Just paste a link to start!_"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    text = message.text
    if not text: return

    # 1. Handle .ics (Legacy / Requested feature)
    if text.lower().endswith('.ics') or '.ics?' in text.lower():
         handle_ics_download(message)
         return

    # 2. Check for URLs
    url_match = re.search(r'(https?://\S+)', text)
    if not url_match:
        bot.reply_to(message, "⚠️ No valid link found. Please send a video URL.")
        return
    
    url = url_match.group(0)
    
    # 3. Fetch Info (Metadata)
    status_msg = bot.reply_to(message, "🔎 *Analyzing Link...*", parse_mode="Markdown")
    
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            
            title = info.get('title', 'Unknown Title')
            duration = format_duration(info.get('duration'))
            uploader = info.get('uploader', 'Unknown Author')
            thumbnail = info.get('thumbnail')
            views = info.get('view_count', 0)
            
            # Prepare Menu
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("🎬 Best Quality", callback_data=f"dl|best|{url[:20]}"), # Shorten URL in data if needed, but we rely on state or re-parsing. 
                # Note: Telegram Callback Data limit is 64 bytes. URLs are too long.
                # SOLUTION: We will store the full URL in memory or just use the message text reference.
                # BETTER: Just pass a tiny ID and store in dict? Or just Extract from reply_to text?
                # Simplest for this context: Store URL in memory temporarily or re-read from msg.
                # We'll use a hack: pass 'dl|mode' and read URL from the message being replied to (if we attached menu to it)
                # But we are sending a NEW message with photo. 
                # Let's simple use a global dict with key = message_id of the menu
            )
            markup.row(
                InlineKeyboardButton("📱 Mobile (480p)", callback_data="dl|mobile"),
                InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl|audio")
            )
            markup.add(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))

            # Store URL mapping for this specific UI message
            # The 'message_id' will be the one involved in the button click.
            # But we don't know the message_id of the photo message until sent.
            # So we send photo, GET message_id, then store.
            
            caption = (
                f"🎬 *{title}*\n\n"
                f"👤 *Channel:* {uploader}\n"
                f"⏱ *Duration:* {duration}\n"
                f"👁 *Views:* {views:,}\n\n"
                "👇 *Select format:* "
            )
            
            bot.delete_message(message.chat.id, status_msg.message_id) # Delete "Analyzing..."
            
            if thumbnail:
                sent_msg = bot.send_photo(
                    message.chat.id, 
                    thumbnail, 
                    caption=caption, 
                    parse_mode="Markdown", 
                    reply_markup=markup,
                    reply_to_message_id=message.message_id
                )
            else:
                sent_msg = bot.send_message(
                    message.chat.id, 
                    caption, 
                    parse_mode="Markdown", 
                    reply_markup=markup
                )
            
            # Store the URL for this message interaction
            # We use the bot's sent message ID as key
            url_storage[sent_msg.message_id] = url

    except Exception as e:
        bot.edit_message_text(f"⚠️ *Invalid Link or Error*\n`{str(e)}`", message.chat.id, status_msg.message_id, parse_mode="Markdown")

# Global storage for URLs linked to menu messages
url_storage = {} 

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    data = call.data
    
    if data == "cancel":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return

    if data.startswith("dl|"):
        mode = data.split("|")[1]
        
        # Retrieve URL
        url = url_storage.get(call.message.message_id)
        if not url:
            bot.answer_callback_query(call.id, "❌ Session expired. Please send link again.")
            return
            
        # Clean storage
        del url_storage[call.message.message_id]
        
        download_video_real(call, url, mode)

def handle_ics_download(message):
    # (Same ICS logic as before, just wrapped)
    url_match = re.search(r'(https?://\S+)', message.text)
    if not url_match: return
    url = url_match.group(0)
    
    if not os.path.exists(config.output_folder):
        os.makedirs(config.output_folder)
        
    try:
        msg = bot.reply_to(message, '📅 *Downloading iCalendar...*', parse_mode="Markdown")
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            filename = url.split('/')[-1].split('?')[0] or "calendar.ics"
            filepath = os.path.join(config.output_folder, filename)
            with open(filepath, 'wb') as f: f.write(response.content)
            
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text='📤 Sending file...')
            with open(filepath, 'rb') as f:
                bot.send_document(message.chat.id, f)
            
            bot.delete_message(message.chat.id, msg.message_id)
            os.remove(filepath)
    except Exception as e:
        bot.edit_message_text(f'❌ Error: {e}', message.chat.id, message.message_id)

print("Premium Bot Started...")
bot.infinity_polling()