#!/usr/bin/env python3
import argparse
import logging
import os
import sys

# Set up simple logging for console outputs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Fallback Dependency Handlers ---
try:
    import africastalking
except ImportError:
    logging.error("The 'africastalking' library is missing. Install it using: pip install africastalking")
    sys.exit(1)

try:
    import requests
except ImportError:
    logging.error("The 'requests' library is missing. Install it using: pip install requests")
    sys.exit(1)


# =====================================================================
# CONFIGURATION SETTINGS
# =====================================================================
# Credentials come from the environment, never from source (SPEC.md §11.1, repo
# rule: no secrets committed). Copy .env.example to .env and export it, or set
# the variables in your shell. Use "sandbox" as the username for the AT sandbox.
USERNAME = os.environ.get("AT_USERNAME", "sandbox")
API_KEY = os.environ.get("AT_API_KEY")
SENDER_ID = os.environ.get("AT_SENDER_ID") or None  # approved sender ID / shortcode, or None


def require_credentials() -> None:
    """Fail loudly when credentials are missing. Called at send time, not import time,
    so the web app can start (and run in DRY_RUN) without a key."""
    if not API_KEY:
        raise RuntimeError("AT_API_KEY is not set. Export it (see .env.example) before sending.")


# =====================================================================
# CORE FUNCTIONS
# =====================================================================

def translate_to_english(text: str, source_lang: str = "sw") -> str:
    """
    Translates an incoming SMS message or local input string into English.
    Utilizes a local dictionary fallback for quick offline Swahili terms, 
    and calls the free MyMemory Translation API for dynamic phrases.
    """
    # 1. Quick local fallback dictionary (useful if API fails or offline)
    fallback_dict = {
        "ndio": "yes",
        "la": "no",
        "habari": "hello",
        "asante": "thank you",
        "mkopo": "loan",
        "imekubaliwa": "approved",
        "imethibitishwa": "confirmed",
        "kataliwa": "declined"
    }
    
    clean_text = text.strip().lower()
    if clean_text in fallback_dict:
        return fallback_dict[clean_text].capitalize()

    # 2. Dynamic HTTP API Translation (MyMemory Free API - requires no key)
    try:
        url = f"https://api.mymemory.translated.net/get?q={text}&langpair={source_lang}|en"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            translated_text = data.get("responseData", {}).get("translatedText", "")
            if translated_text:
                return translated_text
    except Exception as e:
        logging.warning(f"Translation API request failed: {e}. Falling back to original text.")
        
    return text

def send_sms(phone_number: str, message: str) -> dict:
    """
    Initializes the Africa's Talking SDK and dispatches the SMS directly 
    to the designated customer phone number.
    """
    require_credentials()
    africastalking.initialize(USERNAME, API_KEY)
    sms_service = africastalking.SMS

    # Never log the raw MSISDN (SPEC.md §13) — last three digits are enough to debug.
    logging.info(f"Attempting to send SMS to ...{phone_number[-3:]}")
    try:
        # Sandbox accounts must not pass a sender_id.
        kwargs = {"sender_id": SENDER_ID} if SENDER_ID else {}
        response = sms_service.send(message, [phone_number], **kwargs)
        return response
    except Exception as e:
        logging.error(f"Failed to dispatch SMS: {e}")
        return None

# =====================================================================
# COMMAND LINE INTERFACE (CLI) ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CMD Client for Africa's Talking SMS and Translation API")
    parser.add_argument("--to", help="Target phone number (e.g., +254700000000)", type=str)
    parser.add_argument("--msg", help="Message body content text", type=str)
    parser.add_argument("--translate", help="Translate the message input to English first", action="store_true")
    parser.add_argument("--lang", help="Source language for translation (default: sw for Swahili)", default="sw")

    args = parser.parse_args()

    # If parameters were not passed, run interactive mode
    if not args.to or not args.msg:
        print("\n=== KilimoPoa Terminal SMS Client (Interactive Mode) ===")
        phone = input("Enter Customer Phone Number (e.g. +2547xxxxxxxx): ").strip()
        raw_msg = input("Enter SMS Message Body: ").strip()
        
        need_trans = input("Do you need to translate this message first? (yes/no): ").strip().lower()
        if need_trans in ("yes", "y"):
            lang = input("Enter source language code (default 'sw' for Swahili): ").strip() or "sw"
            msg_to_send = translate_to_english(raw_msg, source_lang=lang)
            print(f"-> Translated Message: '{msg_to_send}'")
        else:
            msg_to_send = raw_msg
            
        confirm = input(f"Confirm sending message to {phone}? (yes/no): ").strip().lower()
        if confirm in ("yes", "y"):
            result = send_sms(phone, msg_to_send)
            if result:
                print(f"\nSuccess Output: {result}")
        else:
            print("Operation aborted.")
            
    else:
        # CLI Mode
        final_message = args.msg
        if args.translate:
            logging.info(f"Translating message from '{args.lang}' to English...")
            final_message = translate_to_english(args.msg, source_lang=args.lang)
            logging.info(f"Translated Text: {final_message}")

        result = send_sms(args.to, final_message)
        if result:
            logging.info(f"API Dispatch Result: {result}")