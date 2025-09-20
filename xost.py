import asyncio
import logging
import os
import sqlite3
import subprocess
import signal
import json
from datetime import datetime
import psutil

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

API_TOKEN = "7642235798:AAGk2qpl6l8NQ9dGJTSY7Kn8gjw0bUsmPno"
ADMIN_ID = 8285579114
FILES_DIR = "uploaded_bots"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
os.makedirs(FILES_DIR, exist_ok=True)

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# FSM holatlari
class AdminStates(StatesGroup):
    waiting_broadcast_message = State()
    waiting_user_message = State()
    waiting_system_command = State()

# Ma'lumotlar bazasi bilan ishlash
def get_db_connection():
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        approved INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        username TEXT,
        full_name TEXT,
        registered_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filename TEXT,
        status TEXT DEFAULT 'running',
        uploaded_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        pid INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS statistics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        total_users INTEGER DEFAULT 0,
        active_users INTEGER DEFAULT 0,
        total_bots INTEGER DEFAULT 0,
        running_bots INTEGER DEFAULT 0,
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    conn.commit()
    conn.close()

init_db()

def is_user_approved(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT approved FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None and row[0] == 1

def is_user_banned(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None and row[0] == 1

def approve_user(user_id: int, username: str = None, full_name: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, approved, banned, username, full_name, last_activity) VALUES (?, 1, 0, ?, ?, datetime('now'))",
        (user_id, username, full_name)
    )
    conn.commit()
    conn.close()

def ban_user(user_id: int, username: str = None, full_name: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, approved, banned, username, full_name, last_activity) VALUES (?, 0, 1, ?, ?, datetime('now'))",
        (user_id, username, full_name)
    )
    conn.commit()
    conn.close()

def unban_user(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned = 0, last_activity = datetime('now') WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_banned_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name FROM users WHERE banned = 1")
    users = cursor.fetchall()
    conn.close()
    return users

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name, approved, banned, registered_date, last_activity FROM users")
    users = cursor.fetchall()
    conn.close()
    return users

def get_user_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total FROM users")
    total_users = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as active FROM users WHERE approved = 1 AND banned = 0")
    active_users = cursor.fetchone()['active']
    
    cursor.execute("SELECT COUNT(*) as total FROM bots")
    total_bots = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as running FROM bots WHERE status = 'running'")
    running_bots = cursor.fetchone()['running']
    
    conn.close()
    
    return {
        'total_users': total_users,
        'active_users': active_users,
        'total_bots': total_bots,
        'running_bots': running_bots
    }

def update_user_activity(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_activity = datetime('now') WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_bot_to_db(user_id: int, filename: str, pid: int = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bots (user_id, filename, pid) VALUES (?, ?, ?)",
        (user_id, filename, pid)
    )
    conn.commit()
    conn.close()

def get_user_bots(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename, status, uploaded_date, pid FROM bots WHERE user_id = ?", (user_id,))
    bots = cursor.fetchall()
    conn.close()
    return bots

def get_all_bots():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT b.*, u.username, u.full_name 
        FROM bots b 
        LEFT JOIN users u ON b.user_id = u.user_id 
        ORDER BY b.uploaded_date DESC
    """)
    bots = cursor.fetchall()
    conn.close()
    return bots

def update_bot_status(user_id: int, filename: str, status: str, pid: int = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if pid:
        cursor.execute(
            "UPDATE bots SET status = ?, pid = ? WHERE user_id = ? AND filename = ?",
            (status, pid, user_id, filename)
        )
    else:
        cursor.execute(
            "UPDATE bots SET status = ? WHERE user_id = ? AND filename = ?",
            (status, user_id, filename)
        )
    conn.commit()
    conn.close()

def delete_bot_from_db(user_id: int, filename: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bots WHERE user_id = ? AND filename = ?", (user_id, filename))
    conn.commit()
    conn.close()

def get_bot_by_pid(pid: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bots WHERE pid = ?", (pid,))
    bot = cursor.fetchone()
    conn.close()
    return bot

# ==================== ADMIN PANEL ====================

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Sizda ruxsat yo'q.")
    
    stats = get_user_stats()
    
    text = (
        f"👑 <b>Admin Panel</b>\n\n"
        f"📊 Statistika:\n"
        f"• 👥 Jami foydalanuvchilar: {stats['total_users']}\n"
        f"• ✅ Faol foydalanuvchilar: {stats['active_users']}\n"
        f"• 🤖 Jami botlar: {stats['total_bots']}\n"
        f"• 🟢 Ishlayotgan botlar: {stats['running_bots']}\n\n"
        f"⚡ Server holati:\n"
        f"• CPU: {psutil.cpu_percent()}%\n"
        f"• RAM: {psutil.virtual_memory().percent}%\n"
        f"• Disk: {psutil.disk_usage('/').percent}%"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton(text="🤖 Botlar", callback_data="admin_bots")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⚙️ Tizim", callback_data="admin_system")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="admin_refresh")]
    ])
    
    await message.answer(text, reply_markup=keyboard)

@dp.callback_query(F.data == "admin_refresh")
async def admin_refresh(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    stats = get_user_stats()
    
    text = (
        f"👑 <b>Admin Panel</b>\n\n"
        f"📊 Statistika:\n"
        f"• 👥 Jami foydalanuvchilar: {stats['total_users']}\n"
        f"• ✅ Faol foydalanuvchilar: {stats['active_users']}\n"
        f"• 🤖 Jami botlar: {stats['total_bots']}\n"
        f"• 🟢 Ishlayotgan botlar: {stats['running_bots']}\n\n"
        f"⚡ Server holati:\n"
        f"• CPU: {psutil.cpu_percent()}%\n"
        f"• RAM: {psutil.virtual_memory().percent}%\n"
        f"• Disk: {psutil.disk_usage('/').percent}%"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton(text="🤖 Botlar", callback_data="admin_bots")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⚙️ Tizim", callback_data="admin_system")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="admin_refresh")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("✅ Yangilandi")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    stats = get_user_stats()
    users = get_all_users()
    
    # Faol foydalanuvchilar (oxirgi 7 kun)
    active_recent = 0
    seven_days_ago = datetime.now().timestamp() - 7 * 24 * 3600
    
    for user in users:
        last_activity = datetime.strptime(user['last_activity'], "%Y-%m-%d %H:%M:%S").timestamp()
        if last_activity > seven_days_ago and user['approved'] and not user['banned']:
            active_recent += 1
    
    text = (
        f"📊 <b>Batafsil Statistika</b>\n\n"
        f"👥 Foydalanuvchilar:\n"
        f"• Jami: {stats['total_users']}\n"
        f"• Faol: {stats['active_users']}\n"
        f"• Oxirgi 7 kun faol: {active_recent}\n"
        f"• Banlangan: {len(get_banned_users())}\n\n"
        f"🤖 Botlar:\n"
        f"• Jami: {stats['total_bots']}\n"
        f"• Ishlayotgan: {stats['running_bots']}\n"
        f"• To'xtatilgan: {stats['total_bots'] - stats['running_bots']}\n\n"
        f"📈 Server yuki:\n"
        f"• CPU: {psutil.cpu_percent()}%\n"
        f"• RAM: {psutil.virtual_memory().percent}% ({psutil.virtual_memory().used//1024//1024}MB/{psutil.virtual_memory().total//1024//1024}MB)\n"
        f"• Disk: {psutil.disk_usage('/').percent}%"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    users = get_all_users()
    
    text = "👥 <b>Barcha Foydalanuvchilar</b>\n\n"
    for user in users[:10]:  # Faqat birinchi 10 tasi
        status = "✅" if user['approved'] else "❌"
        if user['banned']:
            status = "🚫"
        text += f"{status} <code>{user['user_id']}</code> - @{user['username'] or 'N/A'}\n"
    
    if len(users) > 10:
        text += f"\n... va yana {len(users) - 10} ta foydalanuvchi"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Export JSON", callback_data="admin_export_users")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_export_users")
async def admin_export_users(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    users = get_all_users()
    users_data = []
    
    for user in users:
        users_data.append({
            'user_id': user['user_id'],
            'username': user['username'],
            'full_name': user['full_name'],
            'approved': bool(user['approved']),
            'banned': bool(user['banned']),
            'registered_date': user['registered_date'],
            'last_activity': user['last_activity']
        })
    
    with open('users_export.json', 'w', encoding='utf-8') as f:
        json.dump(users_data, f, ensure_ascii=False, indent=2)
    
    await callback.message.answer_document(
        FSInputFile('users_export.json'),
        caption="📊 Foydalanuvchilar ro'yxati"
    )
    os.remove('users_export.json')
    await callback.answer()

@dp.callback_query(F.data == "admin_bots")
async def admin_bots(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    bots = get_all_bots()
    
    text = "🤖 <b>Barcha Botlar</b>\n\n"
    for bot in bots[:5]:  # Faqat birinchi 5 tasi
        status = "🟢" if bot['status'] == 'running' else "🔴"
        text += f"{status} <code>{bot['filename']}</code>\n"
        text += f"   👤 {bot['user_id']} (@{bot['username'] or 'N/A'})\n"
        text += f"   📅 {bot['uploaded_date']}\n\n"
    
    if len(bots) > 5:
        text += f"... va yana {len(bots) - 5} ta bot"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Botlar statistikasi", callback_data="admin_bots_stats")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_bots_stats")
async def admin_bots_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    bots = get_all_bots()
    users_bots = {}
    
    for bot in bots:
        user_id = bot['user_id']
        if user_id not in users_bots:
            users_bots[user_id] = 0
        users_bots[user_id] += 1
    
    top_users = sorted(users_bots.items(), key=lambda x: x[1], reverse=True)[:5]
    
    text = "📊 <b>Botlar Statistikasi</b>\n\n"
    text += "👤 Eng ko'p bot yuklagan foydalanuvchilar:\n"
    for user_id, count in top_users:
        text += f"• <code>{user_id}</code> - {count} ta bot\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_bots")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    await state.set_state(AdminStates.waiting_broadcast_message)
    await callback.message.edit_text(
        "📢 <b>Broadcast xabarini yuboring</b>\n\n"
        "Xabarning formatini belgilang:\n"
        "• <code>text</code> - Oddiy matn\n"
        "• <code>html</code> - HTML formatida\n"
        "• <code>markdown</code> - Markdown formatida\n\n"
        "Bekor qilish uchun /cancel buyrug'ini yuboring."
    )
    await callback.answer()

@dp.message(AdminStates.waiting_broadcast_message)
async def admin_broadcast_send(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    users = get_all_users()
    active_users = [user for user in users if user['approved'] and not user['banned']]
    
    await message.answer(f"📢 Xabar {len(active_users)} ta foydalanuvchiga yuborilmoqda...")
    
    success = 0
    failed = 0
    
    for user in active_users:
        try:
            await bot.send_message(chat_id=user['user_id'], text=message.text, parse_mode=ParseMode.HTML)
            success += 1
            await asyncio.sleep(0.1)  # Spamdan qochish uchun
        except Exception as e:
            failed += 1
            logger.error(f"Xabar yuborishda xato {user['user_id']}: {e}")
    
    await message.answer(
        f"✅ Broadcast natijasi:\n\n"
        f"• ✅ Muvaffaqiyatli: {success}\n"
        f"• ❌ Xatolik: {failed}\n"
        f"• 📊 Jami: {len(active_users)}"
    )
    
    await state.clear()

@dp.callback_query(F.data == "admin_system")
async def admin_system(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    text = (
        "⚙️ <b>Tizim Boshqaruvi</b>\n\n"
        "Quyidagi amallarni bajarishingiz mumkin:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Barcha botlarni qayta ishga tushirish", callback_data="admin_restart_all")],
        [InlineKeyboardButton(text="🛑 Barcha botlarni to'xtatish", callback_data="admin_stop_all")],
        [InlineKeyboardButton(text="💻 Terminal", callback_data="admin_terminal")],
        [InlineKeyboardButton(text="📊 Tizim holati", callback_data="admin_system_status")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_restart_all")
async def admin_restart_all(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    bots = get_all_bots()
    restarted = 0
    
    for bot_info in bots:
        try:
            user_id = bot_info['user_id']
            filename = bot_info['filename']
            file_path = os.path.join(FILES_DIR, str(user_id), filename)
            pid_path = file_path + ".pid"
            log_path = file_path + ".log"
            
            # Avvalgi jarayonni to'xtatish
            if os.path.exists(pid_path):
                with open(pid_path, "r") as f:
                    pid = f.read().strip()
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except:
                    pass
            
            # Yangi jarayonni ishga tushirish
            process = subprocess.Popen(
                f"nohup python3 {file_path} > {log_path} 2>&1 & echo $! > {pid_path}",
                shell=True
            )
            
            with open(pid_path, "r") as f:
                new_pid = f.read().strip()
            
            update_bot_status(user_id, filename, "running", new_pid)
            restarted += 1
            
        except Exception as e:
            logger.error(f"Botni qayta ishga tushirishda xato {filename}: {e}")
    
    await callback.answer(f"✅ {restarted} ta bot qayta ishga tushirildi")

@dp.callback_query(F.data == "admin_stop_all")
async def admin_stop_all(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    bots = get_all_bots()
    stopped = 0
    
    for bot_info in bots:
        try:
            user_id = bot_info['user_id']
            filename = bot_info['filename']
            file_path = os.path.join(FILES_DIR, str(user_id), filename)
            pid_path = file_path + ".pid"
            
            if os.path.exists(pid_path):
                with open(pid_path, "r") as f:
                    pid = f.read().strip()
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    update_bot_status(user_id, filename, "stopped")
                    stopped += 1
                except:
                    pass
            
        except Exception as e:
            logger.error(f"Botni to'xtatishda xato {filename}: {e}")
    
    await callback.answer(f"✅ {stopped} ta bot to'xtatildi")

@dp.callback_query(F.data == "admin_system_status")
async def admin_system_status(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    # Tizim ma'lumotlari
    cpu_usage = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    
    text = (
        "📊 <b>Tizim Holati</b>\n\n"
        f"🖥️ CPU: {cpu_usage}%\n"
        f"💾 RAM: {memory.percent}% ({memory.used//1024//1024}MB/{memory.total//1024//1024}MB)\n"
        f"💿 Disk: {disk.percent}% ({disk.used//1024//1024}MB/{disk.total//1024//1024}MB)\n"
        f"⏰ Ishlash vaqti: {(datetime.now() - boot_time).days} kun\n"
        f"📈 Processlar: {len(psutil.pids())} ta"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="admin_system_status")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_system")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_terminal")
async def admin_terminal_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    await state.set_state(AdminStates.waiting_system_command)
    await callback.message.edit_text(
        "💻 <b>Terminal Buyrug'i</b>\n\n"
        "Ishlatmoqchi bo'lgan buyrug'ingizni yuboring.\n"
        "Misol: <code>ls -la</code>, <code>df -h</code>, <code>ps aux</code>\n\n"
        "Bekor qilish uchun /cancel buyrug'ini yuboring."
    )
    await callback.answer()

@dp.message(AdminStates.waiting_system_command)
async def admin_terminal_execute(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    command = message.text.strip()
    if not command:
        await message.answer("❗ Buyruq kiritilmadi.")
        return
    
    await message.answer(f"💻 Buyruq bajarilmoqda: <code>{command}</code>")
    
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if stdout:
            output = stdout.decode().strip()
            if len(output) > 4000:
                with open('command_output.txt', 'w') as f:
                    f.write(output)
                await message.answer_document(FSInputFile('command_output.txt'), caption="📤 Buyruq natijasi")
                os.remove('command_output.txt')
            else:
                await message.answer(f"📤 Natija:\n<pre>{output}</pre>")
        
        if stderr:
            error = stderr.decode().strip()
            await message.answer(f"❌ Xato:\n<pre>{error}</pre>")
            
    except Exception as e:
        await message.answer(f"❌ Xato: {e}")
    
    await state.clear()

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
    
    await admin_panel(callback.message)
    await callback.answer()

@dp.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer("❌ Amal bekor qilindi.")
    await admin_panel(message)

# ==================== USER COMMANDS ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    if is_user_banned(user_id):
        return await message.answer("🚫 Siz botdan foydalanishdan banlangansiz.")
    if is_user_approved(user_id):
        return await message.answer("✅ Siz tasdiqlangansiz.\nIltimos, <b>.py</b> fayl yuboring.")

    user = message.from_user
    text = (
        f"🆕 <b>Yangi foydalanuvchi:</b>\n"
        f"👤 Ism: {user.full_name}\n"
        f"🔗 Username: @{user.username if user.username else 'yo‘q'}\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"❓ Tasdiqlaysizmi yoki ban qilasizmi?"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve:{user_id}"),
                InlineKeyboardButton(text="❌ Banlash", callback_data=f"ban:{user_id}")
            ]
        ]
    )
    await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=keyboard)
    await message.answer("⏳ So‘rovingiz yuborildi. Admin tasdiqlamaguncha kuting.")

# ... (qolgan user commandlari avvalgidek) ...

from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()


async def on_startup():
    logger.info("Bot ishga tushdi")
    # Bandan chiqarilgan foydalanuvchilarga xabar yuborish
    banned_users = get_banned_users()
    for user in banned_users:
        try:
            await bot.send_message(chat_id=user['user_id'], text="🚫 Siz botdan foydalanishdan banlangansiz.")
        except:
            pass

async def on_shutdown():
    logger.info("Bot to'xtatilmoqda")
    # Barcha ishlayotgan jarayonlarni to'xtatish
    for root, dirs, files in os.walk(FILES_DIR):
        for file in files:
            if file.endswith(".pid"):
                pid_path = os.path.join(root, file)
                try:
                    with open(pid_path, "r") as f:
                        pid = f.read().strip()
                    os.kill(int(pid), signal.SIGTERM)
                    user_id = int(os.path.basename(root))
                    filename = file[:-4]  # .pid ni olib tashlaymiz
                    update_bot_status(user_id, filename, "stopped")
                except:
                    pass

if __name__ == "__main__":
    keep_alive()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    asyncio.run(dp.start_polling(bot))
