from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "credentials.json",
    scopes=[
        "https://www.googleapis.com/auth/gmail.readonly",
"https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar"
    ]
)
creds = flow.run_local_server(port=0)
with open("token.json", "w") as f:
    f.write(creds.to_json())
print("Done — token.json updated with Calendar scope")
