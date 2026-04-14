import os
import requests
import resend
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")


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

    summary = analysis.get("call_summary", "No summary available")
    sentiment = analysis.get("user_sentiment", "Unknown")
    support_type = custom.get("support_type", "general")
    successful = analysis.get("call_successful", False)
    from_number = call.get("from_number", "Unknown")
    duration_ms = call.get("duration_ms", 0)
    duration_sec = round(duration_ms / 1000)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    subject = f"E&E Call Summary — {support_type} from {from_number}"

    html_content = f"""
    <h2>New Call Summary</h2>
    <p><strong>Summary:</strong> {summary}</p>
    <hr>
    <table style="border-collapse: collapse;">
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Caller:</strong></td><td>{from_number}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Type:</strong></td><td>{support_type}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Sentiment:</strong></td><td>{sentiment}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Duration:</strong></td><td>{duration_sec} seconds</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Successful:</strong></td><td>{"Yes" if successful else "No"}</td></tr>
        <tr><td style="padding: 4px 12px 4px 0;"><strong>Time:</strong></td><td>{timestamp}</td></tr>
    </table>
    """

    send_email(subject, html_content)

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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
