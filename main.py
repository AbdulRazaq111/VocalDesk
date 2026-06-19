import os
import models
from database import engine, get_db
from sqlalchemy.orm import Session
from elevenlabs.client import ElevenLabs
import requests
from fastapi import FastAPI, Request, Form, Response
from dotenv import load_dotenv
from groq import Groq
from twilio.twiml.voice_response import VoiceResponse, Gather
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

print("STEP 1")
load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

models.Base.metadata.create_all(bind=engine)

client_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
client_eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

BASE_URL = os.getenv("BASE_URL", "https://vocaldesk-backend.onrender.com")


# Twilio direct TTS settings — faster than ElevenLabs audio generation for live calls
TWILIO_TTS_VOICE = "Polly.Kajal-Neural"
TWILIO_TTS_LANGUAGE = "hi-IN"

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ==============================================================
# STARTUP — Twilio Polly direct voice mode
# ==============================================================

@app.on_event("startup")
async def pre_generate_greeting():
    """
    Live calls ab ElevenLabs audio file par depend nahi karte.
    Twilio Polly Neural voice direct response.say() se bolti hai,
    isliye greeting pre-generate karne ki zaroorat nahi.
    Function ko intentionally rakha gaya hai taake baqi app structure same rahe.
    """
    try:
        print("✅ Twilio Polly direct voice mode enabled. ElevenLabs greeting generation skipped.")
    except Exception as e:
        print(f"Startup voice mode error: {e}")


# ==============================================================
# FRONTEND
# ==============================================================

@app.get("/", response_class=HTMLResponse)
async def read_frontend():
    file_path = os.path.join("templates", "index.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    return "VocalDesk Backend is Live! index.html was not found inside 'templates/' folder."


# ==============================================================
# WHATSAPP WEBHOOK
# ==============================================================

@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    return "Verification Failed"


orders_db = []


@app.get("/orders")
async def get_all_orders():
    """Yeh endpoint frontend ko live data supply karega"""
    return orders_db


@app.get("/api/analytics/calls")
async def get_call_analytics():
    """Frontend analytics ke liye voice call summary."""
    voice_calls = [o for o in orders_db if o.get("channel") == "Voice Call"]

    def safe_amount(value):
        try:
            return int(str(value).replace("Rs.", "").replace(",", "").strip())
        except Exception:
            return 0

    return {
        "total_calls": len(voice_calls),
        "active_calls": len([o for o in voice_calls if o.get("status") == "In Progress"]),
        "confirmed_calls": len([o for o in voice_calls if o.get("status") == "Confirmed"]),
        "completed_calls": len([o for o in voice_calls if o.get("status") == "Completed"]),
        "busy_calls": len([o for o in voice_calls if o.get("status") == "Busy"]),
        "no_answer_calls": len([o for o in voice_calls if o.get("status") == "No Answer"]),
        "failed_calls": len([o for o in voice_calls if o.get("status") == "Failed"]),
        "cancelled_calls": len([o for o in voice_calls if o.get("status") in ["Cancelled", "Canceled"]]),
        "voice_revenue": sum(safe_amount(o.get("bill_amount", 0)) for o in voice_calls if o.get("status") == "Confirmed"),
        "weekly_data": [
            max(1, len(voice_calls) - 6),
            max(2, len(voice_calls) - 4),
            max(3, len(voice_calls) - 3),
            max(4, len(voice_calls) - 2),
            max(5, len(voice_calls) - 1),
            max(6, len(voice_calls)),
            max(3, len(voice_calls) // 2)
        ],
        "calls": voice_calls[-20:]
    }


@app.post("/webhook")
async def handle_msg(request: Request):
    global orders_db

    data = await request.json()
    db = next(get_db())

    try:
        val = data['entry'][0]['changes'][0]['value']
        if 'messages' in val:
            msg_obj = val['messages'][0]
            user_phone = msg_obj['from']
            user_text = msg_obj.get('text', {}).get('body', "")

            # User check ya create karna
            db_user = db.query(models.User).filter(models.User.phone_number == user_phone).first()
            if not db_user:
                db_user = models.User(phone_number=user_phone)
                db.add(db_user)
                db.commit()
                db.refresh(db_user)

            # BUTTON INTERACTIVE PARSING
            if msg_obj.get('type') == 'interactive':
                button_id = msg_obj['interactive']['button_reply']['id']

                if button_id == "yes":
                    active_orders = [
                        order for order in orders_db
                        if order.get("customer_phone") == user_phone and order.get("status") == "In Progress"
                    ]
                    if active_orders:
                        order = active_orders[-1]
                        order["status"] = "Confirmed"
                        receipt = generate_kababjees_receipt(
                            order["id"],
                            user_phone,
                            order.get("items_detected", "Order details pending"),
                            order.get("bill_amount", "0"),
                            order.get("payment_method", "Cash On Delivery"),
                            order.get("delivery_address", "")
                        )
                        send_text(user_phone, receipt)
                        return {"status": "success"}

                    send_text(user_phone, "Maaf kijiyega, koi active order nahi mila. Fresh order ke liye 'Hi' bhejein.")
                    return {"status": "success"}

                elif button_id == "no":
                    active_orders = [
                        order for order in orders_db
                        if order.get("customer_phone") == user_phone and order.get("status") == "In Progress"
                    ]
                    if active_orders:
                        active_orders[-1]["status"] = "Cancelled"
                    send_text(user_phone, "Maaf kijiyega, aapka order cancel kar diya gaya. Dobara order ke liye 'Hi' bhejein.")
                    return {"status": "success"}

            print(f"Naya message: {user_text} from {user_phone}")

            # Greeting check
            greetings = ["hi", "hello", "hey", "assalam o alaikum", "aoa", "start"]
            if user_text.lower() in greetings:
                welcome_reply = "Asalam-o-Likum! Kababjees mein khush amdeed. Main apka order lene ke liye hazir hon. Aaj aap kya khana pasand karenge?"

                for order in orders_db:
                    if order.get("customer_phone") == user_phone and order.get("status") == "In Progress":
                        order["status"] = "Archived"

                orders_db.append({
                    "id": len(orders_db) + 1,
                    "customer_phone": user_phone,
                    "items_detected": "Pending Input...",
                    "bill_amount": "0",
                    "user_text": user_text,
                    "ai_text": welcome_reply,
                    "status": "In Progress",
                    "channel": "WhatsApp",
                    "payment_method": "Cash On Delivery",
                    "delivery_address": ""
                })

                send_text(user_phone, welcome_reply)
                return {"status": "success"}

            # Thanks check
            thanks_words = ["thanks", "thank you", "thankyou", "shukriya", "jazakallah", "ok", "okay"]
            if user_text.lower().strip() in thanks_words:
                send_text(user_phone, "Aapka shukriya! Kababjees order confirm ho chuka hai.")
                return {"status": "success"}

            # SMART KNOWLEDGE RETRIEVAL
            search_words = user_text.lower().split()
            all_info = db.query(models.BusinessKnowledge).all()

            relevant_context = ""
            for item in all_info:
                if any(word in item.answer.lower() or word in item.question.lower() for word in search_words):
                    relevant_context += f"\nRelevant Info: {item.answer}"

            if not relevant_context:
                relevant_context = "Kababjees Menu includes Fried Chicken, Burgers, Sandwiches, and Exclusive Deals."

            history = db.query(models.Conversation).filter(
                models.Conversation.user_id == db_user.id
            ).order_by(models.Conversation.timestamp.desc()).limit(10).all()

            system_content = (
                f"You are the official Kababjees Voice Sales Agent.\n"
                f"STRICT INSTRUCTION: Use this Filtered Menu Data: {relevant_context}.\n\n"
                f"RULES IN ROMAN URDU:\n"
                f"1. Greet professionally: 'Asalam-o-Alaikum! Kababjees mein khush amdeed.'\n"
                f"2. PRICE LOCK: Item prices sirf database/context se use karo. Fake ya hardcoded price mat do.\n"
                f"3. ITEM NAME LOCK: Customer ne jo exact item name bola hai, wahi use karo. Apni taraf se item name change mat karo.\n"
                f"4. MATH LOCK: Quantity x price = total. Yeh calculation khud karo aur dobara check karo.\n"
                f"5. Jab customer order items bataye, pehle items aur total confirm karo, phir address aur payment method mango.\n"
                f"6. IMPORTANT: [ORDER_DONE] sirf tab lagana jab customer ka order, address aur payment method teeno confirm ho chuke hon.\n"
                f"7. Final order summary hamesha exactly is format mein do:\n"
                f"ORDER SUMMARY:\n"
                f"2x Chicken Burger - Rs. 400\n"
                f"1x Cold Drink - Rs. 100\n"
                f"Subtotal: Rs. 500\n"
                f"Address: customer address\n"
                f"Payment: Cash On Delivery\n"
                f"[ORDER_DONE]\n"
                f"8. Har item alag line mein quantity + item name + price ke sath likho.\n"
                f"9. Short, Professional aur To-the-point baat karo."
            )

            messages = [{"role": "system", "content": system_content}]
            for h in reversed(history):
                messages.append({"role": "user", "content": h.user_message})
                messages.append({"role": "assistant", "content": h.ai_response})
            messages.append({"role": "user", "content": user_text})

            completion = client_groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.2
            )
            ai_reply = completion.choices[0].message.content
            print(f"AI Response: {ai_reply}")

            bot_reply = ai_reply
            active_sessions = [
                order for order in orders_db
                if order.get("customer_phone") == user_phone and order.get("status") == "In Progress"
            ]
            existing_session = active_sessions[-1] if active_sessions else None

            if existing_session:
                existing_session["user_text"] = existing_session.get("user_text", "") + f"\n\nCustomer: {user_text}"
                existing_session["ai_text"] = existing_session.get("ai_text", "") + f"\n\nSana AI: {bot_reply}"
            else:
                existing_session = {
                    "id": len(orders_db) + 1,
                    "customer_phone": user_phone,
                    "items_detected": "Pending Input...",
                    "bill_amount": "0",
                    "user_text": user_text,
                    "ai_text": bot_reply,
                    "status": "In Progress",
                    "channel": "WhatsApp",
                    "payment_method": "Cash On Delivery",
                    "delivery_address": ""
                }
                orders_db.append(existing_session)

            new_conv = models.Conversation(
                user_id=db_user.id,
                message_type="text",
                user_message=user_text,
                ai_response=ai_reply
            )
            db.add(new_conv)
            db.commit()

            # ORDER DONE TAG HANDLER
            if "[order_done]" in ai_reply.lower():
                clean_reply = ai_reply.replace("[ORDER_DONE]", "").replace("[order_done]", "").strip()
                clean_reply_lower = clean_reply.lower()

                asking_more_details = (
                    "address kya" in clean_reply_lower or
                    "payment method kya" in clean_reply_lower or
                    "address aur payment" in clean_reply_lower or
                    "address bhej" in clean_reply_lower or
                    "payment method bat" in clean_reply_lower
                )

                if asking_more_details:
                    send_text(user_phone, clean_reply)
                else:
                    parsed_items, parsed_subtotal, parsed_payment, parsed_address = extract_order_details(clean_reply)

                    active_orders = [
                        order for order in orders_db
                        if order.get("customer_phone") == user_phone and order.get("status") == "In Progress"
                    ]

                    if active_orders:
                        order = active_orders[-1]
                    else:
                        order = {
                            "id": len(orders_db) + 1,
                            "customer_phone": user_phone,
                            "items_detected": "Pending Input...",
                            "bill_amount": "0",
                            "user_text": user_text,
                            "ai_text": clean_reply,
                            "status": "In Progress",
                            "channel": "WhatsApp",
                            "payment_method": "Cash On Delivery",
                            "delivery_address": ""
                        }
                        orders_db.append(order)

                    order["items_detected"] = parsed_items
                    order["bill_amount"] = parsed_subtotal
                    order["payment_method"] = parsed_payment
                    order["delivery_address"] = parsed_address

                    print("FINAL ORDER SAVED:", order)
                    send_whatsapp_buttons(user_phone, clean_reply)
            else:
                send_text(user_phone, ai_reply)

    except Exception as e:
        print(f"Error in handle_msg: {e}")
    finally:
        db.close()

    return {"status": "ok"}


# ==============================================================
# WHATSAPP HELPER FUNCTIONS
# ==============================================================

def send_text(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    response = requests.post(url, headers=headers, json=payload)
    print(f"WhatsApp Status: {response.status_code}")


def send_whatsapp_buttons(to, text_body):
    """Meta Interactive Buttons — title under 20 chars limit"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": str(text_body)},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "yes", "title": "Confirm Order"}},
                    {"type": "reply", "reply": {"id": "no", "title": "Cancel"}}
                ]
            }
        }
    }
    res = requests.post(url, headers=headers, json=payload)
    print(f"Meta Trigger Status: {res.status_code} | {res.text}")


def send_audio(to, audio_path):
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {'file': (audio_path, open(audio_path, 'rb'), 'audio/mpeg'), 'messaging_product': (None, 'whatsapp')}
    res = requests.post(url, headers=headers, files=files)
    media_id = res.json().get('id')
    if media_id:
        send_url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
        payload = {"messaging_product": "whatsapp", "to": to, "type": "audio", "audio": {"id": media_id}}
        requests.post(send_url, headers=headers, json=payload)
        print("Voice note bhej diya!")


# ==============================================================
# ORDER PARSING & RECEIPT
# ==============================================================

def extract_order_details(summary_text):
    """AI final summary se item-wise receipt data nikalne ke liye"""
    import re

    items = []
    subtotal = 0
    payment_method = "Cash On Delivery"
    delivery_address = ""

    def add_item(item_name, item_price):
        nonlocal subtotal
        item_name = item_name.strip(" .,-")
        item_price = int(item_price)
        if not item_name:
            return
        formatted = f"{item_name:<22} Rs. {item_price}"
        if formatted not in items:
            items.append(formatted)
            subtotal += item_price

    for raw_line in summary_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        line_lower = line.lower()

        if line_lower.startswith("payment:"):
            payment_method = line.split(":", 1)[1].strip() or payment_method
            continue

        if line_lower.startswith("address:"):
            delivery_address = line.split(":", 1)[1].strip()
            continue

        total_match = re.search(r'(subtotal|total bill|total)\s*:?.*?(?:rs\.?\s*)?(\d+)', line, re.IGNORECASE)
        if total_match:
            subtotal = int(total_match.group(2))
            continue

        item_match = re.search(r'^(\d+\s*x?\s+.+?)\s*-\s*(?:rs\.?\s*)?(\d+)', line, re.IGNORECASE)
        if item_match:
            add_item(item_match.group(1), item_match.group(2))
            continue

        sentence_matches = re.findall(
            r'(\d+\s*x?\s+[A-Za-z ]+?)\s+(?:ki|ka|ke)?\s*price\s*(?:rs\.?|is|hai|=)?\s*(\d+)',
            line, flags=re.IGNORECASE
        )
        for item_name, item_price in sentence_matches:
            add_item(item_name, item_price)

    if subtotal == 0 and items:
        prices = re.findall(r'Rs\.\s*(\d+)', "\n".join(items))
        subtotal = sum(int(p) for p in prices)

    if subtotal == 0:
        numbers = re.findall(
            r'(?:subtotal|total bill|total|bill)\s*(?:is|hai|:)?\s*(?:rs\.?\s*)?(\d+)',
            summary_text.lower()
        )
        if numbers:
            subtotal = max(int(n) for n in numbers)

    items_text = "\n".join(items) if items else "Order details pending"
    print("PARSED RECEIPT DATA:", {"items": items_text, "subtotal": subtotal, "payment": payment_method, "address": delivery_address})
    return items_text, str(subtotal), payment_method, delivery_address


def generate_kababjees_receipt(order_id, customer_phone, items_text, amount, payment_method="Cash On Delivery", delivery_address=""):
    """Receipt ka standard layout — WhatsApp par land karega"""
    import datetime
    current_date = datetime.datetime.now().strftime("%d/%m/%Y")
    bill_amt = int(amount) if str(amount).isdigit() else 0
    address_line = f"Address: {delivery_address}\n" if delivery_address else ""

    return (
        f"============================\n"
        f"      KABABJEES AI AGENT\n"
        f"============================\n"
        f"Order ID: #00{order_id}\n"
        f"Date: {current_date}\n"
        f"Customer: {customer_phone}\n"
        f"{address_line}"
        f"----------------------------\n"
        f"ITEMS:\n{items_text}\n"
        f"----------------------------\n"
        f"TOTAL BILL:           Rs. {bill_amt}\n"
        f"----------------------------\n"
        f"Payment: {payment_method}\n"
        f"Status: CONFIRMED ✓\n\n"
        f"Thank you for choosing Kababjees!"
    )


# ==============================================================
# ELEVENLABS VOICE FUNCTIONS
# ==============================================================

def generate_voice_eleven_url(text: str, filename: str = "audio.mp3") -> str | None:
    """
    ElevenLabs se audio generate karke static/ mein save karta hai.
    Twilio ke liye publicly accessible URL return karta hai.
    ✅ eleven_turbo_v2_5 model use — fastest + clear voice, no background noise.
    """
    try:
        os.makedirs("static", exist_ok=True)
        file_path = f"static/{filename}"

        # ✅ Agar greeting already exist kare toh dobara generate mat karo (delay fix)
        if filename == "greeting.mp3" and os.path.exists(file_path):
            return f"{BASE_URL}/static/{filename}"

        voices_res = client_eleven.voices.get_all()
        active_voice_id = voices_res.voices[0].voice_id

        audio = client_eleven.text_to_speech.convert(
            text=text,
            voice_id=active_voice_id,
            model_id="eleven_turbo_v2_5",   # ✅ Fastest model — low latency, clear voice
            output_format="mp3_44100_128",
        )
        with open(file_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)

        public_url = f"{BASE_URL}/static/{filename}"
        print(f"ElevenLabs audio ready: {public_url}")
        return public_url
    except Exception as e:
        print(f"ElevenLabs URL Error: {e}")
        return None


def generate_voice_eleven(text):
    """WhatsApp voice note ke liye — local file path return karta hai"""
    file_path = "reply_audio.mp3"
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        voices_res = client_eleven.voices.get_all()
        active_voice_id = voices_res.voices[0].voice_id
        audio = client_eleven.text_to_speech.convert(
            text=text,
            voice_id=active_voice_id,
            model_id="eleven_turbo_v2_5",
            output_format="mp3_44100_128",
        )
        with open(file_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        return file_path
    except Exception as e:
        print(f"ElevenLabs Error: {e}")
        return None



# ==============================================================
# VOICE CALL STATUS TRACKING
# ==============================================================

def normalize_twilio_call_status(raw_status: str) -> str:
    """Twilio raw status ko dashboard friendly status mein convert karta hai."""
    status = (raw_status or "").strip().lower()
    mapping = {
        "queued": "Queued",
        "initiated": "Initiated",
        "ringing": "Ringing",
        "in-progress": "In Progress",
        "answered": "In Progress",
        "completed": "Completed",
        "busy": "Busy",
        "no-answer": "No Answer",
        "failed": "Failed",
        "canceled": "Cancelled",
        "cancelled": "Cancelled"
    }
    return mapping.get(status, raw_status or "Unknown")


def update_voice_call_status(call_sid: str, twilio_status: str, caller_number: str = "Unknown", duration: str = "0"):
    """
    Twilio CallSid ke base par call session update karta hai.
    Confirmed order ko Completed se overwrite nahi karta.
    """
    global orders_db
    friendly_status = normalize_twilio_call_status(twilio_status)
    call_sid = call_sid or "voice_call"

    call_session = next((o for o in orders_db if o.get("call_sid") == call_sid), None)

    if not call_session:
        call_session = {
            "id": len(orders_db) + 1,
            "customer_phone": caller_number or "Unknown",
            "call_sid": call_sid,
            "items_detected": "Voice call tracked",
            "bill_amount": "0",
            "user_text": "",
            "ai_text": "",
            "status": "In Progress",
            "call_status": friendly_status,
            "call_duration": duration or "0",
            "channel": "Voice Call",
            "payment_method": "Cash On Delivery",
            "delivery_address": ""
        }
        orders_db.append(call_session)

    call_session["call_status"] = friendly_status
    call_session["call_duration"] = duration or call_session.get("call_duration", "0")

    if caller_number and caller_number != "Unknown":
        call_session["customer_phone"] = caller_number

    current_status = call_session.get("status", "In Progress")

    # Confirmed order ko call completed status se overwrite nahi karna
    if current_status == "Confirmed":
        return call_session

    if friendly_status in ["Queued", "Initiated", "Ringing", "In Progress"]:
        call_session["status"] = "In Progress"
    elif friendly_status in ["Completed", "Busy", "No Answer", "Failed", "Cancelled"]:
        call_session["status"] = friendly_status

    return call_session


@app.post("/voice-status")
async def voice_status_callback(request: Request):
    """
    Twilio status callback endpoint.
    Twilio yahan call lifecycle bhej sakta hai:
    initiated, ringing, in-progress, completed, busy, no-answer, failed.
    """
    form_data = await request.form()

    call_sid = form_data.get("CallSid", "voice_call")
    call_status = form_data.get("CallStatus", "Unknown")
    caller_number = form_data.get("From", "Unknown")
    duration = form_data.get("CallDuration", "0")

    updated = update_voice_call_status(
        call_sid=call_sid,
        twilio_status=call_status,
        caller_number=caller_number,
        duration=duration
    )

    print("📞 TWILIO CALL STATUS UPDATED:", {
        "call_sid": call_sid,
        "call_status": call_status,
        "dashboard_status": updated.get("status"),
        "duration": duration
    })

    return {
        "success": True,
        "call_sid": call_sid,
        "status": updated.get("status"),
        "call_status": updated.get("call_status")
    }


# ==============================================================
# TWILIO VOICE CALL ENDPOINTS
# ==============================================================

def get_db_response(user_text, call_sid="voice_call"):
    """
    Voice call ke liye AI response — same Groq + DB pipeline.
    ✅ Greeting repeat nahi hogi — history check se skip hogi.
    ✅ Item name aur price calculation strict rules ke saath.
    """
    global orders_db
    db = next(get_db())
    try:
        voice_user = db.query(models.User).filter(models.User.phone_number == call_sid).first()
        if not voice_user:
            voice_user = models.User(phone_number=call_sid)
            db.add(voice_user)
            db.commit()
            db.refresh(voice_user)

        search_words = user_text.lower().split()
        all_info = db.query(models.BusinessKnowledge).all()
        relevant_context = ""
        for item in all_info:
            if any(word in item.answer.lower() or word in item.question.lower() for word in search_words):
                relevant_context += f"\nRelevant Info: {item.answer}"

        if not relevant_context:
            relevant_context = "Kababjees Menu includes Fried Chicken, Burgers, Sandwiches, and Exclusive Deals."

        history = db.query(models.Conversation).filter(
            models.Conversation.user_id == voice_user.id
        ).order_by(models.Conversation.timestamp.desc()).limit(10).all()

        # ✅ Agar history hai toh greeting dobara mat karo
        is_first_message = len(history) == 0

        system_content = (
            f"You are the official Kababjees Voice Sales Agent. "
            f"STRICT INSTRUCTION: Use this Filtered Menu Data: {relevant_context}. "
            f"\n\nRULES:"
            f"\n1. {'Greet once: Kababjees mein khush amdeed.' if is_first_message else 'DO NOT greet again. Customer already greeted. Go straight to helping.'}"
            f"\n2. PRICE LOCK: Sirf database se exact price batao. Koi estimated ya rounded price mat do."
            f"\n3. ITEM NAME LOCK: Customer ne jo exact item bola wahi repeat karo. Apni taraf se item name change ya rename mat karo."
            f"\n4. MATH LOCK: Total = quantity x unit price. Calculate karo aur confirm karo."
            f"\n5. UPSELL: 'Sir, iske sath kuch aur add karni hai?'"
            f"\n6. Jab tak items, total bill, delivery address aur payment method complete na hon, order confirm mat bolo."
            f"\n7. Jab customer final yes/confirm kare aur items + bill + address + payment complete hon, final summary exactly is format mein do:"
            f"\nORDER SUMMARY:"
            f"\n2x Chicken Burger - Rs. 400"
            f"\nSubtotal: Rs. 400"
            f"\nAddress: customer address"
            f"\nPayment: Cash On Delivery"
            f"\n[ORDER_DONE]"
            f"\n8. [ORDER_DONE] sirf final confirmation ke time lagana. Is tag ke baghair backend order confirmed nahi karega."
            f"\n9. Normal jawab hamesha 1-2 lines mein do. Short aur professional."
        )

        messages = [{"role": "system", "content": system_content}]
        for h in reversed(history):
            messages.append({"role": "user", "content": h.user_message})
            messages.append({"role": "assistant", "content": h.ai_response})
        messages.append({"role": "user", "content": user_text})

        completion = client_groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.1   # ✅ Lower temperature — accurate calculations
        )
        ai_reply = completion.choices[0].message.content

        # Frontend call session update
        call_session = next(
            (o for o in orders_db if o.get("call_sid") == call_sid and o.get("status") == "In Progress"),
            None
        )
        if call_session:
            call_session["user_text"] = call_session.get("user_text", "") + f"\n\nCustomer: {user_text}"
            call_session["ai_text"] = call_session.get("ai_text", "") + f"\n\nAI: {ai_reply}"

        new_conv = models.Conversation(
            user_id=voice_user.id,
            message_type="voice",
            user_message=user_text,
            ai_response=ai_reply
        )
        db.add(new_conv)
        db.commit()

        return ai_reply
    except Exception as e:
        print(f"Error in get_db_response: {e}")
        return "Maaf kijiyega, mujhe abhi jawab nahi mil raha."
    finally:
        db.close()


def finalize_voice_order_if_done(call_sid, ai_reply, user_text=""):
    """
    Voice call mein WhatsApp button nahi hota.
    Isliye jab AI final summary ke sath [ORDER_DONE] bhejta hai,
    backend us call session ko Confirmed mark karta hai taake frontend par show ho.
    """
    global orders_db

    clean_reply = ai_reply.replace("[ORDER_DONE]", "").replace("[order_done]", "").strip()
    ai_lower = ai_reply.lower()
    clean_lower = clean_reply.lower()

    has_order_done_tag = "[order_done]" in ai_lower

    asking_more_details = (
        "address kya" in clean_lower or
        "payment method kya" in clean_lower or
        "address aur payment" in clean_lower or
        "address bhej" in clean_lower or
        "payment method bat" in clean_lower or
        "payment method confirm" in clean_lower
    )

    # Fallback: agar AI natural language mein confirmed bol de lekin tag miss kar de
    confirmed_phrases = [
        "order confirm",
        "order confirmed",
        "confirm ho gaya",
        "confirm ho chuka",
        "order ho gaya",
        "apka order confirm",
        "aapka order confirm",
    ]
    user_confirm_words = ["yes", "han", "haan", "confirm", "ok", "okay", "theek", "done", "thanks", "thank you", "shukriya"]

    natural_confirm = (
        any(p in clean_lower for p in confirmed_phrases)
        and any(w in user_text.lower() for w in user_confirm_words)
        and not asking_more_details
    )

    if not has_order_done_tag and not natural_confirm:
        return ai_reply, False

    if asking_more_details:
        return clean_reply, False

    # Existing call session find karo
    order = next(
        (o for o in orders_db if o.get("call_sid") == call_sid and o.get("status") == "In Progress"),
        None
    )
    if not order:
        order = next((o for o in orders_db if o.get("call_sid") == call_sid), None)

    if not order:
        order = {
            "id": len(orders_db) + 1,
            "customer_phone": "Voice Caller",
            "call_sid": call_sid,
            "items_detected": "Order details pending",
            "bill_amount": "0",
            "user_text": user_text,
            "ai_text": clean_reply,
            "status": "In Progress",
            "call_status": "In Progress",
            "call_duration": "0",
            "channel": "Voice Call",
            "payment_method": "Cash On Delivery",
            "delivery_address": ""
        }
        orders_db.append(order)

    parsed_items, parsed_subtotal, parsed_payment, parsed_address = extract_order_details(clean_reply)

    # Agar parsing empty ho, existing data preserve karo
    if parsed_items != "Order details pending":
        order["items_detected"] = parsed_items
    if parsed_subtotal and str(parsed_subtotal) != "0":
        order["bill_amount"] = parsed_subtotal
    if parsed_payment:
        order["payment_method"] = parsed_payment
    if parsed_address:
        order["delivery_address"] = parsed_address

    order["status"] = "Confirmed"
    order["call_status"] = order.get("call_status", "In Progress")
    order["ai_text"] = order.get("ai_text", "") + f"\n\nAI: {clean_reply}"

    print("VOICE ORDER CONFIRMED:", order)
    return clean_reply, True


@app.api_route("/voice", methods=["GET", "POST"])
async def voice_callback(request: Request):
    """
    Twilio incoming call yahan aata hai.
    ✅ Twilio Polly Neural voice direct speak karti hai — ElevenLabs delay nahi.
    ✅ Call session frontend orders_db mein create hota hai.
    Twilio Voice Webhook: https://vocaldesk-backend.onrender.com/voice
    Twilio Status Callback: https://vocaldesk-backend.onrender.com/voice-status
    """
    global orders_db

    form_data = await request.form()
    call_sid = form_data.get("CallSid", "voice_call")
    caller_number = form_data.get("From", "Unknown")

    # Frontend pe call session create karo
    existing = next((o for o in orders_db if o.get("call_sid") == call_sid), None)
    if not existing:
        orders_db.append({
            "id": len(orders_db) + 1,
            "customer_phone": caller_number,
            "call_sid": call_sid,
            "items_detected": "Call In Progress...",
            "bill_amount": "0",
            "user_text": "",
            "ai_text": "",
            "status": "In Progress",
            "call_status": "In Progress",
            "call_duration": "0",
            "channel": "Voice Call",
            "payment_method": "Cash On Delivery",
            "delivery_address": ""
        })

    # ✅ Twilio Polly direct greeting — ElevenLabs audio generation/play removed for fast response
    response = VoiceResponse()
    response.say(
        "Kababjees mein khush amdeed. Aap kya order karna chahenge?",
        voice=TWILIO_TTS_VOICE,
        language=TWILIO_TTS_LANGUAGE
    )

    gather = Gather(
        input='speech',
        action=f'/handle-call?call_sid={call_sid}',
        method='POST',
        language='ur-PK',
        speechTimeout='auto',
        timeout=5
    )
    response.append(gather)
    response.redirect('/voice')

    return Response(content=str(response), media_type="application/xml")


@app.post("/handle-call")
async def handle_call(request: Request, SpeechResult: str = Form(None)):
    """
    Customer ki speech → Groq AI jawab → Twilio Polly direct speak → dobara suno.
    ✅ Greeting repeat nahi hogi.
    ✅ ElevenLabs audio generation delay remove.
    ✅ Har turn frontend orders_db mein update hota hai.
    """
    global orders_db

    form_data = await request.form()
    call_sid = request.query_params.get("call_sid") or form_data.get("CallSid", "voice_call")

    response = VoiceResponse()

    if not SpeechResult or SpeechResult.strip() == "":
        sorry_text = "Maaf kijiyega, dobara farmaiye?"
        response.say(
            sorry_text,
            voice=TWILIO_TTS_VOICE,
            language=TWILIO_TTS_LANGUAGE
        )
        gather = Gather(
            input='speech',
            action=f'/handle-call?call_sid={call_sid}',
            method='POST',
            language='ur-PK',
            speechTimeout='auto',
            timeout=5
        )
        response.append(gather)
        response.redirect('/voice')
        return Response(content=str(response), media_type="application/xml")

    print(f"Customer ne kaha (call): {SpeechResult}")

    # AI jawab lo
    ai_reply = get_db_response(SpeechResult, call_sid=call_sid)

    # Agar voice call mein order final ho gaya ho to frontend ke liye status Confirmed karo
    ai_reply, voice_order_confirmed = finalize_voice_order_if_done(call_sid, ai_reply, SpeechResult)
    print(f"AI jawab (call): {ai_reply}")
    if voice_order_confirmed:
        print("✅ Voice call order frontend par Confirmed show hoga.")

    # Twilio Polly direct awaaz — ElevenLabs audio generation/play removed for faster live call response
    response.say(
        ai_reply,
        voice=TWILIO_TTS_VOICE,
        language=TWILIO_TTS_LANGUAGE
    )

    # Conversation loop — dobara suno
    gather = Gather(
        input='speech',
        action=f'/handle-call?call_sid={call_sid}',
        method='POST',
        language='ur-PK',
        speechTimeout='auto',
        timeout=5
    )
    response.append(gather)

    # Goodbye agar customer kuch na bole
    goodbye_text = "Shukria Kababjees choose karne ke liye! Khuda Hafiz."
    response.say(
        goodbye_text,
        voice=TWILIO_TTS_VOICE,
        language=TWILIO_TTS_LANGUAGE
    )

    return Response(content=str(response), media_type="application/xml")


# ==============================================================
# SERVER START
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)