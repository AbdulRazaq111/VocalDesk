import requests

# Aapka Data
account_sid = "AC407e35ce82011cefc39a8b5dee63595f"
auth_token = "2a77f3a81b52a29bd9e07b4ee7faf9bf"
to_number = "+923238292357"
from_number = "+17622167199"
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