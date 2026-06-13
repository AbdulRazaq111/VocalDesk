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

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


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

            # 1. User check ya create karna (PostgreSQL)
            db_user = db.query(models.User).filter(models.User.phone_number == user_phone).first()
            if not db_user:
                db_user = models.User(phone_number=user_phone)
                db.add(db_user)
                db.commit()
                db.refresh(db_user)

            # 📥 BUTTON INTERACTIVE PARSING NODE
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

            # --- SMART KNOWLEDGE RETRIEVAL (Kababjees Menu) ---
            search_words = user_text.lower().split()
            all_info = db.query(models.BusinessKnowledge).all()

            relevant_context = ""
            for item in all_info:
                if any(word in item.answer.lower() or word in item.question.lower() for word in search_words):
                    relevant_context += f"\nRelevant Info: {item.answer}"

            if not relevant_context:
                relevant_context = "Kababjees Menu includes Fried Chicken, Burgers, Sandwiches, and Exclusive Deals."

            # Memory Context (Pichli 10 baatein)
            history = db.query(models.Conversation).filter(
                models.Conversation.user_id == db_user.id
            ).order_by(models.Conversation.timestamp.desc()).limit(10).all()

            # --- STRICT SALESMAN SYSTEM PROMPT ---
            system_content = (
                f"You are the official Kababjees Voice Sales Agent.\n"
                f"STRICT INSTRUCTION: Use this Filtered Menu Data: {relevant_context}.\n\n"
                f"RULES IN ROMAN URDU:\n"
                f"1. Greet professionally: 'Asalam-o-Alaikum! Kababjees mein khush amdeed.'\n"
                f"2. PRICE LOCK: Item prices sirf database/context se use karo. Fake ya hardcoded price mat do.\n"
                f"3. Jab customer order items bataye, pehle items aur total confirm karo, phir address aur payment method mango.\n"
                f"4. IMPORTANT: [ORDER_DONE] sirf tab lagana jab customer ka order, address aur payment method teeno confirm ho chuke hon.\n"
                f"5. Final order summary hamesha exactly is format mein do:\n"
                f"ORDER SUMMARY:\n"
                f"2x Chicken Burger - Rs. 400\n"
                f"1x Cold Drink - Rs. 100\n"
                f"Subtotal: Rs. 500\n"
                f"Address: customer address\n"
                f"Payment: Cash On Delivery\n"
                f"[ORDER_DONE]\n"
                f"6. Har item alag line mein quantity + item name + price ke sath likho.\n"
                f"7. Short, Professional aur To-the-point baat karo."
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

            # Frontend Live State Sync
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
                    "payment_method": "Cash On Delivery",
                    "delivery_address": ""
                }
                orders_db.append(existing_session)

            # Conversation Database Save
            new_conv = models.Conversation(
                user_id=db_user.id,
                message_type="text",
                user_message=user_text,
                ai_response=ai_reply
            )
            db.add(new_conv)
            db.commit()

            # --- ORDER DONE TAG HANDLER ---
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
    """
    try:
        os.makedirs("static", exist_ok=True)
        file_path = f"static/{filename}"
        voices_res = client_eleven.voices.get_all()
        active_voice_id = voices_res.voices[0].voice_id
        audio = client_eleven.text_to_speech.convert(
            text=text,
            voice_id=active_voice_id,
            model_id="eleven_multilingual_v2",
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
            model_id="eleven_multilingual_v2",
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
# TWILIO VOICE CALL ENDPOINTS
# ==============================================================

def get_db_response(user_text):
    """Voice call ke liye AI response — same Groq + DB pipeline"""
    db = next(get_db())
    try:
        voice_user = db.query(models.User).filter(models.User.phone_number == "voice_call").first()
        if not voice_user:
            voice_user = models.User(phone_number="voice_call")
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

        system_content = (
            f"You are the official Kababjees Voice Sales Agent. "
            f"STRICT INSTRUCTION: Use this Filtered Menu Data: {relevant_context}. "
            f"\n\nRULES IN ROMAN URDU:"
            f"\n1. Greet professionally: 'Asalam-o-Alaikum! Kababjees mein khush amdeed.'"
            f"\n2. PRICE LOCK: Sirf database se price batao."
            f"\n3. NO REPETITION: Sirf us item ki baat karo jo user ne puchi hai."
            f"\n4. MATH LOGIC: Quantity bataye toh total calculate karo."
            f"\n5. UPSELL: 'Sir, iske sath Raita, Fries ya Cold drink add karni hai?'"
            f"\n6. ORDER SUMMARY: Bill, address aur payment method confirm karo."
            f"\n7. Jawab hamesha 1-2 lines mein do. Short aur professional."
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


@app.post("/voice")
async def voice_callback():
    greeting_text = "Asalam-o-Alaikum! Kababjees mein khush amdeed. Main apka AI sales agent hoon. Aap kya order karna chahenge?"

    audio_url = generate_voice_eleven_url(greeting_text, filename="greeting.mp3")

    response = VoiceResponse()

    if audio_url:
        response.play(audio_url)
    else:
        response.say(greeting_text, voice='Polly.Aditi', language='hi-IN')

    gather = Gather(
        input='speech',
        action='/handle-call',
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
    Customer ki speech → Groq AI jawab → ElevenLabs se play → dobara suno.
    Fallback: Polly agar ElevenLabs fail ho.
    Conversation loop call khatam hone tak chalta rehta hai.
    """
    response = VoiceResponse()

    if not SpeechResult or SpeechResult.strip() == "":
        # Kuch samajh nahi aaya — dobara poochho
        sorry_text = "Maaf kijiyega, mujhe aapki baat samajh nahi aayi. Zara dobara farmaiye?"
        audio_url = generate_voice_eleven_url(sorry_text, filename="sorry.mp3")

        if audio_url:
            response.play(audio_url)
        else:
            response.say(sorry_text, voice='Polly.Aditi', language='hi-IN')

        gather = Gather(
            input='speech',
            action='/handle-call',
            method='POST',
            language='ur-PK',
            speechTimeout='auto',
            timeout=5
        )
        response.append(gather)
        response.redirect('/voice')
        return Response(content=str(response), media_type="application/xml")

    print(f"Customer ne kaha (call): {SpeechResult}")

    # AI se jawab lo
    ai_reply = get_db_response(SpeechResult)
    print(f"AI jawab (call): {ai_reply}")

    # ElevenLabs se natural awaaz mein play karo
    audio_url = generate_voice_eleven_url(ai_reply, filename="reply.mp3")

    if audio_url:
        response.play(audio_url)
    else:
        response.say(ai_reply, voice='Polly.Aditi', language='hi-IN')

    # Conversation loop — dobara customer ko suno
    gather = Gather(
        input='speech',
        action='/handle-call',
        method='POST',
        language='ur-PK',
        speechTimeout='auto',
        timeout=5
    )
    response.append(gather)

    # Agar customer 5 second mein kuch na bole toh goodbye
    goodbye_text = "Shukria Kababjees choose karne ke liye! Khuda Hafiz."
    goodbye_url = generate_voice_eleven_url(goodbye_text, filename="goodbye.mp3")
    if goodbye_url:
        response.play(goodbye_url)
    else:
        response.say(goodbye_text, voice='Polly.Aditi', language='hi-IN')

    return Response(content=str(response), media_type="application/xml")


# ==============================================================
# SERVER START
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
