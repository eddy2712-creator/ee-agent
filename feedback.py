"""Post-call SMS feedback survey: send a 1-5 rating request, capture replies, notify the team."""
import json
import os
import re
from datetime import datetime, timezone

SURVEY_BODY = (
    "Hi, this is E&E Contracting. Thanks for calling! "
    "How was your experience talking to Emily, our AI assistant? "
    "Reply 1-5 (1=poor, 5=great). You can add a short comment too. "
    "Reply STOP to opt out."
)

# In-memory dedup: phone -> iso timestamp of last survey sent.
# Resets on container restart — acceptable for v1.
_recent_sends: dict[str, str] = {}
DEDUP_HOURS = 24


def _feedback_log_path():
    return os.getenv("FEEDBACK_LOG_PATH", "/tmp/ee_feedback.jsonl")


def is_enabled():
    return os.getenv("FEEDBACK_SMS_ENABLED", "false").lower() in ("1", "true", "yes")


def _recently_sent(phone: str) -> bool:
    last = _recent_sends.get(phone)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    delta = datetime.now(timezone.utc) - last_dt
    return delta.total_seconds() < DEDUP_HOURS * 3600


def send_survey(twilio_client, from_number_twilio: str, to_phone: str, call_id: str = "") -> bool:
    """Send a one-shot survey SMS. Returns True if sent, False if skipped/failed."""
    if not (is_enabled() and twilio_client and from_number_twilio and to_phone):
        return False
    if _recently_sent(to_phone):
        return False
    try:
        twilio_client.messages.create(
            body=SURVEY_BODY,
            from_=from_number_twilio,
            to=to_phone,
        )
        _recent_sends[to_phone] = datetime.now(timezone.utc).isoformat()
        return True
    except Exception:
        return False


_RATING_RE = re.compile(r"\b([1-5])\b")


def parse_reply(body: str) -> dict:
    """Extract a 1-5 rating and a trimmed comment from an inbound SMS body."""
    text = (body or "").strip()
    rating = None
    match = _RATING_RE.search(text)
    if match:
        rating = int(match.group(1))
    comment = _RATING_RE.sub("", text, count=1).strip(" .-,:") if rating else text
    return {"rating": rating, "comment": comment, "raw": text}


def record_reply(from_number: str, body: str) -> dict:
    """Append a feedback record to the JSONL log and return it. Best-effort — never raises."""
    parsed = parse_reply(body)
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "from_number": from_number,
        "rating": parsed["rating"],
        "comment": parsed["comment"],
        "raw": parsed["raw"],
    }
    try:
        path = _feedback_log_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass
    return record


def format_email(record: dict) -> tuple[str, str]:
    """Build a (subject, html) tuple for the team notification email."""
    rating = record.get("rating")
    star_row = ("⭐" * rating) if rating else "(no numeric rating)"
    subject = (
        f"Emily Feedback — {rating}/5 from {record.get('from_number', 'Unknown')}"
        if rating
        else f"Emily Feedback (text reply) from {record.get('from_number', 'Unknown')}"
    )
    html = f"""
    <h2>New customer feedback for Emily</h2>
    <table style="border-collapse: collapse;">
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Rating:</strong></td><td>{star_row} {f'({rating}/5)' if rating else ''}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>From:</strong></td><td>{record.get('from_number', '')}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Comment:</strong></td><td>{record.get('comment') or '(none)'}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Time:</strong></td><td>{record.get('timestamp', '')}</td></tr>
    </table>
    <p style="color: #888; font-size: 12px;">Raw reply: {record.get('raw', '')}</p>
    """
    return subject, html
