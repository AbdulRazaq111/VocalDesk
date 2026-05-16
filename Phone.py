from twilio.rest import Client

# Naye account ki details yahan dalein
account_sid = 'ACc8df76271216da78b8cdcd15c5f7e38f'
auth_token = 'fe63d52fb0ae6fd5cc2c87da9224e63e'
client = Client(account_sid, auth_token)

call = client.calls.create(
    # Jo Ngrok link aapne banaya hai /voice ke sath
    url='https://unperused-cristi-superstylishly.ngrok-free.dev/voice',
    to='+923402388393', # Aapka verified number
    from_='+17179529931' # Aapka naya Twilio number
)

print(f"Call trigger ho gayi! SID: {call.sid}")