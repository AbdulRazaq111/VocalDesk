import requests

# Aapka Data
#account_sid = "AC7790b9f52f3604313f889c17dfc9497e"
auth_token = "0514121108d1f63b318225a5c6c35cfb"
to_number = "+923238292357"
from_number = "+18142921639"
ngrok_url = "https://unperused-cristi-superstylishly.ngrok-free.dev/voice"

url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"

payload = {
    "To": to_number,
    "From": from_number,
    "Url": ngrok_url
}

print("Call trigger ho rahi hai...")
response = requests.post(url, data=payload, auth=(account_sid, auth_token))

if response.status_code == 201:
    print("✅ Mubarak ho! Call trigger ho gayi. Phone check karein.")
else:
    print(f"❌ Error aaya hai: {response.status_code}")
    print(response.text)