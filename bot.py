# bot.py
import telebot
import os
import time
import logging
import sqlite3
from datetime import datetime
from threading import Thread

# --- CONFIG / LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("TOKEN")          # obrigatório
POST_INTERVAL = int(os.getenv("POST_INTERVAL", "600"))  # em segundos, default 600s (10 min)
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")  # onde as divulgações serão publicadas (opcional)
DB_PATH = os.getenv("DB_PATH", "partners.db")  # arquivo sqlite (persistente no container Render)

if not TOKEN:
    logging.error("TOKEN não encontrado nas variáveis de ambiente. Defina TOKEN.")
    raise SystemExit("TOKEN não definido")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- Database helpers ---
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE,
            title TEXT,
            username TEXT,
            owner_id INTEGER,
            added_at TEXT,
            last_posted_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            user_id INTEGER PRIMARY KEY,
            action TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

# --- state helpers (pending actions) ---
def set_pending(user_id, action):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("INSERT OR REPLACE INTO pending (user_id, action, created_at) VALUES (?, ?, ?)", (user_id, action, now))
    conn.commit()
    conn.close()

def get_pending(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT action FROM pending WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["action"] if row else None

def clear_pending(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# --- Channels CRUD ---
def add_channel(chat_id, title, username, owner_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    try:
        cur.execute("INSERT OR IGNORE INTO channels (chat_id, title, username, owner_id, added_at) VALUES (?, ?, ?, ?, ?)",
                    (chat_id, title, username, owner_id, now))
        conn.commit()
        added = cur.rowcount > 0
        return added
    finally:
        conn.close()

def list_channels_by_owner(owner_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM channels WHERE owner_id = ?", (owner_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_all_channels():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM channels ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows

def update_last_posted(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("UPDATE channels SET last_posted_at = ? WHERE chat_id = ?", (now, chat_id))
    conn.commit()
    conn.close()

# --- Utility ---
def chat_link_from_row(row):
    if row["username"]:
        return f"https://t.me/{row['username'].lstrip('@')}"
    else:
        # If private and no username, we may not have a public link
        return None

def ensure_bot_is_admin(chat_id):
    try:
        me = bot.get_me()
        member = bot.get_chat_member(chat_id, me.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logging.exception("Erro ao verificar se bot é admin")
        return False

# --- Bot commands and handlers ---

# Start with menu
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("➕ Add Canal", callback_data="add_channel"))
    kb.add(InlineKeyboardButton("➕ Add Grupo", callback_data="add_group"))
    kb.add(InlineKeyboardButton("📁 Meus Canais/Grupos", callback_data="my_channels"))
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(m):
    txt = ("😃 Bem-vindo! Use nosso sistema para cadastrar seu canal ou grupo e participar das divulgações.\n\n"
           "Clique em <b>Add Canal</b> ou <b>Add Grupo</b> para começar.")
    bot.send_message(m.chat.id, txt, reply_markup=main_menu())

@bot.callback_query_handler(func=lambda cq: True)
def callback_handler(cq):
    user_id = cq.from_user.id
    data = cq.data

    if data == "add_channel":
        set_pending(user_id, "add_channel")
        bot.send_message(user_id, "➡️ Para cadastrar um CANAL:\n\n1) Adicione este bot como administrador no seu canal.\n2) Depois, encaminhe aqui uma mensagem do canal OU envie o @username do canal.\n\nEncaminhe uma mensagem do canal agora ou envie @username.")
        cq.answer()
    elif data == "add_group":
        set_pending(user_id, "add_group")
        bot.send_message(user_id, "➡️ Para cadastrar um GRUPO:\n\n1) Adicione este bot como administrador no grupo (ou adicione ao grupo).\n2) Depois, encaminhe aqui uma mensagem do grupo OU envie o @username do grupo.\n\nEncaminhe uma mensagem do grupo agora ou envie @username.")
        cq.answer()
    elif data == "my_channels":
        rows = list_channels_by_owner(user_id)
        if not rows:
            bot.send_message(user_id, "Você ainda não cadastrou nenhum canal/grupo.")
        else:
            txt = "🔎 Seus canais/grupos:\n\n"
            for r in rows:
                link = chat_link_from_row(r) or f"<code>chat_id: {r['chat_id']}</code>"
                txt += f"• {r['title']} — {link}\n"
            bot.send_message(user_id, txt, disable_web_page_preview=True)
        cq.answer()

@bot.message_handler(func=lambda m: True, content_types=["text", "photo", "video", "document", "sticker", "voice"])
def handle_message(m):
    user_id = m.from_user.id
    pending = get_pending(user_id)

    # If no pending, ignore or show menu
    if not pending:
        return

    # If user forwarded a message from a channel/group, use forward_from_chat
    target_chat = None
    if hasattr(m, "forward_from_chat") and m.forward_from_chat:
        f = m.forward_from_chat
        target_chat = f
    elif m.text and m.text.strip().startswith("@"):
        # user sent @username
        username = m.text.strip().split()[0]
        try:
            target_chat = bot.get_chat(username)
        except Exception as e:
            bot.send_message(user_id, "Não consegui obter dados desse @username. Verifique se está correto e que o bot está adicionado como admin.", reply_markup=main_menu())
            clear_pending(user_id)
            return
    else:
        bot.send_message(user_id, "Encaminhe uma mensagem do canal/grupo OU envie o @username. Tente novamente.", reply_markup=main_menu())
        clear_pending(user_id)
        return

    # we have target_chat object
    chat_id = target_chat.id
    title = getattr(target_chat, "title", str(getattr(target_chat, "username", "Canal/Grupo")))
    username = getattr(target_chat, "username", None)

    # verify bot admin
    is_admin = ensure_bot_is_admin(chat_id)
    if not is_admin:
        bot.send_message(user_id, "⚠️ O bot precisa estar como administrador no canal/grupo para cadastrá-lo. Adicione o bot e tente novamente.", reply_markup=main_menu())
        clear_pending(user_id)
        return

    # save
    added = add_channel(chat_id, title, username, user_id)
    if added:
        bot.send_message(user_id, f"✅ {title} cadastrado com sucesso!")
    else:
        bot.send_message(user_id, f"⚠️ Este canal/grupo já está cadastrado.")

    clear_pending(user_id)

# --- Posting/Rotation routine ---
def format_promo(row):
    title = row["title"]
    username = row["username"]
    chat_id = row["chat_id"]
    link = None
    if username:
        link = f"https://t.me/{username.lstrip('@')}"
    else:
        try:
            # try to export invite link (requires bot admin)
            link = bot.export_chat_invite_link(chat_id)
        except Exception:
            link = None

    txt = f"📣 <b>{title}</b>\n"
    if link:
        txt += f"🔗 {link}\n"
    else:
        txt += f"🔔 Canal/Grupo privado — sem link público\n"
    txt += f"\nAdicione seu canal: @{bot.get_me().username}\n"
    return txt

def rotation_worker():
    if not TARGET_CHAT_ID:
        logging.info("TARGET_CHAT_ID não definido — rotação automática desativada.")
        return

    logging.info("Rotation worker started. Interval: %s seconds", POST_INTERVAL)
    while True:
        try:
            rows = get_all_channels()
            if not rows:
                logging.info("Nenhum canal cadastrado — pulando ciclo.")
                time.sleep(POST_INTERVAL)
                continue

            for r in rows:
                try:
                    promo = format_promo(r)
                    bot.send_message(TARGET_CHAT_ID, promo, disable_web_page_preview=False)
                    update_last_posted(r["chat_id"])
                    logging.info("Posted promo for %s (%s)", r["title"], r["chat_id"])
                    time.sleep(5)  # breve pausa entre posts
                except Exception as e:
                    logging.exception("Erro ao postar promoção para %s", r["chat_id"])
                # sleep between items (to slow-down) and allow interruption
                time.sleep(POST_INTERVAL // max(1, len(rows)))
        except Exception as e:
            logging.exception("Erro no loop de rotação")
            time.sleep(10)

# --- Admin command to set target (only allowed to the deployer or bot owner) ---
# We'll allow anyone who is the creator of the bot token (owner) to set target.
BOT_OWNER_IDS = os.getenv("OWNER_IDS")  # optional CSV of ids allowed to configure target

def is_owner(user_id):
    if not BOT_OWNER_IDS:
        return False
    ids = [int(x.strip()) for x in BOT_OWNER_IDS.split(",") if x.strip()]
    return user_id in ids

@bot.message_handler(commands=["settarget"])
def cmd_settarget(m):
    user_id = m.from_user.id
    if not is_owner(user_id):
        bot.reply_to(m, "Você não tem permissão para usar esse comando.")
        return
    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "Uso: /settarget <chat_id ou @username>\nEx: /settarget -1001234567890 ou /settarget @meucanal")
        return
    target = args[1].strip()
    try:
        chat = bot.get_chat(target)
        # Save to environment is manual on Render; inform user:
        bot.reply_to(m, f"Target validado: {chat.title or chat.id}. Agora vá em Render → Environment e coloque TARGET_CHAT_ID={chat.id} e dê Deploy.")
    except Exception as e:
        bot.reply_to(m, "Não consegui obter o chat informado. Verifique e tente novamente.")

# --- Startup & threads ---
if __name__ == "__main__":
    init_db()
    # Start rotation worker in separate thread (only if TARGET_CHAT_ID provided)
    if TARGET_CHAT_ID:
        try:
            # ensure it's an integer
            TARGET_CHAT_ID = int(TARGET_CHAT_ID)
            t = Thread(target=rotation_worker, daemon=True)
            t.start()
            logging.info("Rotation thread started.")
        except Exception:
            logging.exception("TARGET_CHAT_ID inválido. Desativando rotação.")
    else:
        logging.info("TARGET_CHAT_ID não definido — rotação automática desligada.")

    logging.info("Bot polling starting...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception:
            logging.exception("Polling caiu — reiniciando em 5s")
            time.sleep(5)
