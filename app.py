import os
import requests
import resend
from datetime import datetime, timezone
from twilio.rest import Client as TwilioClient
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

load_dotenv()

app = Flask(__name__)

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
RETELL_API_KEY = os.getenv("RETELL_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
CRAIG_PHONE = os.getenv("CRAIG_PHONE", "")


def send_email(subject, html_content):
    to_emails = [e.strip() for e in EMAIL_TO.split(",")]
    resend.Emails.send({
        "from": EMAIL_FROM,
        "to": to_emails,
        "subject": subject,
        "html": html_content,
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    event = data.get("event")
    if event != "call_analyzed":
        return jsonify({"status": "ignored"}), 200

    call = data.get("call", {})
    analysis = call.get("call_analysis", {})
    custom = analysis.get("custom_analysis_data", {})

    summary = custom.get("detailed_summary") or analysis.get("call_summary", "No summary available")
    sentiment = analysis.get("user_sentiment", "Unknown")
    support_type = custom.get("support_type", "general")
    successful = analysis.get("call_successful", False)
    lead_temperature = custom.get("lead_temperature", "Unknown")
    caller_name = custom.get("caller_name", "")
    caller_phone = custom.get("caller_phone", "")
    caller_email = custom.get("caller_email", "")
    property_address = custom.get("property_address", "")
    preferred_availability = custom.get("preferred_availability", "")
    from_number = call.get("from_number", "Unknown")
    duration_ms = call.get("duration_ms", 0)
    duration_sec = round(duration_ms / 1000)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build transcript section
    transcript_text = ""
    transcript = call.get("transcript_object", [])
    if transcript:
        lines = []
        for entry in transcript:
            role = entry.get("role", "")
            content = entry.get("content", "")
            speaker = "Emily" if role == "agent" else "Caller"
            lines.append(f"<p><strong>{speaker}:</strong> {content}</p>")
        transcript_text = "\n".join(lines)

    display_name = caller_name or from_number or "Unknown"
    subject = f"E&E Call Summary — {support_type} from {display_name}"

    html_content = f"""
    <h2>New Call Summary</h2>
    <p><strong>Summary:</strong> {summary}</p>
    <hr>
    <table style="border-collapse: collapse;">
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Name:</strong></td><td>{caller_name or "Not provided"}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Phone:</strong></td><td>{caller_phone or from_number}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Email:</strong></td><td>{caller_email or "Not provided"}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Address:</strong></td><td>{property_address or "Not provided"}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Preferred Availability:</strong></td><td>{preferred_availability or "Not provided"}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Type:</strong></td><td>{support_type}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Lead Temperature:</strong></td><td>{lead_temperature}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Sentiment:</strong></td><td>{sentiment}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Duration:</strong></td><td>{duration_sec} seconds</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Time:</strong></td><td>{timestamp}</td></tr>
    </table>
    """

    if transcript_text:
        html_content += f"""
    <hr>
    <h3>Full Transcript</h3>
    {transcript_text}
    """

    send_email(subject, html_content)

    # SMS alert to Craig for hot leads
    if lead_temperature == "Hot" and CRAIG_PHONE and TWILIO_ACCOUNT_SID:
        try:
            twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            sms_body = (
                f"🔥 Hot Lead — {display_name}\n"
                f"Phone: {caller_phone or from_number}\n"
                f"Service: {support_type}\n"
                f"Summary: {summary[:160]}"
            )
            twilio.messages.create(
                body=sms_body,
                from_=TWILIO_FROM_NUMBER,
                to=CRAIG_PHONE,
            )
        except Exception:
            pass  # Don't let SMS issues break email delivery

    # Send call data to Concord AI Dashboard
    if DASHBOARD_URL:
        try:
            requests.post(
                f"{DASHBOARD_URL}/api/call",
                json={
                    "agent_id": call.get("agent_id"),
                    "call_id": call.get("call_id"),
                    "from_number": from_number,
                    "duration_ms": duration_ms,
                    "call_summary": summary,
                    "user_sentiment": sentiment,
                    "support_type": support_type,
                    "call_successful": successful,
                },
                headers={"X-API-Key": DASHBOARD_API_KEY},
                timeout=5,
            )
        except Exception:
            pass  # Don't let dashboard issues break email delivery

    return jsonify({"status": "email_sent"}), 200


@app.route("/lookup-caller", methods=["POST"])
def lookup_caller():
    data = request.json or {}
    args = data.get("args", data)
    phone_number = args.get("phone_number", "")

    if not phone_number or not RETELL_API_KEY:
        return jsonify({"status": "new_caller", "message": "No previous calls found."})

    try:
        resp = requests.post(
            "https://api.retellai.com/v2/list-calls",
            headers={
                "Authorization": f"Bearer {RETELL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "filter_criteria": {"from_number": [phone_number]},
                "sort_order": "descending",
                "limit": 5,
            },
            timeout=5,
        )

        if resp.status_code != 200:
            return jsonify({"status": "new_caller", "message": "No previous calls found."})

        calls = resp.json()
        if not calls:
            return jsonify({"status": "new_caller", "message": "No previous calls found."})

        # Get the most recent call's analysis
        latest = calls[0]
        analysis = latest.get("call_analysis", {})
        custom = analysis.get("custom_analysis_data", {})
        caller_name = custom.get("caller_name", "")
        prev_summary = custom.get("detailed_summary") or analysis.get("call_summary", "")
        call_count = len(calls)

        if caller_name:
            return jsonify({
                "status": "returning_caller",
                "caller_name": caller_name,
                "call_count": call_count,
                "last_call_summary": prev_summary[:200],
                "message": f"This is a returning caller. Their name is {caller_name}. They have called {call_count} time(s) before. Their last call was about: {prev_summary[:200]}",
            })
        else:
            return jsonify({
                "status": "returning_caller",
                "call_count": call_count,
                "last_call_summary": prev_summary[:200],
                "message": f"This number has called {call_count} time(s) before. Last call was about: {prev_summary[:200]}",
            })

    except Exception:
        return jsonify({"status": "new_caller", "message": "No previous calls found."})


CRON_SECRET = os.getenv("CRON_SECRET", "")

FORWARD_ON_CODE = "**61*15795893235**15#"
FORWARD_OFF_CODE = "##002#"


def dial_page(title, code, description):
    encoded = code.replace("*", "%2A").replace("#", "%23")
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; text-align: center; padding: 40px 20px; background: #f9f9f9; }}
  .card {{ background: white; border-radius: 16px; padding: 32px; max-width: 400px; margin: 0 auto; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }}
  h1 {{ font-size: 22px; margin-bottom: 8px; }}
  .code {{ font-family: monospace; font-size: 24px; font-weight: bold; background: #f0f0f0; padding: 16px; border-radius: 8px; margin: 20px 0; letter-spacing: 2px; }}
  .btn {{ display: inline-block; background: #2563eb; color: white; padding: 18px 36px; font-size: 20px; text-decoration: none; border-radius: 12px; margin-top: 16px; }}
  .btn:active {{ background: #1d4ed8; }}
  .desc {{ color: #666; font-size: 14px; margin-top: 16px; }}
</style>
</head><body>
<div class="card">
  <h1>{title}</h1>
  <div class="code">{code}</div>
  <a href="tel:{encoded}" class="btn">📞 Tap to Dial</a>
  <p class="desc">{description}</p>
</div>
</body></html>"""


@app.route("/dial/forward-on")
def dial_forward_on():
    return dial_page(
        "Start Call Forwarding",
        FORWARD_ON_CODE,
        "This sends unanswered calls to Emily after 15 seconds.",
    )


@app.route("/dial/forward-off")
def dial_forward_off():
    return dial_page(
        "Stop Call Forwarding",
        FORWARD_OFF_CODE,
        "This stops forwarding so calls come straight to you.",
    )


@app.route("/cron/forward-on", methods=["POST"])
def forward_on():
    if CRON_SECRET and request.headers.get("X-Cron-Secret") != CRON_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    try:
        send_forward_on_email()
        return jsonify({"status": "sent", "type": "forward_on"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cron/forward-off", methods=["POST"])
def forward_off():
    if CRON_SECRET and request.headers.get("X-Cron-Secret") != CRON_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    try:
        send_forward_off_email()
        return jsonify({"status": "sent", "type": "forward_off"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "2.3"}), 200


# --- Scheduled call forwarding reminders (via email) ---

CRAIG_EMAIL = os.getenv("CRAIG_EMAIL", "info@eecontracting.ca")
MT = pytz.timezone("America/Edmonton")


def send_forward_on_email():
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [CRAIG_EMAIL],
            "subject": "Evening Reminder — Start Call Forwarding",
            "html": """
<h3>Hey Craig, time to start call forwarding for the evening.</h3>
<p>Tap the button below — it'll open a page where you can dial the code in one tap:</p>
<p><a href="https://ee-agent-production.up.railway.app/dial/forward-on" style="display: inline-block; background: #2563eb; color: white; padding: 16px 32px; font-size: 18px; text-decoration: none; border-radius: 8px;">📞 Start Call Forwarding</a></p>
<p style="color: #666; font-size: 13px;">This sends unanswered calls to Emily after 15 seconds.<br>You'll get another email at 7am to turn it off.</p>
<hr>
<p style="color: #888; font-size: 12px;"><em>Automated reminder from E&amp;E AI system.</em></p>
""",
        })
    except Exception:
        pass


def send_forward_off_email():
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [CRAIG_EMAIL],
            "subject": "Morning Reminder — Stop Call Forwarding",
            "html": """
<h3>Good morning Craig! Time to turn off call forwarding.</h3>
<p>Tap the button below — it'll open a page where you can dial the code in one tap:</p>
<p><a href="https://ee-agent-production.up.railway.app/dial/forward-off" style="display: inline-block; background: #2563eb; color: white; padding: 16px 32px; font-size: 18px; text-decoration: none; border-radius: 8px;">📞 Stop Call Forwarding</a></p>
<p style="color: #666; font-size: 13px;">This stops forwarding so calls come straight to you.</p>
<hr>
<p style="color: #888; font-size: 12px;"><em>Automated reminder from E&amp;E AI system.</em></p>
""",
        })
    except Exception:
        pass


scheduler = BackgroundScheduler()
scheduler.add_job(send_forward_on_email, CronTrigger(hour=18, minute=0, timezone=MT))
scheduler.add_job(send_forward_off_email, CronTrigger(hour=7, minute=0, timezone=MT))
scheduler.start()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
