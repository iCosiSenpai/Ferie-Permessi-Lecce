import logging
import os
import uuid
import json
from datetime import datetime
from pathlib import Path

from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

# Configurazione del logging (facilita il debug)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Caricamento variabili d'ambiente con supporto per .env ---
def load_env_file():
    """Carica variabili d'ambiente da file .env se presente"""
    env_file = Path('.env')
    if env_file.exists():
        logger.info("Caricamento file .env trovato")
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key not in os.environ:  # Non sovrascrivere variabili già impostate
                            os.environ[key] = value
            logger.info("File .env caricato con successo")
        except Exception as e:
            logger.error(f"Errore nel caricamento del file .env: {e}")
    else:
        logger.info("File .env non trovato, uso solo variabili d'ambiente di sistema")

# Carica il file .env se presente
load_env_file()

# Carica le variabili d'ambiente con valori di default e validazione
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN non trovato! Impostare questa variabile è obbligatorio.")
    exit(1)

try:
    MANAGER_CHAT_ID = int(os.environ.get('MANAGER_CHAT_ID', '0'))
    if MANAGER_CHAT_ID == 0:
        raise ValueError("MANAGER_CHAT_ID non impostato")
except (TypeError, ValueError) as e:
    logger.error(f"❌ MANAGER_CHAT_ID non trovato o non è un numero valido: {e}")
    logger.error("Il bot continuerà ma le notifiche al manager non funzioneranno!")
    MANAGER_CHAT_ID = None

# Configurazione percorsi e porte
DATA_DIR = os.environ.get('DATA_DIR', '/share/Container/telegram-bot-data')
WEB_PORT = int(os.environ.get('WEB_PORT', '5000'))
ENABLE_WEB_SERVER = os.environ.get('ENABLE_WEB_SERVER', 'true').lower() == 'true'

# Crea la directory dei dati se non esiste
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "requests_data.json")

logger.info(f"🚀 Avvio del bot Telegram per richieste ferie/permessi...{BOT_TOKEN}")
logger.info(f"📁 Directory dati: {DATA_DIR}")
logger.info(f"💽 File database: {DB_FILE}")
logger.info(f"🌐 Server web: {'Abilitato' if ENABLE_WEB_SERVER else 'Disabilitato'}")
logger.info(f"🔌 Porta web: {WEB_PORT}")

# Stati per i ConversationHandler
ASK_START_DATE_FERIE, ASK_END_DATE_FERIE, ASK_REASON_FERIE, CONFIRM_FERIE = range(4)
ASK_DATE_PERMESSO, ASK_HOURS_PERMESSO, ASK_REASON_PERMESSO, CONFIRM_PERMESSO = range(4, 8)

# --- Gestione Dati Richieste (con file JSON per persistenza) ---
def load_requests():
    """Carica le richieste da un file JSON."""
    try:
        with open(DB_FILE, "r", encoding='utf-8') as f:
            data = json.load(f)
            logger.info(f"📊 Caricate {len(data)} richieste dal database")
            return data
    except FileNotFoundError:
        logger.info(f"📄 {DB_FILE} non trovato, ne verrà creato uno nuovo.")
        return {}
    except json.JSONDecodeError:
        logger.error(f"❌ Errore nel decodificare {DB_FILE}. Verrà restituito un dizionario vuoto.")
        return {}
    except Exception as e:
        logger.error(f"❌ Errore imprevisto nel caricamento dei dati: {e}")
        return {}

def save_requests(requests_data):
    """Salva le richieste in un file JSON."""
    try:
        # Backup del file esistente
        if os.path.exists(DB_FILE):
            backup_file = f"{DB_FILE}.backup"
            os.rename(DB_FILE, backup_file)
            
        with open(DB_FILE, "w", encoding='utf-8') as f:
            json.dump(requests_data, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 Dati salvati correttamente ({len(requests_data)} richieste)")
        
        # Rimuovi backup se tutto è andato bene
        backup_file = f"{DB_FILE}.backup"
        if os.path.exists(backup_file):
            os.remove(backup_file)
            
    except IOError as e:
        logger.error(f"❌ Errore durante il salvataggio dei dati su {DB_FILE}: {e}")
        # Ripristina backup se esiste
        backup_file = f"{DB_FILE}.backup"
        if os.path.exists(backup_file):
            os.rename(backup_file, DB_FILE)
            logger.info("🔄 Ripristinato backup del database")

# Dizionario per memorizzare le richieste attive (caricate all'avvio)
active_requests = load_requests()

# --- Funzioni Helper ---
def generate_request_id():
    """Genera un ID univoco per la richiesta."""
    return uuid.uuid4().hex[:8]

async def send_to_manager(context: ContextTypes.DEFAULT_TYPE, user_name: str, user_id: int, request_type: str, details: str, request_id: str):
    """Invia la notifica della richiesta allo store manager."""
    if not MANAGER_CHAT_ID:
        logger.error("❌ MANAGER_CHAT_ID non configurato. Impossibile inviare notifica.")
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ C'è stato un problema nell'inoltrare la tua richiesta al manager. Per favore, contatta l'amministratore del bot."
        )
        return

    message_text = f"🔔 Nuova richiesta di {request_type} da {user_name} (ID utente: {user_id}):\n\n"
    message_text += details
    message_text += f"\n\n🆔 ID Richiesta: {request_id}"

    keyboard = [
        [
            InlineKeyboardButton("✅ Approva", callback_data=f"approve_{request_id}"),
            InlineKeyboardButton("❌ Rifiuta", callback_data=f"deny_{request_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.send_message(
            chat_id=MANAGER_CHAT_ID, text=message_text, reply_markup=reply_markup
        )
        logger.info(f"📨 Notifica inviata al manager per la richiesta {request_id}")
    except Exception as e:
        logger.error(f"❌ Errore nell'invio della notifica al manager: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Si è verificato un errore tecnico nell'invio della notifica al manager. Riprova più tardi o contatta l'amministrazione."
        )

def get_main_keyboard():
    """Restituisce la tastiera principale del bot."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("🏖️ Chiedi Ferie"), KeyboardButton("📝 Chiedi Permesso")],
        [KeyboardButton("ℹ️ Aiuto")]
    ], resize_keyboard=True)

# --- Comandi Principali ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Saluta l'utente e mostra i pulsanti per le richieste."""
    user = update.effective_user
    welcome_message = (
        f"Ciao {user.first_name}! 👋 Sono il tuo assistente per le richieste di ferie e permessi.\n\n"
        "Cosa vorresti fare?"
    )
    await update.message.reply_text(welcome_message, reply_markup=get_main_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra un messaggio di aiuto."""
    help_text = (
        "🤖 **Come usare il bot:**\n"
        "Premi '🏖️ Chiedi Ferie' per avviare una richiesta di ferie.\n"
        "Premi '📝 Chiedi Permesso' per avviare una richiesta di permesso.\n\n"
        "Segui le istruzioni e rispondi alle domande del bot.\n"
        "Lo store manager riceverà una notifica e potrà approvare o rifiutare la tua richiesta.\n"
        "Sarai avvisato dell'esito.\n\n"
        "Puoi annullare una richiesta in qualsiasi momento digitando /annulla."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

# --- Flusso Richiesta Ferie ---
async def start_ferie_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inizia il processo di richiesta ferie."""
    await update.message.reply_text("🏖️ Ottimo! Iniziamo con la richiesta di ferie.")
    await update.message.reply_text("🗓️ Quando vorresti iniziare le ferie? (formato GG/MM/AAAA)")
    context.user_data['request_type'] = 'ferie'
    return ASK_START_DATE_FERIE

async def ask_start_date_ferie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Chiede la data di inizio delle ferie."""
    context.user_data['start_date_ferie'] = update.message.text
    await update.message.reply_text("🗓️ Quando vorresti terminare le ferie? (formato GG/MM/AAAA)")
    return ASK_END_DATE_FERIE

async def ask_end_date_ferie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Chiede la data di fine delle ferie e poi la motivazione."""
    context.user_data['end_date_ferie'] = update.message.text
    await update.message.reply_text("📝 Vuoi aggiungere una motivazione? (opzionale, scrivi 'no' se non serve)")
    return ASK_REASON_FERIE

async def ask_reason_ferie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Chiede la motivazione e mostra il riepilogo per conferma."""
    reason = update.message.text
    context.user_data['reason_ferie'] = None if reason.lower() == 'no' else reason

    summary = (
        f"📋 Riepilogo richiesta FERIE:\n"
        f"📅 Dal: {context.user_data['start_date_ferie']}\n"
        f"📅 Al: {context.user_data['end_date_ferie']}\n"
        f"💬 Motivazione: {context.user_data['reason_ferie'] or 'Nessuna'}\n\n"
        "Confermi l'invio? (Sì/No)"
    )
    keyboard = [[KeyboardButton("Sì 👍"), KeyboardButton("No 👎")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(summary, reply_markup=reply_markup)
    return CONFIRM_FERIE

async def confirm_ferie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Conferma e invia la richiesta di ferie."""
    user_response = update.message.text.lower()
    if user_response in ['sì 👍', 'sì', 'si']:
        user = update.effective_user
        request_id = generate_request_id()
        start_date = context.user_data['start_date_ferie']
        end_date = context.user_data['end_date_ferie']
        reason = context.user_data.get('reason_ferie')

        active_requests[request_id] = {
            'user_id': user.id,
            'user_name': user.full_name or user.first_name,
            'request_type': 'Ferie',
            'start_date': start_date,
            'end_date': end_date,
            'reason': reason,
            'status': 'in attesa',
            'timestamp': datetime.now().isoformat()
        }
        save_requests(active_requests)

        details = (
            f"📅 Periodo: dal {start_date} al {end_date}\n"
            f"💬 Motivazione: {reason or 'Nessuna'}"
        )
        await send_to_manager(context, user.full_name or user.first_name, user.id, "Ferie", details, request_id)
        await update.message.reply_text(
            "✅ La tua richiesta di ferie è stata inviata con successo allo store manager!",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Richiesta annullata. Cosa vuoi fare ora?",
            reply_markup=get_main_keyboard()
        )
    context.user_data.clear()
    return ConversationHandler.END

# --- Flusso Richiesta Permesso ---
async def start_permesso_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inizia il processo di richiesta permesso."""
    await update.message.reply_text("📝 Bene! Iniziamo con la richiesta di permesso.")
    await update.message.reply_text("🗓️ Per quale giorno richiedi il permesso? (formato GG/MM/AAAA)")
    context.user_data['request_type'] = 'permesso'
    return ASK_DATE_PERMESSO

async def ask_date_permesso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Chiede la data del permesso."""
    context.user_data['date_permesso'] = update.message.text
    await update.message.reply_text("⏰ Indica le ore di permesso o una breve descrizione (es. 'dalle 9 alle 11', '2 ore al mattino', 'giornata intera per visita medica').")
    return ASK_HOURS_PERMESSO

async def ask_hours_permesso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Chiede le ore/descrizione del permesso."""
    context.user_data['hours_permesso'] = update.message.text
    await update.message.reply_text("📝 Vuoi aggiungere una motivazione specifica? (opzionale, scrivi 'no' se non serve)")
    return ASK_REASON_PERMESSO

async def ask_reason_permesso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Chiede la motivazione e mostra il riepilogo per conferma."""
    reason = update.message.text
    context.user_data['reason_permesso'] = None if reason.lower() == 'no' else reason

    summary = (
        f"📋 Riepilogo richiesta PERMESSO:\n"
        f"📅 Giorno: {context.user_data['date_permesso']}\n"
        f"⏰ Orario/Descrizione: {context.user_data['hours_permesso']}\n"
        f"💬 Motivazione: {context.user_data['reason_permesso'] or 'Nessuna'}\n\n"
        "Confermi l'invio? (Sì/No)"
    )
    keyboard = [[KeyboardButton("Sì 👍"), KeyboardButton("No 👎")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(summary, reply_markup=reply_markup)
    return CONFIRM_PERMESSO

async def confirm_permesso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Conferma e invia la richiesta di permesso."""
    user_response = update.message.text.lower()
    if user_response in ['sì 👍', 'sì', 'si']:
        user = update.effective_user
        request_id = generate_request_id()
        date_permesso = context.user_data['date_permesso']
        hours_permesso = context.user_data['hours_permesso']
        reason = context.user_data.get('reason_permesso')

        active_requests[request_id] = {
            'user_id': user.id,
            'user_name': user.full_name or user.first_name,
            'request_type': 'Permesso',
            'date': date_permesso,
            'hours_description': hours_permesso,
            'reason': reason,
            'status': 'in attesa',
            'timestamp': datetime.now().isoformat()
        }
        save_requests(active_requests)

        details = (
            f"📅 Giorno: {date_permesso}\n"
            f"⏰ Orario/Descrizione: {hours_permesso}\n"
            f"💬 Motivazione: {reason or 'Nessuna'}"
        )
        await send_to_manager(context, user.full_name or user.first_name, user.id, "Permesso", details, request_id)
        await update.message.reply_text(
            "✅ La tua richiesta di permesso è stata inviata con successo allo store manager!",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Richiesta annullata. Cosa vuoi fare ora?",
            reply_markup=get_main_keyboard()
        )
    context.user_data.clear()
    return ConversationHandler.END

# --- Gestione Azioni Manager (Approvazione/Rifiuto) ---
async def manager_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce l'approvazione o il rifiuto da parte del manager."""
    query = update.callback_query
    await query.answer()

    try:
        action, request_id = query.data.split("_", 1)
    except ValueError:
        logger.error(f"❌ Formato callback_data non valido: {query.data}")
        await query.edit_message_text(text=f"{query.message.text}\n\n⚠️ Errore nel formato del comando.")
        return

    # Verifica che sia il manager a premere il pulsante
    if query.from_user.id != MANAGER_CHAT_ID:
        await query.answer("⚠️ Non sei autorizzato a eseguire questa azione.", show_alert=True)
        return

    if request_id in active_requests:
        request_details = active_requests[request_id]
        original_user_id = request_details['user_id']
        original_user_name = request_details['user_name']
        request_type = request_details['request_type']

        if action == "approve":
            active_requests[request_id]['status'] = 'approvata'
            active_requests[request_id]['approved_at'] = datetime.now().isoformat()
            new_text = f"✅ Richiesta ({request_id}) di {request_type} da {original_user_name} APPROVATA."
            try:
                await context.bot.send_message(
                    chat_id=original_user_id,
                    text=f"🎉 Buone notizie! La tua richiesta di {request_type.lower()} (ID: {request_id}) è stata APPROVATA!"
                )
            except Exception as e:
                logger.error(f"❌ Errore nell'invio del messaggio di approvazione all'utente {original_user_id}: {e}")
                
        elif action == "deny":
            active_requests[request_id]['status'] = 'rifiutata'
            active_requests[request_id]['denied_at'] = datetime.now().isoformat()
            new_text = f"❌ Richiesta ({request_id}) di {request_type} da {original_user_name} RIFIUTATA."
            try:
                await context.bot.send_message(
                    chat_id=original_user_id,
                    text=f"😔 La tua richiesta di {request_type.lower()} (ID: {request_id}) è stata RIFIUTATA."
                )
            except Exception as e:
                logger.error(f"❌ Errore nell'invio del messaggio di rifiuto all'utente {original_user_id}: {e}")
        else:
            await query.edit_message_text(text=f"{query.message.text}\n\n⚠️ Azione sconosciuta.")
            logger.warning(f"⚠️ Azione sconosciuta '{action}' per request_id '{request_id}'")
            return

        save_requests(active_requests)
        await query.edit_message_text(text=f"{query.message.text}\n\n--- ESITO: {new_text.split(' da ')[0].split(') di ')[0]}) ---")
        logger.info(f"✅ Azione '{action}' eseguita per la richiesta {request_id} da parte del manager.")
    else:
        await query.edit_message_text(text=f"{query.message.text}\n\n⚠️ Errore: Richiesta ID ({request_id}) non trovata o già processata.")
        logger.warning(f"⚠️ Richiesta ID {request_id} non trovata durante l'azione del manager.")

# --- Annullamento Conversazione ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Annulla la conversazione corrente."""
    user = update.message.from_user
    logger.info("🔄 L'utente %s ha annullato la conversazione.", user.first_name)
    await update.message.reply_text(
        "Operazione annullata. Dimmi pure se hai bisogno di altro!",
        reply_markup=get_main_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- Gestione messaggi non riconosciuti ---
async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce messaggi non riconosciuti."""
    await update.message.reply_text(
        "Non ho capito. Usa i pulsanti qui sotto per interagire con me.",
        reply_markup=get_main_keyboard()
    )

# --- Server Web per Monitoraggio ---
app = Flask(__name__)

@app.route('/')
def home():
    return f"""
    <h1>🤖 Bot Richieste Ferie/Permessi</h1>
    <p><strong>Status:</strong> 🟢 Attivo</p>
    <p><strong>Richieste totali:</strong> {len(active_requests)}</p>
    <p><strong>Directory dati:</strong> {DATA_DIR}</p>
    <p><strong>Manager configurato:</strong> {'✅ Sì' if MANAGER_CHAT_ID else '❌ No'}</p>
    <p><a href="/health">Health Check</a> | <a href="/stats">Statistiche</a></p>
    """

@app.route('/health')
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_requests": len(active_requests),
        "manager_configured": MANAGER_CHAT_ID is not None,
        "data_dir": DATA_DIR
    }

@app.route('/stats')
def stats():
    """Endpoint per statistiche del bot."""
    stats_data = {
        "total_requests": len(active_requests),
        "pending": len([r for r in active_requests.values() if r['status'] == 'in attesa']),
        "approved": len([r for r in active_requests.values() if r['status'] == 'approvata']),
        "rejected": len([r for r in active_requests.values() if r['status'] == 'rifiutata']),
        "data_directory": DATA_DIR,
        "database_file": DB_FILE,
        "manager_configured": MANAGER_CHAT_ID is not None
    }
    return stats_data

def run_flask():
    """Avvia il server Flask."""
    try:
        app.run(host='0.0.0.0', port=WEB_PORT, debug=False)
    except Exception as e:
        logger.error(f"❌ Errore nell'avvio del server web: {e}")

def start_web_server():
    """Avvia il server web opzionale per monitoraggio."""
    if ENABLE_WEB_SERVER:
        t = Thread(target=run_flask)
        t.daemon = True
        t.start()
        logger.info(f"🌐 Server web di monitoraggio avviato su porta {WEB_PORT}")
    else:
        logger.info("🌐 Server web disabilitato")

# Funzione keep_alive rimossa perché non necessaria su QNAP

# --- Main ---
def main() -> None:
    """Avvia il bot."""
    logger.info("🚀 Avvio del bot Telegram per richieste ferie/permessi...")
    
    # Verifica configurazione
    if not BOT_TOKEN:
        logger.critical("❌ TELEGRAM_BOT_TOKEN non trovato! Il bot non può partire.")
        return
    if not MANAGER_CHAT_ID:
        logger.warning("⚠️ MANAGER_CHAT_ID non configurato! Le notifiche al manager non funzioneranno.")

    # Crea l'applicazione del bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handler per le richieste di ferie
    conv_handler_ferie = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🏖️ Chiedi Ferie$"), start_ferie_request)],
        states={
            ASK_START_DATE_FERIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start_date_ferie)],
            ASK_END_DATE_FERIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_end_date_ferie)],
            ASK_REASON_FERIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_reason_ferie)],
            CONFIRM_FERIE: [MessageHandler(filters.Regex("^(Sì|Si|sì|si|Sì 👍|No 👎|no|NO)$"), confirm_ferie)],
        },
        fallbacks=[CommandHandler("annulla", cancel), MessageHandler(filters.Regex("^Annulla$"), cancel)],
    )

    # Handler per le richieste di permesso
    conv_handler_permesso = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Chiedi Permesso$"), start_permesso_request)],
        states={
            ASK_DATE_PERMESSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date_permesso)],
            ASK_HOURS_PERMESSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_hours_permesso)],
            ASK_REASON_PERMESSO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_reason_permesso)],
            CONFIRM_PERMESSO: [MessageHandler(filters.Regex("^(Sì|Si|sì|si|Sì 👍|No 👎|no|NO)$"), confirm_permesso)],
        },
        fallbacks=[CommandHandler("annulla", cancel), MessageHandler(filters.Regex("^Annulla$"), cancel)],
    )

    # Aggiungi gli handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex("^ℹ️ Aiuto$"), help_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler_ferie)
    application.add_handler(conv_handler_permesso)
    application.add_handler(CallbackQueryHandler(manager_action, pattern="^(approve_|deny_)"))
    
    # Handler per messaggi non riconosciuti (deve essere l'ultimo)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown_message))

    # Avvia il server web se abilitato
    start_web_server()

    # Avvia il bot
    logger.info("✅ Bot avviato con successo!")
    application.run_polling()

if __name__ == "__main__":
    main()
