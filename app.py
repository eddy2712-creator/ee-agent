import base64
import os
import requests
import resend
from datetime import datetime, timezone
from twilio.rest import Client as TwilioClient
from flask import Flask, request, jsonify
from dotenv import load_dotenv

try:
    from ee_call_log import log_call
except Exception:
    log_call = None

try:
    import jobber_client
except Exception:
    jobber_client = None

try:
    import spam_check
except Exception:
    spam_check = None

try:
    import feedback
except Exception:
    feedback = None

load_dotenv()

app = Flask(__name__)

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_CC = os.getenv("EMAIL_CC", "")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
RETELL_API_KEY = os.getenv("RETELL_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
CRAIG_PHONE = os.getenv("CRAIG_PHONE", "")


def send_email(subject, html_content, attachments=None):
    to_emails = [e.strip() for e in EMAIL_TO.split(",") if e.strip()]
    payload = {
        "from": EMAIL_FROM,
        "to": to_emails,
        "subject": subject,
        "html": html_content,
    }
    if EMAIL_CC:
        payload["cc"] = [e.strip() for e in EMAIL_CC.split(",") if e.strip()]
    if attachments:
        payload["attachments"] = attachments
    resend.Emails.send(payload)


def fetch_recording_attachment(recording_url):
    """Download a Retell call recording and return it as a Resend attachment dict.
    Returns None on any failure so the summary email still goes out."""
    if not recording_url:
        return None
    try:
        resp = requests.get(recording_url, timeout=30)
        resp.raise_for_status()
        return {
            "filename": "call-recording.mp3",
            "content": base64.b64encode(resp.content).decode(),
        }
    except Exception:
        return None


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

    # Spam / phone reputation lookup (fail-soft).
    spam_result = spam_check.check_number(from_number) if spam_check else {"checked": False, "label": "Not checked", "risk_level": "unknown"}

    display_name = caller_name or from_number or "Unknown"
    subject = f"E&E Call Summary — {support_type} from {display_name}"
    if spam_result.get("risk_level") == "high":
        subject = "[Likely Spam] " + subject

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

    if spam_result.get("checked"):
        risk_colors = {"high": "#dc2626", "medium": "#d97706", "low": "#65a30d", "clean": "#16a34a", "unknown": "#666"}
        color = risk_colors.get(spam_result.get("risk_level", "unknown"), "#666")
        score = spam_result.get("fraud_score")
        score_text = f"{score}/100" if score is not None else "n/a"
        flags = []
        if spam_result.get("recent_abuse"):
            flags.append("recent abuse")
        if spam_result.get("risky"):
            flags.append("risky")
        if spam_result.get("voip"):
            flags.append("VOIP")
        if spam_result.get("prepaid"):
            flags.append("prepaid")
        flags_text = ", ".join(flags) if flags else "none"
        carrier = spam_result.get("carrier") or spam_result.get("line_type") or "Unknown"
        html_content += f"""
    <hr>
    <h3>Phone Reputation</h3>
    <table style="border-collapse: collapse;">
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Risk:</strong></td><td><span style="color: {color}; font-weight: bold;">{spam_result.get('label', 'Unknown')}</span></td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Spam Score:</strong></td><td>{score_text}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Flags:</strong></td><td>{flags_text}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Carrier / Line:</strong></td><td>{carrier}</td></tr>
    </table>
    """

    if transcript_text:
        html_content += f"""
    <hr>
    <h3>Full Transcript</h3>
    {transcript_text}
    """

    recording_attachment = fetch_recording_attachment(call.get("recording_url"))
    attachments = [recording_attachment] if recording_attachment else None
    send_email(subject, html_content, attachments=attachments)

    # Append call to Google Sheet
    if log_call is not None:
        try:
            log_call(call, spam_result)
        except Exception:
            pass  # Don't let Sheets issues break email delivery

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

    # Push lead into Jobber (find-or-create client + attach call summary as a note).
    # Skip for instant hang-ups with no captured info, or for likely-spam numbers.
    skip_jobber = False
    if spam_check is not None:
        skip_jobber = spam_check.should_skip_jobber(
            spam_result, duration_sec, caller_name, caller_email, property_address
        )

    if jobber_client is not None and jobber_client.is_configured() and not skip_jobber:
        try:
            note_body = (
                f"Call summary ({timestamp}):\n"
                f"{summary}\n\n"
                f"Service type: {support_type}\n"
                f"Lead temperature: {lead_temperature}\n"
                f"Preferred availability: {preferred_availability or 'not provided'}"
            )
            jobber_client.upsert_lead(
                name=caller_name,
                phone=caller_phone or from_number,
                email=caller_email,
                address=property_address,
                call_summary=note_body,
            )
        except Exception:
            pass  # Don't let Jobber issues break email delivery

    # Post-call feedback survey: text the caller asking them to rate Emily.
    # Gate: must be enabled, real conversation (>= 30s), not flagged spam, not the business owner's own number.
    if (
        feedback is not None
        and feedback.is_enabled()
        and TWILIO_ACCOUNT_SID
        and TWILIO_FROM_NUMBER
        and duration_sec >= 30
        and spam_result.get("risk_level") not in ("high",)
        and not spam_result.get("recent_abuse")
        and from_number
        and from_number != CRAIG_PHONE
        and from_number != TWILIO_FROM_NUMBER
    ):
        try:
            twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            feedback.send_survey(
                twilio_client=twilio,
                from_number_twilio=TWILIO_FROM_NUMBER,
                to_phone=from_number,
                call_id=call.get("call_id", ""),
            )
        except Exception:
            pass  # Survey send is best-effort

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


@app.route("/sms-feedback", methods=["POST"])
def sms_feedback():
    """Twilio inbound SMS webhook. Captures customer rating replies and emails the team."""
    from_number = request.values.get("From", "")
    body = request.values.get("Body", "")

    twiml_empty = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

    if feedback is None or not body.strip():
        return twiml_empty, 200, {"Content-Type": "text/xml"}

    # Honor STOP / opt-out — Twilio handles unsubscribe automatically; we just don't log it.
    if body.strip().upper() in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"):
        return twiml_empty, 200, {"Content-Type": "text/xml"}

    record = feedback.record_reply(from_number, body)

    try:
        subject, html = feedback.format_email(record)
        send_email(subject, html)
    except Exception:
        pass  # Don't break Twilio webhook on email failure

    # Optional thank-you reply via TwiML (no extra Twilio API call needed).
    rating = record.get("rating")
    if rating:
        thanks = f"Thanks for the {rating}/5 rating — we appreciate the feedback!"
    else:
        thanks = "Thanks for the feedback — we appreciate it!"
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{thanks}</Message></Response>'
    return twiml, 200, {"Content-Type": "text/xml"}


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


@app.route("/jobber/connect", methods=["GET"])
def jobber_connect():
    """Sara/Craig clicks this once to authorize the app. Redirects to Jobber."""
    if jobber_client is None:
        return "Jobber client not loaded", 500
    return ("", 302, {"Location": jobber_client.authorize_url()})


@app.route("/jobber/callback", methods=["GET"])
def jobber_callback():
    """Jobber sends the user back here with ?code=... — we exchange it for tokens."""
    if jobber_client is None:
        return "Jobber client not loaded", 500

    error = request.args.get("error")
    if error:
        return f"<h2>Authorization failed</h2><p>{error}</p>", 400

    code = request.args.get("code", "")
    if not code:
        return "<h2>Missing authorization code</h2>", 400

    try:
        tokens = jobber_client.exchange_code_for_tokens(code)
    except Exception as e:
        return f"<h2>Token exchange failed</h2><pre>{e}</pre>", 500

    refresh_token = tokens.get("refresh_token", "")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Jobber Connected</title>
<style>
  body {{ font-family: -apple-system, sans-serif; padding: 40px 20px; background: #f9f9f9; }}
  .card {{ background: white; border-radius: 16px; padding: 32px; max-width: 700px; margin: 0 auto; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }}
  h1 {{ color: #16a34a; }}
  code {{ background: #f0f0f0; padding: 8px 12px; border-radius: 6px; font-size: 14px; word-break: break-all; display: block; margin: 12px 0; }}
  .step {{ margin: 20px 0; padding: 16px; background: #f8fafc; border-left: 4px solid #2563eb; border-radius: 4px; }}
</style></head><body>
<div class="card">
  <h1>✅ Jobber connected</h1>
  <p>Authorization successful. Copy the refresh token below into Railway as the <strong>JOBBER_REFRESH_TOKEN</strong> environment variable, then redeploy.</p>
  <div class="step">
    <strong>Refresh token (copy this):</strong>
    <code>{refresh_token}</code>
  </div>
  <p style="color: #666; font-size: 14px;">After you paste it into Railway and the service restarts, the agent will start pushing new callers into Jobber automatically.</p>
</div>
</body></html>"""


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "2.9"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
