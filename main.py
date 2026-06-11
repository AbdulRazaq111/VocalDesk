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


@app.get("/", response_class=HTMLResponse)
async def read_frontend():
    file_path = os.path.join("templates", "index.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    return "VocalDesk Backend is Live! index.html was not found inside 'templates/' folder."


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
    global orders_db  # <-- 4 spaces ke sath data ke bilkul upar
    import re
    
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
                    for order in orders_db:
                        if order["customer_phone"] == user_phone and order["status"] == "In Progress":
                            order["status"] = "Confirmed"
                            receipt = generate_kababjees_receipt(order["id"], user_phone, order["items_detected"], order["bill_amount"])
                            send_text(user_phone, receipt)
                            return {"status": "success"}
                            
                elif button_id == "no":
                    for order in orders_db:
                        if order["customer_phone"] == user_phone and order["status"] == "In Progress":
                            order["status"] = "Cancelled"
                    send_text(user_phone, "Maaf kijiyega, aapka Kababjees order cancel kar diya gaya hai. Dobara order ke liye 'Hi' bhejein.")
                    return {"status": "success"}

            print(f"Naya message: {user_text} from {user_phone}")

            greetings = ["hi", "hello", "hey", "assalam o alaikum", "aoa", "start"]
            if user_text.lower() in greetings:
                welcome_reply = "Asalam-o-Likum! Kababjees mein khush amdeed. Main apka order lene ke liye hazir hon. Aaj aap kya khana pasand karenge?"
                
                # Deactivate older completed loops to allow multiple test rounds smoothly
                for o in orders_db:
                    if o["customer_phone"] == user_phone and o["status"] in ["Confirmed", "Cancelled"]:
                        o["status"] = "Archived"

                orders_db.append({
                    "id": len(orders_db) + 1,
                    "customer_phone": user_phone,
                    "items_detected": "Pending Input...",
                    "bill_amount": "0",
                    "user_text": user_text,
                    "ai_text": welcome_reply,
                    "status": "In Progress"
                })

                send_text(user_phone, welcome_reply)
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
            
            # --- THE STRICT SALESMAN LOGIC WITH VALUE PARSING DIRECTIONS ---
            system_content = (
                f"You are the official Kababjees Voice Sales Agent.\n"
                f"STRICT INSTRUCTION: Use this Filtered Menu Data: {relevant_context}.\n\n"
                f"RULES IN ROMAN URDU:\n"
                f"1. Greet professionally when a flow triggers.\n"
                f"2. PRICE INTEGRITY: Match item selection strictly with prices inside database bounds.\n"
                f"3. CRITICAL OUTPUT FORMAT: Jab user kahe ke order confirm karo ya bill batao, toh aap user ke selected items ka naam aur unki itemized individual price sath batayein (e.g., '1x Chicken Burger - Rs. 350').\n"
                f"4. Message ke bilkul end mein summary khatam hote hi exact yeh text short string append karein bina kisi extra word ke: [ORDER_DONE]\n"
                f"Jawab hamesha polite aur to-the-point do."
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
            existing_session = next((order for order in orders_db if order["customer_phone"] == user_phone and order["status"] == "In Progress"), None)
            
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
                    "status": "In Progress"
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

            # --- 🎯 STABLE DYNAMIC ITEM AND BILL EXTRACTOR SWITCH ---
            if "[order_done]" in ai_reply.lower():
                clean_reply = ai_reply.replace("[ORDER_DONE]", "").replace("[order_done]", "").strip()
                
                # Regex patterns to pull exact total integers generated by Llama out dynamically
                numbers_found = re.findall(r'(?:rs\.?\s*|total\s* bill\s* is\s*|bill\s*|rs\s*)(\d+)', ai_reply.lower())
                detected_price = "400" # Safe default backup boundary
                if numbers_found:
                    # Target the largest found number assuming it represents the summed bill total value
                    detected_price = str(max([int(n) for n in numbers_found]))
                
                # Dynamic matching formatting to separate multiline layouts safely for receipt engine representation
                lines = clean_reply.split('\n')
                items_captured = []
                for line in lines:
                    if '-' in line or 'rs' in line.lower() or 'burger' in line.lower() or 'deal' in line.lower():
                        if "total" not in line.lower() and "bill" not in line.lower():
                            items_captured.append(line.strip())
                
                items_summary_string = "\n".join(items_captured) if items_captured else "1x Kababjees Premium Selection"

                if existing_session:
                    existing_session["items_detected"] = items_summary_string
                    existing_session["bill_amount"] = detected_price
                
                send_whatsapp_buttons(user_phone, clean_reply)
            else:
                send_text(user_phone, ai_reply)
            
    except Exception as e:
        print(f"Error in handle_msg: {e}")
    finally:
        db.close()
        
    return {"status": "ok"}


def send_text(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    response = requests.post(url, headers=headers, json=payload)
    print(f"WhatsApp Status: {response.status_code}")


def send_whatsapp_buttons(to, text_body):
    """Meta Interactive Protocol: String length locked under 20 chars limit"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}", 
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": str(text_body)
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply", 
                        "reply": {"id": "yes", "title": "Confirm Order"} # Under 20 chars limit
                    },
                    {
                        "type": "reply", 
                        "reply": {"id": "no", "title": "Cancel"} # Under 20 chars limit
                    }
                ]
            }
        }
    }
    res = requests.post(url, headers=headers, json=payload)
    print(f"Meta Trigger Status: {res.status_code} | Payload Response: {res.text}")

def generate_kababjees_receipt(order_id, customer_phone, items_text, amount):
    """VIP Dynamic Receipt - Stripped GST + Auto Aligned Itemized Pricing Logs"""
    import datetime
    current_date = datetime.datetime.now().strftime("%d/%m/%Y")
    
    # Ensuring clean integer parsing for structural safety
    total_bill = int(amount) if str(amount).isdigit() else 400
    
    receipt_text = (
        f"============================\n"
        f"      KABABJEES AI AGENT\n"
        f"============================\n"
        f"Order ID: #00{order_id}\n"
        f"Date: {current_date}\n"
        f"Customer: {customer_phone}\n"
        f"----------------------------\n"
        f"ITEMS DETECTED:\n"
        f"{items_text}\n"
        f"----------------------------\n"
        f"TOTAL BILL:           Rs. {total_bill}\n"
        f"----------------------------\n"
        f"Payment: Cash On Delivery\n"
        f"Status: CONFIRMED ✓\n\n"
        f"Thank you for choosing Kababjees!"
    )
    return receipt_text

def generate_voice_eleven(text):
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


def get_db_response(user_text):
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
            f"\n1. Greet professionally: 'Asalam-o-Alaikum! Kababjees mein khush amdeed. Main apka order lene ke liye hazir hon.'"
            f"\n2. PRICE LOCK: Agar customer kisi item ka puche, toh context mein 'EXACT PRICES FOUND' wala hissa lazmi check karo. Agar price mil jaye toh batana zaroori hai."
            f"\n3. NO REPETITION: Poora menu list mat karo. Sirf us item ki baat karo jo user ne puchi hai."
            f"\n4. MATH LOGIC: Agar user quantity bataye (e.g. 2 pieces), toh total price calculate karke batao."
            f"\n5. UPSELL: Main item ke baad pucho: 'Sir, iske sath Raita, Fries ya Cold drink add karni hai?'"
            f"\n6. ORDER SUMMARY: Aakhir mein bill, Delivery address aur payment method confirm karo."
            f"\n7. Speak like a professional waiter, short and polite."
            f"\n7. Tum Kababjees ke salesman ho. Jawab hamesha 1-2 lines mein do."
            f"\nAgar koi price puche toh sirf price aur item ka naam batao, lambay paragraphs mat likho."
            f"\nShort, Professional aur To-the-point baat karo."
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
    response = VoiceResponse()
    response.say(
        "Assalam-o-Alaikum! Kababjees VocalDesk mein khush amdeed. Main aapki kya madad kar sakta hoon?",
        voice='polly.Aditi',
        language='hi-IN'
    )

    gather = response.gather(input='speech', action='/handle-call', language='ur-PK', timeout=3)
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.post("/handle-call")
async def handle_call(SpeechResult: str = Form(None)):
    response = VoiceResponse()

    if SpeechResult:
        print(f"Customer ne kaha: {SpeechResult}")
        answer = get_db_response(SpeechResult)

        response.say(answer, voice='polly.Aditi', language='hi-IN')

        response.gather(input='speech', action='/handle-call', language='ur-PK', timeout=3)
    else:
        response.say("Maaf kijiyega, mujhe aapki awaaz nahi aayi.")
        response.redirect('/voice')

    return HTMLResponse(content=str(response), media_type="application/xml")


@app.post("/voice")
async def voice_endpoint():
    response = VoiceResponse()

    response.say(
        "Assalam-o-alaikum Captain! VocalDesk mein khush amdeed. Main Kababjees ka AI assistant hoon.",
        voice='Polly.Aditi',
        language='hi-IN'
    )

    gather = Gather(input='speech', action='/handle-response', speechTimeout='auto')
    gather.say(
        "Main aapki kya madad kar sakta hoon? Aap menu ya order status ke baare mein puch sakte hain.",
        voice='Polly.Aditi',
        language='hi-IN'
    )
    response.append(gather)

    return Response(content=str(response), media_type="application/xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000 )

