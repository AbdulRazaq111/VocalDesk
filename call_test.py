import requests
import time
from dotenv import load_dotenv
import os

load_dotenv()

account_sid = os.getenv("account_sid")
auth_token = os.getenv("auth_token")
to_number = os.getenv("to_number")
from_number = os.getenv("from_number")
render_url = "https://vocaldesk-backend.onrender.com/voice"

print("Server wake up kar raha hai... (10 second wait)")
try:
    requests.get("https://vocaldesk-backend.onrender.com", timeout=15)
except:
    pass
time.sleep(10)

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