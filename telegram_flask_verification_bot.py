import os
import logging
import json
import asyncio
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, ChatJoinRequestHandler, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode, Update as TelegramUpdateType # Renamed Update for clarity

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
            exc_info=True # Log full traceback
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
            original_join_request = pending_join_requests.pop(user.id) # Remove from pending
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
                    reply_markup=ReplyKeyboardRemove() # Remove the keyboard
                )

                if ADMIN_CHAT_ID:
                    try:
                        # MarkdownV2 requires escaping specific characters
                        escaped_group_name = group_name.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[') \
                                                     .replace('`', '\\`').replace('.', '\\.').replace('!', '\\!') \
                                                     .replace('(', '\\(').replace(')', '\\)').replace('-', '\\-') \
                                                     .replace('~', '\\~').replace('>', '\\>').replace('#', '\\#') \
                                                     .replace('+', '\\+').replace('=', '\\=').replace('|', '\\|') \
                                                     .replace('{', '\\{').replace('}', '\\}').replace('.', '\\.')
                        escaped_user_full_name = user.full_name.replace('_', '\\_').replace('*', '\\*')

                        admin_notification_text = (
                            f"✅ \\*\\*New User Verified and Joined\\!\\*\\*\n"
                            f"\\*\\*Group:\\*\\* {escaped_group_name}\n"
                            f"\\*\\*User ID:\\*\\* `{user.id}`\n"
                            f"\\*\\*Name:\\*\\* {escaped_user_full_name}\n"
                            f"\\*\\*Username:\\*\\* @{user.username if user.username else 'N/A'}\n"
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
                reply_markup=ReplyKeyboardRemove()
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
    if user and update.message and update.message.text: # Ensure message and text exist
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
            await update.message.reply_text("Hello! I'm here to help with group join requests. How can I assist you?")
    elif user:
        logger.warning(f"Received a non-text message from user {user.id} in fallback handler.")
        await update.message.reply_text("I can only process text messages and contact shares. Please use the provided buttons.")
    else:
        logger.warning("Received a message without effective user in fallback handler.")

# --- Callbacks for Application Lifecycle (minimal/removed for webhook setup) ---
# post_init_callback and post_shutdown_callback are not used for webhook setup anymore

# --- Function to Create and Configure the PTB Application ---
def create_application() -> Application:
    """
    Creates and configures the python-telegram-bot Application instance.
    This is the "factory" function that Gunicorn will call implicitly when running.
    """
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set. Cannot create PTB Application.")
        raise ValueError("BOT_TOKEN is not set. Please configure it in environment variables.")

    logger.info(f"DEBUG: BOT_TOKEN (first 5 chars): {BOT_TOKEN[:5] if BOT_TOKEN else 'None'}")

    # Build the application instance
    ptb_application = Application.builder().token(BOT_TOKEN).arbitrary_callback_data(True).build()
    
    # --- Ensure an event loop is available for PTB's internal use ---
    # This loop is for PTB's internal asynchronous operations, NOT for Flask
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    ptb_application.loop = loop
    logger.info(f"DEBUG: Explicitly set ptb_application.loop to: {ptb_application.loop}")

    # The post_init/post_shutdown warnings are expected but harmless now, as webhook is set externally.
    # You can remove these debug prints/warnings if you find the logs too noisy.
    post_init_attr = getattr(ptb_application, 'post_init', 'ATTRIBUTE_MISSING')
    if post_init_attr == 'ATTRIBUTE_MISSING' or not callable(post_init_attr):
        logger.warning(f"WARNING: ptb_application does not have a callable 'post_init' method as expected. "
                        f"Value: {post_init_attr}. This is okay as webhook is set externally.")
    # No registration of post_init_callback here

    # No registration of post_shutdown_callback here


    # Register handlers
    ptb_application.add_handler(CommandHandler("start", start))
    ptb_application.add_handler(ChatJoinRequestHandler(handle_join_request))
    ptb_application.add_handler(MessageHandler(filters.CONTACT & ChatType.PRIVATE, handle_contact_shared))
    ptb_application.add_handler(MessageHandler(filters.TEXT & ChatType.PRIVATE, fallback_message_handler))

    return ptb_application

# Initialize the global application instance
# This is called once when the module is loaded by Gunicorn/Flask.
application = create_application()


# --- Flask Webhook Route ---
@app.route('/webhook', methods=['POST'])
async def webhook():
    """Endpoint for Telegram to send updates."""
    if request.method == "POST":
        if not application:
            logger.error("PTB Application not initialized when webhook received.")
            return jsonify({"status": "error", "message": "Bot not ready"}), 503
        try:
            update_data_json = request.get_data().decode('utf-8')
            # Use the renamed Update type from telegram.constants
            await application.update_queue.put(TelegramUpdateType.de_json(json.loads(update_data_json), application.bot))
            return jsonify({"status": "ok"}), 200
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in webhook request: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400
        except Exception as e:
            logger.error(f"Error processing webhook update: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "Method Not Allowed"}), 405

# --- Optional: Root Route for Flask (for health checks) ---
@app.route('/', methods=['GET'])
def root_route():
    """Simple health check endpoint."""
    status_message = "Telegram Bot Webhook Listener is Live and Operational!"
    logger.info(f"Root route accessed. Status: {status_message}")
    return status_message, 200

# --- Flask Server Startup (for local development ONLY) ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set. Bot cannot start locally.")
        exit(1)
    
    if not ADMIN_CHAT_ID:
        print("WARNING: ADMIN_CHAT_ID environment variable is not set. Admin notifications will be skipped.")
    else:
        try:
            _ = int(ADMIN_CHAT_ID) # Validate if it's an integer
        except ValueError:
            print(f"WARNING: ADMIN_CHAT_ID '{ADMIN_CHAT_ID}' is not a valid integer. Admin notifications may fail.")

    logger.info("Starting local development server with PTB Application...")

    async def run_local_webhook_server():
        # Clear any old webhooks before starting local PTB webhook server
        # This will require a temporary Application instance for webhook setting
        local_app_builder = Application.builder().token(BOT_TOKEN).build()
        await local_app_builder.bot.set_webhook(url="")
        logger.info("Cleared any existing webhooks for local testing.")

        webserver_port = PORT
        logger.info(f"Local webserver for PTB starting on port {webserver_port}...")
        
        await application.run_webhook( # Use the globally defined 'application'
            listen="0.0.0.0",
            port=webserver_port,
            url_path="/webhook",
            webhook_url=WEBHOOK_URL # Use the locally determined WEBHOOK_URL
        )

    try:
        asyncio.run(run_local_webhook_server())
    except KeyboardInterrupt:
        logger.info("Local bot stopped by user.")
    except Exception as e:
        logger.error(f"Error running local bot: {e}", exc_info=True)
