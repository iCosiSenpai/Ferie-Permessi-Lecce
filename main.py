import logging
import os
import uuid
import json
from datetime import datetime

from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
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

# Carica le variabili d'ambiente (TOKEN e MANAGER_ID)
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
try:
    MANAGER_CHAT_ID = int(os.environ.get('MANAGER_CHAT_ID'))
except (TypeError, ValueError):
    logger.error("MANAGER_CHAT_ID non trovato o non è un numero valido nelle variabili d'ambiente!")
    MANAGER_CHAT_ID = None # Gestisci questo caso, magari uscendo o avvisando

# Stati per la ConversationHandler (gestione delle conversazioni a più passaggi)
(ASK_START_DATE_FERIE, ASK_END_DATE_FERIE, ASK_REASON_FERIE, CONFIRM_FERIE,
 ASK_DATE_PERMESSO, ASK_HOURS_PERMESSO, ASK_REASON_PERMESSO, CONFIRM_PERMESSO) = range(8)

# Nome del file per salvare i dati delle richieste
DB_FILE = "requests_data.json"

# --- Gestione Dati Richieste (con file JSON per persistenza) ---
def load_requests():
    """Carica le richieste da un file JSON."""
    try:
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            # Converti le chiavi stringa (ID richiesta) in ID effettivi se necessario
            # Per ora, assumiamo che le chiavi siano già stringhe come generate da uuid.uuid4().hex
            return data
    except FileNotFoundError:
        logger.info(f"{DB_FILE} non trovato, ne verrà creato uno nuovo.")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Errore nel decodificare {DB_FILE}. Verrà restituito un dizionario vuoto.")
        return {}

def save_requests(requests_data):
    """Salva le richieste in un file JSON."""
    try:
        with open(DB_FILE, "w") as f:
            json.dump(requests_data, f, indent=4)
    except IOError as e:
        logger.error(f"Errore durante il salvataggio dei dati su {DB_FILE}: {e}")

# Dizionario per memorizzare le richieste attive (caricate all'avvio)
active_requests = load_requests()

# --- Funzioni Helper ---
def generate_request_id():
    """Genera un ID univoco per la richiesta."""
    return uuid.uuid4().hex[:8] # ID più corto per leggibilità

async def send_to_manager(context: ContextTypes.DEFAULT_TYPE, user_name: str, user_id: int, request_type: str, details: str, request_id: str):
    """Invia la notifica della richiesta allo store manager."""
    if not MANAGER_CHAT_ID:
        logger.error("MANAGER_CHAT_ID non configurato. Impossibile inviare notifica.")
        # Potresti voler notificare anche l'utente che c'è un problema
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
        logger.info(f"Notifica inviata al manager per la richiesta {request_id}")
    except Exception as e:
        logger.error(f"Errore nell'invio della notifica al manager: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Si è verificato un errore tecnico nell'invio della notifica al manager. Riprova più tardi o contatta l'amministrazione."
        )

# --- Comandi Principali ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Saluta l'utente e mostra i pulsanti per le richieste."""
    user = update.effective_user
    welcome_message = (
        f"Ciao {user.first_name}! 👋 Sono il tuo assistente per le richieste di ferie e permessi.\n\n"
        "Cosa vorresti fare?"
    )
    keyboard = [
        [KeyboardButton("🏖️ Chiedi Ferie"), KeyboardButton("📝 Chiedi Permesso")],
        [KeyboardButton("ℹ️ Aiuto")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

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
        f" Riepilogo richiesta FERIE:\n"
        f"Dal: {context.user_data['start_date_ferie']}\n"
        f"Al: {context.user_data['end_date_ferie']}\n"
        f"Motivazione: {context.user_data['reason_ferie'] or 'Nessuna'}\n\n"
        "Confermi l'invio? (Sì/No)"
    )
    keyboard = [[KeyboardButton("Sì 👍"), KeyboardButton("No 👎")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(summary, reply_markup=reply_markup)
    return CONFIRM_FERIE

async def confirm_ferie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Conferma e invia la richiesta di ferie."""
    user_response = update.message.text.lower()
    if user_response == 'sì 👍' or user_response == 'sì' or user_response == 'si':
        user = update.effective_user
        request_id = generate_request_id()
        start_date = context.user_data['start_date_ferie']
        end_date = context.user_data['end_date_ferie']
        reason = context.user_data.get('reason_ferie')

        active_requests[request_id] = {
            'user_id': user.id,
            'user_name': user.full_name or user.first_name, # user.full_name se disponibile
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
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🏖️ Chiedi Ferie"), KeyboardButton("📝 Chiedi Permesso")],[KeyboardButton("ℹ️ Aiuto")]], resize_keyboard=True) # Torna ai comandi principali
        )
    else:
        await update.message.reply_text(
            "❌ Richiesta annullata. Cosa vuoi fare ora?",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🏖️ Chiedi Ferie"), KeyboardButton("📝 Chiedi Permesso")],[KeyboardButton("ℹ️ Aiuto")]], resize_keyboard=True)
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
        f" Riepilogo richiesta PERMESSO:\n"
        f"Giorno: {context.user_data['date_permesso']}\n"
        f"Orario/Descrizione: {context.user_data['hours_permesso']}\n"
        f"Motivazione: {context.user_data['reason_permesso'] or 'Nessuna'}\n\n"
        "Confermi l'invio? (Sì/No)"
    )
    keyboard = [[KeyboardButton("Sì 👍"), KeyboardButton("No 👎")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(summary, reply_markup=reply_markup)
    return CONFIRM_PERMESSO

async def confirm_permesso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Conferma e invia la richiesta di permesso."""
    user_response = update.message.text.lower()
    if user_response == 'sì 👍' or user_response == 'sì' or user_response == 'si':
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
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🏖️ Chiedi Ferie"), KeyboardButton("📝 Chiedi Permesso")],[KeyboardButton("ℹ️ Aiuto")]], resize_keyboard=True)
        )
    else:
        await update.message.reply_text(
            "❌ Richiesta annullata. Cosa vuoi fare ora?",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🏖️ Chiedi Ferie"), KeyboardButton("📝 Chiedi Permesso")],[KeyboardButton("ℹ️ Aiuto")]], resize_keyboard=True)
        )
    context.user_data.clear()
    return ConversationHandler.END

# --- Gestione Azioni Manager (Approvazione/Rifiuto) ---
async def manager_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce l'approvazione o il rifiuto da parte del manager."""
    query = update.callback_query
    await query.answer() # Risponde al callback per rimuovere il "loading" dal pulsante

    action, request_id = query.data.split("_")
    
    # Verifica che sia il manager a premere il pulsante (opzionale ma consigliato se il bot è in gruppi)
    # if query.from_user.id != MANAGER_CHAT_ID:
    #     await query.edit_message_text(text=f"{query.message.text}\n\n⚠️ Azione non permessa.")
    #     return

    if request_id in active_requests:
        request_details = active_requests[request_id]
        original_user_id = request_details['user_id']
        original_user_name = request_details['user_name']
        request_type = request_details['request_type']

        if action == "approve":
            active_requests[request_id]['status'] = 'approvata'
            new_text = f"✅ Richiesta ({request_id}) di {request_type} da {original_user_name} APPROVATA."
            await context.bot.send_message(
                chat_id=original_user_id,
                text=f"🎉 Buone notizie! La tua richiesta di {request_type.lower()} (ID: {request_id}) è stata APPROVATA!"
            )
        elif action == "deny":
            active_requests[request_id]['status'] = 'rifiutata'
            new_text = f"❌ Richiesta ({request_id}) di {request_type} da {original_user_name} RIFIUTATA."
            await context.bot.send_message(
                chat_id=original_user_id,
                text=f"😔 La tua richiesta di {request_type.lower()} (ID: {request_id}) è stata RIFIUTATA."
            )
        else:
            new_text = query.message.text + "\n\n⚠️ Azione sconosciuta."
            logger.warning(f"Azione sconosciuta '{action}' per request_id '{request_id}'")
            return # Non salvare se l'azione non è riconosciuta

        save_requests(active_requests) # Salva lo stato aggiornato
        await query.edit_message_text(text=f"{query.message.text}\n\n--- ESITO: {new_text.splitlines()[-1]} ---") # Modifica il messaggio originale del manager
        logger.info(f"Azione '{action}' eseguita per la richiesta {request_id} da parte del manager.")
    else:
        await query.edit_message_text(text=f"{query.message.text}\n\n⚠️ Errore: Richiesta ID ({request_id}) non trovata o già processata.")
        logger.warning(f"Richiesta ID {request_id} non trovata durante l'azione del manager.")


# --- Annullamento Conversazione ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Annulla la conversazione corrente."""
    user = update.message.from_user
    logger.info("L'utente %s ha annullato la conversazione.", user.first_name)
    await update.message.reply_text(
        "Operazione annullata. Dimmi pure se hai bisogno di altro!",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🏖️ Chiedi Ferie"), KeyboardButton("📝 Chiedi Permesso")],[KeyboardButton("ℹ️ Aiuto")]], resize_keyboard=True)
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- Keep Alive per Replit (con Flask) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot richieste ferie/permessi è attivo! 👍"

def run_flask():
  app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Avvia un server web Flask in un thread separato per mantenere attivo il Repl."""
    t = Thread(target=run_flask)
    t.start()

# --- Main ---
def main() -> None:
    """Avvia il bot."""
    if not BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN non trovato nelle variabili d'ambiente! Il bot non può partire.")
        return
    if not MANAGER_CHAT_ID:
        logger.critical("MANAGER_CHAT_ID non trovato o non valido! Le notifiche al manager non funzioneranno correttamente.")
        # Potresti decidere di non avviare il bot o di farlo funzionare con funzionalità limitate.

    # Crea l'applicazione del bot
    application = Application.builder().token(BOT_TOKEN).build()

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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex("^ℹ️ Aiuto$"), help_command))
    application.add_handler(CommandHandler("help", help_command)) # Alias per /help
    application.add_handler(conv_handler_ferie)
    application.add_handler(conv_handler_permesso)
    application.add_handler(CallbackQueryHandler(manager_action, pattern="^(approve_|deny_)")) # Gestisce i pulsanti del manager

    # Avvia il server Flask per Replit
    keep_alive()
    logger.info("Servizio keep_alive avviato.")

    # Avvia il bot
    logger.info("Avvio del bot...")
    application.run_polling()


if __name__ == "__main__":
    main()