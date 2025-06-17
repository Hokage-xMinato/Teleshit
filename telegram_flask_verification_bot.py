import os
import logging
import json
import asyncio
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# --- Core Telegram Imports ---
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, ChatJoinRequestHandler, CommandHandler, MessageHandler, ContextTypes, filters
# --- Constants Imports ---
from telegram.constants import ParseMode, ChatType
# --- Error Handling Import ---
from telegram.error import TelegramError

# --- Load Environment Variables ---
load_dotenv()

# --- Configure Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING) # Suppress httpx library warnings
logger = logging.getLogger(__name__)

# --- Configuration Variables (Global for easy access by Flask routes) ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
# The WEBHOOK_URL is primarily for the set_webhook.py script now
WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook" if RENDER_EXTERNAL_HOSTNAME else os.getenv("WEBHOOK_URL", "http://127.0.0.1:5000/webhook")
if not RENDER_EXTERNAL_HOSTNAME:
    logger.warning("RENDER_EXTERNAL_HOSTNAME not found, falling back to WEBHOOK_URL from .env or default for local testing. Make sure it's correct for Render.")

PORT = int(os.getenv("PORT", 5000))

# --- Initialize Flask Application ---
app = Flask(__name__)

# --- Global Application Instance (will be initialized by create_application) ---
application = None

# Dictionary to store pending join requests awaiting verification.
pending_join_requests = {}

# --- Helper Function for MarkdownV2 Escaping (NEW) ---
def escape_markdown_v2_text(text: str) -> str:
    """Escapes characters in a string for MarkdownV2 text (non-URL/non-code) contexts.
    Reference: https://core.telegram.org/bots/api#markdownv2-style
    """
    chars_to_escape = r'_*[]()~>#+-=|{}.!'
    
    # Escape backslash first to prevent issues with subsequent escapes
    text = text.replace('\\', '\\\\')
    
    for char in chars_to_escape:
        text = text.replace(char, f'\\{char}')
    return text

# --- Telegram Bot Handlers (Logic) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command in private chats."""
    user = update.effective_user
    if user:
        await update.message.reply_html(
            rf"Hi {user.mention_html()}! I manage group join requests. "
            "If you're trying to join a group, I'll send you a verification message here first."
        )
        logger.info(f"User {user.id} started the bot in DM.")
    else:
        logger.warning("Received start command without effective user.")

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles new chat join requests."""
    chat_join_request = update.chat_join_request
    user = chat_join_request.from_user
    chat = chat_join_request.chat

    logger.info(
        f"Received join request for chat '{chat.title}' (ID: {chat.id}) "
        f"from user '{user.full_name}' (ID: {user.id}). Storing for verification."
    )

    pending_join_requests[user.id] = chat_join_request

    keyboard = [
        [KeyboardButton("I am not a bot", request_contact=True)]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        one_time_keyboard=True,
        resize_keyboard=True
    )

    verification_message_text = (
        f"Welcome! To complete your request to join '{chat.title}' and verify you are not a bot, "
        "please tap the button below to share your phone number.\n\n"
        "This helps us ensure a real person is joining. Your phone number "
        "will only be used for verification purposes. Telegram will ask for your confirmation."
    )

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=verification_message_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        logger.info(f"Sent verification prompt to user {user.id} in DM for chat '{chat.title}'.")
    except Exception as e:
        logger.error(
            f"Failed to send verification prompt to user {user.id} for chat '{chat.title}'. "
            f"Error: {e}. Removing from pending requests.",
            exc_info=True
        )
        if user.id in pending_join_requests:
            del pending_join_requests[user.id]


async def handle_contact_shared(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the contact shared by the user for verification."""
    message = update.message
    user = message.from_user
    contact = message.contact

    if contact and contact.user_id == user.id:
        phone_number = contact.phone_number
        logger.info(
            f"User {user.full_name} (ID: {user.id}) successfully shared phone number: {phone_number}. "
            f"User details: First Name: {user.first_name}, Last Name: {user.last_name}, "
            f"Username: @{user.username if user.username else 'N/A'}"
        )

        if user.id in pending_join_requests:
            original_join_request = pending_join_requests.pop(user.id)
            group_name = original_join_request.chat.title

            try:
                await original_join_request.approve()
                logger.info(
                    f"Approved join request for user '{user.full_name}' (ID: {user.id}) "
                    f"to group '{group_name}' after successful phone verification."
                )

                await message.reply_text(
                    f"Thank you for verifying! Your request to join '{group_name}' has been approved. "
                    "You are all set! You can now access the group.",
                    reply_markup=ReplyKeyboardRemove()
                )

                if ADMIN_CHAT_ID:
                    try:
                        # Apply comprehensive MarkdownV2 escaping to dynamic text
                        # IMPORTANT: Ensure escape_markdown_v2_text is defined above this.
                        escaped_group_name = escape_markdown_v2_text(group_name)
                        escaped_user_full_name = escape_markdown_v2_text(user.full_name)

                        admin_notification_text = (
                            f"✅ \\*\\*New User Verified and Joined\\!\\*\\*\n"
                            f"\\*\\*Group:\\*\\* {escaped_group_name}\n"
                            f"\\*\\*User ID:\\*\\* `{user.id}`\n"
                            f"\\*\\*Name:\\*\\* {escaped_user_full_name}\n"
                            f"\\*\\*Username:\\*\\* @{escape_markdown_v2_text(user.username) if user.username else 'N/A'}\n"
                            f"\\*\\*Phone:\\*\\* `{phone_number}`\n"
                            f"[View User Profile](tg://user?id={user.id})"
                        )
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=admin_notification_text,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        logger.info(f"Sent verification notification to admin chat {ADMIN_CHAT_ID} for user {user.id}.")
                    except Exception as admin_notify_error:
                        logger.error(f"Failed to send admin notification for user {user.id}: {admin_notify_error}", exc_info=True)
                else:
                    logger.warning("ADMIN_CHAT_ID not set, skipping admin notification.")

            except Exception as e:
                logger.error(
                    f"Failed to approve join request for user {user.id} to group '{group_name}' "
                    f"after verification. Error: {e}", exc_info=True
                )
                await message.reply_text(
                    f"Verification successful, but I encountered an issue approving your request to join '{group_name}'. "
                    "Please contact a group administrator. Apologies for the inconvenience.",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            logger.warning(f"User {user.id} shared contact, but no pending join request found for them.")
            await message.reply_text(
                "Thanks for sharing your contact! It seems you're not currently awaiting verification "
                "for a group join request through this bot. If you were trying to join a group, "
                "please try sending the join request again to the group.",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("I am not a bot", request_contact=True)]],
                    one_time_keyboard=True, resize_keyboard=True
                )
            )
    else:
        logger.warning(f"User {user.id} sent invalid contact data or user_id mismatch.")
        await message.reply_text(
            "It seems like the contact shared was not valid or not your own. "
            "Please tap the 'I am not a bot' button again if it's still there.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("I am not a bot", request_contact=True)]],
                one_time_keyboard=True, resize_keyboard=True
            )
        )

async def fallback_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles any other text messages in private chat."""
    user = update.effective_user
    if user and update.message and update.message.text:
        if user.id in pending_join_requests:
            await update.message.reply_text(
                "Please complete the verification by tapping the 'I am not a bot' button. "
                "If you don't see it, it might have disappeared; you can type /start or "
                "re-send your group join request to receive the button again.",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("I am not a bot", request_contact=True)]],
                    one_time_keyboard=True, resize_keyboard=True
                )
            )
        else:
            await update.message.reply_text(
                "I'm designed to manage group join requests. Please send a join request to a group I manage, or type /start."
            )
    else:
        logger.warning(f"Received non-text message or message without text from user {user.id}")


# --- Flask Routes ---

@app.route('/')
async def root_route():
    """Simple root route for health checks or basic info."""
    status_message = "Telegram Bot Webhook Listener is Live and Operational!"
    logger.info(f"Root route accessed. Status: {status_message}")
    return status_message, 200

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handles incoming Telegram webhook updates."""
    if not application:
        logger.error("Application instance not initialized when webhook received!")
        return jsonify({"status": "error", "message": "Bot application not ready"}), 503

    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)

        async with application:
            await application.process_update(update)

        return jsonify({"status": "ok"})

    except TelegramError as e:
        logger.error(f"Telegram API or Update processing error in webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Telegram processing error: {e}"}), 500
    except Exception as e:
        logger.error(f"Unhandled exception in webhook route: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal Server Error"}), 500


# --- Application Initialization ---

def create_application():
    """Creates and configures the PTB Application instance."""
    logger.info("Initializing PTB Application...")
    if not BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN environment variable is not set!")
        raise ValueError("TELEGRAM_BOT_TOKEN is required.")

    ptb_application = Application.builder().token(BOT_TOKEN).build()

    ptb_application.loop = asyncio.get_event_loop()
    logger.info("DEBUG: Explicitly set ptb_application.loop to: %s", ptb_application.loop)

    # --- Register Handlers ---
    ptb_application.add_handler(CommandHandler("start", start))
    ptb_application.add_handler(ChatJoinRequestHandler(handle_join_request))
    ptb_application.add_handler(MessageHandler(filters.CONTACT & filters.ChatType.PRIVATE, handle_contact_shared))
    ptb_application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, fallback_message_handler))

    logger.info("PTB Application initialized and handlers added.")
    return ptb_application

# --- Main Execution Block ---
if __name__ == "__main__":
    logger.info("Running in __main__ block (local development mode likely).")
    application = create_application()
    logger.info(f"Starting Flask app locally on port {PORT}...")
    app.run(host="0.0.0.0", port=PORT, debug=True)
else:
    logger.info("Running as a Gunicorn worker (production mode likely).")
    application = create_application()
    logger.info("Gunicorn worker loaded PTB Application.")
