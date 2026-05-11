"""E&E call log: append one row per Retell call to the Google Sheet."""
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from sheets_client import append_row, read_headers, write_headers

load_dotenv()

EE_SHEET_ID = os.getenv("EE_SHEET_ID", "")

HEADERS = [
    "Timestamp", "Caller Name", "Summary", "Phone", "Email", "Address",
    "Service Type", "Lead Temperature", "Sentiment", "Duration (sec)",
    "Successful", "From Number", "Call ID", "Agent ID",
]


def _values_for(call: dict) -> dict:
    """Map header names -> values pulled from a Retell webhook `call` dict."""
    analysis = call.get("call_analysis", {})
    custom = analysis.get("custom_analysis_data", {})
    return {
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "Caller Name": custom.get("caller_name", ""),
        "Summary": custom.get("detailed_summary") or analysis.get("call_summary", ""),
        "Phone": custom.get("caller_phone", ""),
        "Email": custom.get("caller_email", ""),
        "Address": custom.get("property_address", ""),
        "Service Type": custom.get("support_type", ""),
        "Lead Temperature": custom.get("lead_temperature", ""),
        "Sentiment": analysis.get("user_sentiment", ""),
        "Duration (sec)": round(call.get("duration_ms", 0) / 1000),
        "Successful": "Yes" if analysis.get("call_successful") else "No",
        "From Number": call.get("from_number", ""),
        "Call ID": call.get("call_id", ""),
        "Agent ID": call.get("agent_id", ""),
    }


def log_call(call: dict):
    """Append one Retell call to the E&E sheet. `call` is the Retell webhook payload's `call` dict."""
    if not EE_SHEET_ID:
        return

    values = _values_for(call)

    # Read the sheet's current header order so a column reshuffle is picked up automatically.
    headers = read_headers(EE_SHEET_ID)
    if not headers:
        # Sheet is empty — initialize with the default order.
        headers = HEADERS
        write_headers(EE_SHEET_ID, headers)

    row = [values.get(h, "") for h in headers]
    append_row(EE_SHEET_ID, row)


def setup_sheet():
    """One-time: write headers + formatting."""
    write_headers(EE_SHEET_ID, HEADERS)
    print(f"✓ Headers written to sheet {EE_SHEET_ID}")


if __name__ == "__main__":
    print("Setting up E&E sheet headers...")
    setup_sheet()

    print("\nAppending a test row...")
    fake_call = {
        "call_id": "test_call_001",
        "agent_id": "test_agent",
        "from_number": "+15875551234",
        "duration_ms": 92000,
        "call_analysis": {
            "user_sentiment": "Positive",
            "call_summary": "Test call — please ignore.",
            "call_successful": True,
            "custom_analysis_data": {
                "caller_name": "Test Caller",
                "caller_phone": "+15875551234",
                "caller_email": "test@example.com",
                "property_address": "123 Fake St, Calgary AB",
                "support_type": "gutter cleaning",
                "lead_temperature": "Warm",
                "detailed_summary": "Caller asked about gutter cleaning. Test row from setup.",
            },
        },
    }
    log_call(fake_call)
    print("✓ Test row appended. Check the sheet!")
