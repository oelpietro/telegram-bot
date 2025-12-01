import telebot
import os
import time
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    logging.error("TOKEN não encontrado nas variáveis de ambiente. Pare o bot e defina TOKEN.")
    raise SystemExit("TOKEN não definido")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=["start"])
def start(msg):
    try:
        bot.reply_to(msg, "🚀 Bot ativo! Envie /help para ver comandos.")
        logging.info(f"/start recebido de {msg.from_user.username or msg.from_user.id}")
    except Exception as e:
        logging.exception("Erro ao responder /start")

@bot.message_handler(commands=["help"])
def help_cmd(msg):
    txt = "Comandos:\n/start - iniciar\n/help - ajuda"
    bot.reply_to(msg, txt)

def run():
    while True:
        try:
            logging.info("Iniciando polling do bot...")
            bot.polling(none_stop=True)
        except Exception as e:
            logging.exception("Polling caiu — reiniciando em 5s")
            time.sleep(5)

if __name__ == "__main__":
    run()
