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

print("STEP 1")
load_dotenv()

app = FastAPI()

# Database Tables Create Karna (Server start hote hi tables ban jayenge)
models.Base.metadata.create_all(bind=engine)

# Groq Client Setup
client_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
client_eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    return "Verification Failed"

@app.post("/webhook")
async def handle_msg(request: Request):
    data = await request.json()
    db = next(get_db()) # Database session start
    
    try:
        val = data['entry'][0]['changes'][0]['value']
        if 'messages' in val:
            user_phone = val['messages'][0]['from']
            user_text = val['messages'][0]['text']['body']
            print(f"Naya message: {user_text} from {user_phone}")

            

            # 1. User check ya create karna (PostgreSQL)
            db_user = db.query(models.User).filter(models.User.phone_number == user_phone).first()
            if not db_user:
                db_user = models.User(phone_number=user_phone)
                db.add(db_user)
                db.commit()
                db.refresh(db_user)
            
            greetings = ["hi", "hello", "hey", "assalam o alaikum", "aoa", "start"]
            if user_text.lower() in greetings:
                #db.query(models.Message).filter(models.Message.user_id == db_user.id).delete()
                #db.commit()
                welcome_reply = "Asalam-o-Alaikum! Kababjees mein khush amdeed. Main apka order lene ke liye hazir hon. Aaj aap kya khana pasand karenge?"
                send_text(user_phone, welcome_reply)
                return {"status": "success"}


            # --- SMART KNOWLEDGE RETRIEVAL (Kababjees Menu) ---
            # Hum sirf wo rows uthayenge jo user ke sawal se match karti hon
            search_words = user_text.lower().split()
            all_info = db.query(models.BusinessKnowledge).all()
            
            relevant_context = ""
            for item in all_info:
                # Agar user 'burger' bole toh sirf burger wala data context mein jaye
                if any(word in item.answer.lower() or word in item.question.lower() for word in search_words):
                    relevant_context += f"\nRelevant Info: {item.answer}"

            # Agar koi match na mile toh default small context
            if not relevant_context:
                relevant_context = "Kababjees Menu includes Fried Chicken, Burgers, Sandwiches, and Exclusive Deals."
            # -------------------------------------------

            # 2. Memory Context (Pichli 10 baatein taake order yaad rahe)
            history = db.query(models.Conversation).filter(
                models.Conversation.user_id == db_user.id
            ).order_by(models.Conversation.timestamp.desc()).limit(10).all()
            
            # --- THE STRICT SALESMAN LOGIC ---
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
                f"\n7. Tum Kababjees ke salesman ho. Jawab hamesha 1-2 lines mein do. Agar koi price puche toh sirf price aur item ka naam batao, lambay paragraphs mat likho. Short, Professional aur To-the-point baat karo."
            )
            
            messages = [{"role": "system", "content": system_content}]
            for h in reversed(history):
                messages.append({"role": "user", "content": h.user_message})
                messages.append({"role": "assistant", "content": h.ai_response})
            
            messages.append({"role": "user", "content": user_text})

            # 3. Groq AI se Jawab lena
            completion = client_groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.2 # Lower for even more accuracy
            )
            ai_reply = completion.choices[0].message.content
            print(f"AI Response: {ai_reply}")

            # 4. Conversation Database mein Save karna
            new_conv = models.Conversation(
                user_id=db_user.id,
                message_type="text",
                user_message=user_text,
                ai_response=ai_reply
            )
            db.add(new_conv)
            db.commit()

            # 5. WhatsApp Text Reply
            send_text(user_phone, ai_reply)

            # 6. ElevenLabs Voice Note (Voice on karne ke liye sirf uncomment karein)
            # if ai_reply:
            #     audio_file = generate_voice_eleven(ai_reply)
            #     if audio_file:
            #         send_audio(user_phone, audio_file)
            
    except Exception as e:
        print(f"Error in handle_msg: {e}")
    finally:
        db.close() # Connection band karna zaroori hai
        
    return {"status": "ok"}

# --- Helper Functions (Remaining untouched) ---

def send_text(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    response = requests.post(url, headers=headers, json=payload)
    print(f"WhatsApp Status: {response.status_code}")

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
            f"\n7. Tum Kababjees ke salesman ho. Jawab hamesha 1-2 lines mein do. Agar koi price puche toh sirf price aur item ka naam batao, lambay paragraphs mat likho. Short, Professional aur To-the-point baat karo."
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
    response.say("Assalam-o-Alaikum! Kababjees VocalDesk mein khush amdeed. Main aapki kya madad kar sakta hoon?", 
                 voice='polly.Aditi', language='hi-IN')
    
    # User ki baat sun'ne ke liye 'Gather'
    gather = response.gather(input='speech', action='/handle-call', language='ur-PK', timeout=3)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.post("/handle-call")
async def handle_call(SpeechResult: str = Form(None)):
    response = VoiceResponse()
    
    if SpeechResult:
        print(f"Customer ne kaha: {SpeechResult}")
        # Yahan aapka database wala function call hoga
        answer = get_db_response(SpeechResult) 
        
        response.say(answer, voice='polly.Aditi', language='hi-IN')
        
        # Agli baat sun'ne ke liye
        response.gather(input='speech', action='/handle-call', language='ur-PK', timeout=3)
    else:
        response.say("Maaf kijiyega, mujhe aapki awaaz nahi aayi.")
        response.redirect('/voice')
        
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.post("/voice")
async def voice_endpoint():
    response = VoiceResponse()
    
    # 1. Welcome Message
    response.say("Assalam-o-alaikum Captain! VocalDesk mein khush amdeed. Main Kababjees ka AI assistant hoon.", voice='Polly.Aditi', language='hi-IN')
    
    # 2. User ki baat sunne ke liye (Gather)
    # Is se call band nahi hogi, ye 5 seconds tak aapka intezar karega
    gather = Gather(input='speech', action='/handle-response', speechTimeout='auto')
    gather.say("Main aapki kya madad kar sakta hoon? Aap menu ya order status ke baare mein puch sakte hain.", voice='Polly.Aditi', language='hi-IN')
    response.append(gather)

    return Response(content=str(response), media_type="application/xml")
# Windows Multi-processing fix
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)