"""Reusable Google Sheets client for Erick's projects."""
import json
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _credentials():
    """Load service account credentials from JSON env var (Railway) or file path (local)."""
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials/service-account.json")
    return service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)


def get_sheets():
    return build("sheets", "v4", credentials=_credentials())


def write_headers(sheet_id: str, headers: list[str]):
    sheets = get_sheets()
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [
            {"repeatCell": {
                "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }},
            {"updateSheetProperties": {
                "properties": {"sheetId": 0, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]},
    ).execute()


def append_row(sheet_id: str, row: list):
    sheets = get_sheets()
    sheets.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range="A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def read_headers(sheet_id: str) -> list:
    """Return the header row (row 1) as a list of strings. Empty list if the sheet is blank."""
    sheets = get_sheets()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range="1:1",
    ).execute()
    rows = result.get("values", [])
    return rows[0] if rows else []
