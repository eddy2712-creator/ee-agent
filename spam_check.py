"""IPQualityScore phone reputation lookup. Fail-soft: returns a safe 'unknown' result if not configured or the API fails."""
import os
import requests

IPQS_BASE = "https://www.ipqualityscore.com/api/json/phone"

UNKNOWN = {
    "checked": False,
    "fraud_score": None,
    "risky": False,
    "recent_abuse": False,
    "voip": False,
    "prepaid": False,
    "active": None,
    "line_type": "",
    "carrier": "",
    "label": "Not checked",
    "risk_level": "unknown",
}


def _api_key():
    return os.getenv("IPQS_API_KEY", "")


def is_configured():
    return bool(_api_key())


def _risk_level(score, recent_abuse, risky):
    if score is None:
        return "unknown"
    if recent_abuse or score >= 85:
        return "high"
    if risky or score >= 60:
        return "medium"
    if score >= 30:
        return "low"
    return "clean"


def check_number(phone_number, country="CA"):
    """Look up a phone number's spam/fraud reputation. Always returns a dict — never raises."""
    if not phone_number or not _api_key():
        return dict(UNKNOWN)

    try:
        resp = requests.get(
            f"{IPQS_BASE}/{_api_key()}/{phone_number}",
            params={"country[]": country, "strictness": 1},
            timeout=5,
        )
        if resp.status_code != 200:
            return dict(UNKNOWN)
        data = resp.json()
        if not data.get("success", False):
            return dict(UNKNOWN)

        score = data.get("fraud_score")
        recent_abuse = bool(data.get("recent_abuse", False))
        risky = bool(data.get("risky", False))
        risk = _risk_level(score, recent_abuse, risky)

        labels = {
            "high": "High risk — likely spam",
            "medium": "Suspicious",
            "low": "Slight risk",
            "clean": "Clean",
            "unknown": "Not checked",
        }

        return {
            "checked": True,
            "fraud_score": score,
            "risky": risky,
            "recent_abuse": recent_abuse,
            "voip": bool(data.get("VOIP", False)),
            "prepaid": bool(data.get("prepaid", False)),
            "active": data.get("active"),
            "line_type": data.get("line_type", "") or "",
            "carrier": data.get("carrier", "") or "",
            "label": labels[risk],
            "risk_level": risk,
        }
    except Exception:
        return dict(UNKNOWN)


def should_skip_jobber(spam_result, duration_sec, caller_name, caller_email, property_address, threshold=85):
    """True if we should NOT push this caller into Jobber.

    Skip if:
      - High spam (fraud_score >= threshold), OR
      - Recent_abuse flag set, OR
      - Short call (<15s) with no useful intake info captured.
    """
    if spam_result.get("recent_abuse"):
        return True
    score = spam_result.get("fraud_score")
    if score is not None and score >= threshold:
        return True
    if duration_sec < 15 and not (caller_name or caller_email or property_address):
        return True
    return False
