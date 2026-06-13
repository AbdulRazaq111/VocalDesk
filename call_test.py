import requests
import time

# Aapka Data — values .env se ya yahan directly
account_sid = "AC407e35ce82011cefc39a8b5dee63595f"   # Twilio Console se naya copy karo (purana leak ho gaya)
auth_token = "f9e11da5007f409face938ca3efd940a"     # Twilio Console se naya regenerate karo
to_number = "+923238292357"
from_number = "+17622167199"
render_url = "https://vocaldesk-backend.onrender.com/voice"  # ✅ Render URL (ngrok nahi)

# ✅ Step 1: Pehle Render server ko wake up karo (free tier cold start fix)
print("Server wake up kar raha hai... (10 second wait)")
try:
    requests.get("https://vocaldesk-backend.onrender.com", timeout=15)
except:
    pass
time.sleep(10)  # Server warm hone ka wait

# ✅ Step 2: Call trigger karo
url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"

payload = {
    "To": to_number,
    "From": from_number,
    "Url": render_url
}

print("Call trigger ho rahi hai...")
response = requests.post(url, data=payload, auth=(account_sid, auth_token))

if response.status_code == 201:
    print("✅ Mubarak ho! Call trigger ho gayi. Phone check karein.")
else:
    print(f"❌ Error aaya hai: {response.status_code}")
    print(response.text)