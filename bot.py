import telebot
import os

TOKEN = os.getenv("8026509446:AAFOQTquBugbB61GvysqSM_bEmluKco6Ixk")  # vamos colocar o token no Render

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=["start"])
def start(msg):
    bot.reply_to(msg, "👋 Olá! Seu bot está funcionando no Render!")

bot.polling(none_stop=True)
