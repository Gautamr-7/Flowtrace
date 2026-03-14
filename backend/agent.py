import sys, os, json, sqlite3
from datetime import datetime
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool

load_dotenv()

# ── Gemini with retry (fixes 503 overload) ──────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def make_llm():
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.1,
        convert_system_message_to_human=True,
    )

llm = make_llm()

# ── SQLite team memory (zero downloads, works offline) ──────────────────────
def init_db():
    conn = sqlite3.connect("flowtrace_memory.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, tool TEXT, input TEXT, output TEXT
    )""")
    conn.commit()
    conn.close()

def save_action(tool_name, input_data, output):
    conn = sqlite3.connect("flowtrace_memory.db")
    conn.execute("INSERT INTO actions VALUES (NULL,?,?,?,?)",
                 (datetime.now().isoformat(), tool_name, str(input_data), str(output)))
    conn.commit()
    conn.close()

init_db()

# ── Tool 1: Gmail ────────────────────────────────────────────────────────────
@tool
def gmail_read_leads(query: str = "is:unread") -> str:
    """Read emails from Gmail matching a search query. Returns sender and subject."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(
            "token.json", ["https://www.googleapis.com/auth/gmail.readonly"])
        service = build("gmail", "v1", credentials=creds)
        results = service.users().messages().list(userId="me", q=query, maxResults=5).execute()
        messages = results.get("messages", [])
        leads = []
        for msg in messages:
            detail = service.users().messages().get(userId="me", id=msg["id"]).execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            leads.append({"from": headers.get("From",""), "subject": headers.get("Subject","")})
        result = json.dumps(leads)
        save_action("gmail_read_leads", query, result)
        return result
    except Exception as e:
        return f"Gmail error: {e}"
@tool
def gmail_send_email(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        import base64
        from email.mime.text import MIMEText

        creds = Credentials.from_authorized_user_file(
    "token.json",
    [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send"
    ]
)

        service = build("gmail", "v1", credentials=creds)

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        output = f"Sent email to {to}"
        save_action("gmail_send_email", to, output)
        return output

    except Exception as e:
        return f"Gmail send error: {e}"
# ── Tool 2: HubSpot ──────────────────────────────────────────────────────────
@tool
def hubspot_create_contact(email: str, first_name: str, last_name: str = "") -> str:
    """Create a new contact in HubSpot CRM with email and name."""
    try:
        import hubspot
        from hubspot.crm.contacts import SimplePublicObjectInputForCreate
        client = hubspot.Client.create(access_token=os.getenv("HUBSPOT_API_KEY"))
        props = {"email": email, "firstname": first_name, "lastname": last_name}
        contact = SimplePublicObjectInputForCreate(properties=props)
        result = client.crm.contacts.basic_api.create(
            simple_public_object_input_for_create=contact)
        output = f"Created HubSpot contact: {email} (id={result.id})"
        save_action("hubspot_create_contact", email, output)
        return output
    except Exception as e:
        return f"HubSpot error: {e}"
@tool
def hubspot_get_new_leads(limit: int = 5) -> str:
    """Fetch recently created leads from HubSpot."""
    try:
        import hubspot
        client = hubspot.Client.create(access_token=os.getenv("HUBSPOT_API_KEY"))

        contacts = client.crm.contacts.basic_api.get_page(limit=limit)

        leads = []
        for c in contacts.results:
            props = c.properties
            leads.append({
                "name": f"{props.get('firstname','')} {props.get('lastname','')}".strip(),
                "email": props.get("email","")
            })

        result = json.dumps(leads)
        save_action("hubspot_get_new_leads", "", result)
        return result

    except Exception as e:
        return f"HubSpot read error: {e}"
# ── Tool 3: Google Sheets ────────────────────────────────────────────────────
@tool
def sheets_append_row(name: str, email: str, source: str = "Gmail", status: str = "New") -> str:
    """Append a lead row to the Google Sheet tracker."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(
            os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
            scopes=["https://spreadsheets.google.com/feeds",
                    "https://www.googleapis.com/auth/drive"])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(os.getenv("SHEETS_SPREADSHEET_ID"))
        sh.sheet1.append_row([name, email, source, status,
                               datetime.now().strftime("%Y-%m-%d %H:%M")])
        output = f"Appended row: {name} | {email}"
        save_action("sheets_append_row", f"{name},{email}", output)
        return output
    except Exception as e:
        return f"Sheets error: {e}"

# ── Tool 4: Notion ───────────────────────────────────────────────────────────
@tool
def notion_create_task(title: str, due_date: str = "") -> str:
    """Create a follow-up task in the Notion database."""
    try:
        from notion_client import Client
        notion = Client(auth=os.getenv("NOTION_TOKEN"))
        props = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Status": {"select": {"name": "Not started"}},
        }
        if due_date:
            props["Due date"] = {"date": {"start": due_date}}
        page = notion.pages.create(
            parent={"database_id": os.getenv("NOTION_DATABASE_ID")},
            properties=props)
        output = f"Created Notion task: '{title}' (id={page['id'][:8]})"
        save_action("notion_create_task", title, output)
        return output
    except Exception as e:
        return f"Notion error: {e}"

# ── Tool 5: Slack ────────────────────────────────────────────────────────────
@tool
def slack_notify(channel: str, message: str) -> str:
    """Send a message to a Slack channel."""
    try:
        from slack_sdk import WebClient
        client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
        client.chat_postMessage(channel=f"#{channel}", text=message)
        output = f"Posted to #{channel}"
        save_action("slack_notify", channel, output)
        return output
    except Exception as e:
        return f"Slack error: {e}"
# ── Tool 6: Google Calendar ──────────────────────────────────────────────────
@tool
def calendar_create_event(title: str, attendee_email: str, date: str, time: str = "10:00") -> str:
    """Create a Google Calendar event for a follow-up meeting. Date format: YYYY-MM-DD, time format: HH:MM"""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        from datetime import datetime, timedelta
        creds = Credentials.from_authorized_user_file(
            "token.json", [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/calendar"
            ])
        service = build("calendar", "v3", credentials=creds)
        start = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        end = start + timedelta(hours=1)
        event = {
            "summary": title,
            "attendees": [{"email": attendee_email}],
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
            "end":   {"dateTime": end.isoformat(),   "timeZone": "Asia/Kolkata"},
        }
        result = service.events().insert(calendarId="primary", body=event,
                                         sendUpdates="all").execute()
        output = f"Created calendar event: '{title}' on {date} at {time} with {attendee_email}"
        save_action("calendar_create_event", attendee_email, output)
        return output
    except Exception as e:
        return f"Calendar error: {e}"

# ── Agent ────────────────────────────────────────────────────────────────────
tools = [
    gmail_read_leads,
    gmail_send_email,
    hubspot_get_new_leads,
    hubspot_create_contact,
    sheets_append_row,
    notion_create_task,
    slack_notify,
    calendar_create_event
]

agent_executor = create_react_agent(llm, tools)