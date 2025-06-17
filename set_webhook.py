import asyncio
import os
import logging
from telegram import Update # Import Update directly from telegram
from telegram.ext import Application
# Removed: from telegram.constants import Update as TelegramUpdateType # Renamed Update for clarity
from dotenv import load_dotenv

# --- Configure Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv() # This ensures .env variables are available for this script too

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")

# Determine WEBHOOK_URL based on Render environment or .env for local testing
if RENDER_EXTERNAL_HOSTNAME:
    WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook"
else:
    WEBHOOK_URL = os.getenv("WEBHOOK_URL") # Ensure this is explicitly set in your .env for local testing of this script

async def set_telegram_webhook():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set. Cannot set webhook.")
        return
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL could not be determined. Make sure RENDER_EXTERNAL_HOSTNAME is set in Render, or WEBHOOK_URL in your .env.")
        return

    # Create a minimal Application instance just for webhook operations
    # No need for handlers or full setup here, as this is a temporary app for webhook setting.
    # We explicitly set a new event loop here to ensure it's clean for this script.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError: # No running loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.loop = loop # Assign the loop to this temporary application

    try:
        logger.info(f"Attempting to clear any old webhooks...")
        await application.bot.set_webhook(url="") # Clear any old webhook
        logger.info("Old webhooks cleared.")

        logger.info(f"Attempting to set webhook to: {WEBHOOK_URL}")
        # Use TelegramUpdateType.ALL_TYPES for allowed_updates
        await application.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=TelegramUpdateType.ALL_TYPES)
        logger.info(f"Webhook successfully set to: {WEBHOOK_URL}")
        logger.info(f"Verification URL: https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo")
    except Exception as e:
        logger.error(f"Failed to set Telegram webhook: {e}", exc_info=True)

if __name__ == "__main__":
    logger.info("Starting webhook setter script...")
    asyncio.run(set_telegram_webhook())
    logger.info("Webhook setter script finished.")
