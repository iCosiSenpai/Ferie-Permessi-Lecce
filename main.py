import logging
import os
import uuid
import json
from datetime import datetime
from pathlib import Path

from flask import Flask
from threading import Thread

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

# Configurazione del logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Caricamento variabili d'ambiente
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN non trovato! Impostare questa variabile è obbligatorio.")
    exit(1)

try:
    MANAGER_CHAT_ID = int(os.environ.get('MANAGER_CHAT_ID', '0'))
    if MANAGER_CHAT_ID == 0:
        raise ValueError()
except Exception:
    logger.warning("⚠️ MANAGER_CHAT_ID non configurato o non valido. Le notifiche al manager non funzioneranno.")
    MANAGER_CHAT_ID = None

DATA_DIR = os.environ.get('DATA_DIR', './data')
WEB_PORT = int(os.environ.get('WEB_PORT', '8080'))
ENABLE_WEB_SERVER = os.environ.get('ENABLE_WEB_SERVER', 'true').lower() == 'true'

# Crea directory dati
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "requests_data.json")

# Stati conversazioni
ASK_START_DATE_FERIE, ASK_END_DATE_FERIE, ASK_REASON_FERIE, CONFIRM_FERIE = range(4)
ASK_DATE_PERMESSO, ASK_HOURS_PERMESSO, ASK_REASON_PERMESSO, CONFIRM_PERMESSO = range(4, 8)

# --- Helper per gestione richieste ---
def load_requests():
    if not os.path.isfile(DB_FILE):
        return []
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_requests(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def generate_request_id():
    return uuid.uuid4().hex[:8]

def send_to_manager(text, context: ContextTypes.DEFAULT_TYPE):
    if MANAGER_CHAT_ID:
        context.bot.send_message(chat_id=MANAGER_CHAT_ID, text=text)

def get_main_keyboard():
    buttons = [[KeyboardButton("Richiesta ferie")], [KeyboardButton("Richiesta permesso")]]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# --- Handlers principali ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Ferie", callback_data='ferie')],
        [InlineKeyboardButton("Permesso", callback_data='permesso')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Benvenuto! Seleziona un'opzione:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'ferie':
        await query.edit_message_text("Inserisci data inizio ferie (YYYY-MM-DD):")
        return ASK_START_DATE_FERIE
    elif query.data == 'permesso':
        await query.edit_message_text("Inserisci data permesso (YYYY-MM-DD):")
        return ASK_DATE_PERMESSO

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Usa i comandi o i bottoni per inviare una richiesta ferie o permesso. /start per tornare al menu.")

# --- Workflow richiesta ferie ---
async def ferie_ask_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['start_date'] = update.message.text
    await update.message.reply_text("Inserisci data fine ferie (YYYY-MM-DD):")
    return ASK_END_DATE_FERIE

async def ferie_ask_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['end_date'] = update.message.text
    await update.message.reply_text("Motivo delle ferie:")
    return ASK_REASON_FERIE

async def ferie_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text
    data = load_requests()
    req = {
        'id': generate_request_id(),
        'type': 'ferie',
        'user': update.effective_user.username or update.effective_user.id,
        'start': context.user_data['start_date'],
        'end': context.user_data['end_date'],
        'reason': reason,
        'timestamp': datetime.utcnow().isoformat(),
    }
    data.append(req)
    save_requests(data)
    notification = f"[FERIE] ID:{req['id']} {req['start']}-> {req['end']} Motivo:{req['reason']}"
    await update.message.reply_text("Richiesta ferie inviata! Grazie.")
    send_to_manager(notification, context)
    return ConversationHandler.END

# --- Workflow richiesta permesso ---
async def permesso_ask_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['date'] = update.message.text
    await update.message.reply_text("Numero di ore di permesso:")
    return ASK_HOURS_PERMESSO

async def permesso_ask_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['hours'] = update.message.text
    await update.message.reply_text("Motivo del permesso:")
    return ASK_REASON_PERMESSO

async def permesso_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text
    data = load_requests()
    req = {
        'id': generate_request_id(),
        'type': 'permesso',
        'user': update.effective_user.username or update.effective_user.id,
        'date': context.user_data['date'],
        'hours': context.user_data['hours'],
        'reason': reason,
        'timestamp': datetime.utcnow().isoformat(),
    }
    data.append(req)
    save_requests(data)
    notification = f"[PERMESSO] ID:{req['id']} Data:{req['date']} Ore:{req['hours']} Motivo:{req['reason']}"
    await update.message.reply_text("Richiesta permesso inviata! Grazie.")
    send_to_manager(notification, context)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operazione annullata.")
    return ConversationHandler.END

# --- Setup e avvio bot ---
def main() -> None:
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handler conversazioni
    ferie_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='ferie')],
        states={
            ASK_START_DATE_FERIE: [MessageHandler(filters.Regex(r'^\d{4}-\d{2}-\d{2}$'), ferie_ask_end)],
            ASK_END_DATE_FERIE: [MessageHandler(filters.Regex(r'^\d{4}-\d{2}-\d{2}$'), ferie_ask_reason)],
            ASK_REASON_FERIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ferie_confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    permesso_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='permesso')],
        states={
            ASK_DATE_PERMESSO: [MessageHandler(filters.Regex(r'^\d{4}-\d{2}-\d{2}$'), permesso_ask_hours)],
            ASK_HOURS_PERMESSO: [MessageHandler(filters.Regex(r'^\d+$'), permesso_ask_reason)],
            ASK_REASON_PERMESSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, permesso_confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    # Comandi base
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(ferie_conv)
    application.add_handler(permesso_conv)
    application.add_handler(MessageHandler(filters.ALL & ~filters.CallbackQueryHandler, lambda u, c: u.message.reply_text("Comando non riconosciuto. Usa /help.")))

    # Server Flask per healthcheck
    if ENABLE_WEB_SERVER:
        web_app = Flask(__name__)
        @web_app.route('/')
        def home(): return f"Bot attivo con {len(load_requests())} richieste"
        @web_app.route('/health')
        def health(): return {"status": "healthy"}
        Thread(target=lambda: web_app.run(host='0.0.0.0', port=WEB_PORT, debug=False)).start()

    logger.info("✅ Bot avviato con successo!")
    application.run_polling()

if __name__ == "__main__":
    main()
# Fine script
