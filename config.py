import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
SPREADSHEET_ID: str = os.environ["SPREADSHEET_ID"]
CREDENTIALS_FILE: str = os.getenv("CREDENTIALS_FILE", "credentials.json")
DB_FILE: str = os.getenv("DB_FILE", "iq_hh_bot.db")

_raw_admins = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = (
    [int(x.strip()) for x in _raw_admins.split(",") if x.strip().isdigit()]
    if _raw_admins.strip()
    else []
)
