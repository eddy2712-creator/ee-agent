"""
Automated test batch for E&E sandbox agent.
Runs simulated calls via Retell API and collects results.
Tests: normal intake, gutter cleaning, emergency, general inquiry, returning caller.
"""
import requests
import json
import time
import sys

RETELL_API_KEY = "key_c465c9bc6663d542aa18d21a56ca"
SANDBOX_AGENT_ID = "agent_2ebb97e48923e79166ee081591"
API_BASE = "https://api.retellai.com"

HEADERS = {
    "Authorization": f"Bearer {RETELL_API_KEY}",
    "Content-Type": "application/json",
}

# Test scenarios — each has a name, simulated caller prompt, and what to check
TEST_SCENARIOS = [
    # --- Normal intake (2 tests) ---
    {
        "name": "Normal intake — full flow",
        "prompt": "You are Mike Johnson calling about getting new gutters installed on your two-story home at 123 Thickwood Drive, postal code T9H 4A1. Your phone is 780-555-1234, email mike.johnson@email.com. You're hoping to get it done in the next month. You're available weekday mornings. You don't need to be home, but there's a dog in the backyard that needs to be secured. Be friendly and cooperative.",
    },
    {
        "name": "Normal intake — caller gives info upfront (anti-repetition)",
        "prompt": "You are Jennifer Adams. Say immediately: 'Hi, my name is Jennifer Adams, I live at 300 Thickwood Drive T9H 3B2, it's a single-story home, I need new gutters installed because my old ones are falling off, my phone is 780-555-1111, email jenny@email.com, I'm available mornings, and I'd like it done within the next month.' Then just confirm anything Emily asks. If Emily re-asks something you already said, say 'I already told you that.'",
    },

    # --- Gutter cleaning (3 tests — key feature) ---
    {
        "name": "Gutter cleaning — should be hot lead",
        "prompt": "You are Lisa Martin calling about gutter cleaning. Your gutters are full of leaves and need to be cleaned out. Address: 200 Parsons Creek Drive, T9H 3X1. Single story home. Phone: 780-555-2222. Email: lisa.m@email.com. You want it done as soon as possible. Available any day.",
    },
    {
        "name": "Gutter cleaning — brief caller",
        "prompt": "You are Bob Smith. Say: 'Yeah I need my gutters cleaned, 150 Thickwood Boulevard, T9H 2R4. Name's Bob Smith, 780-555-3333.' Be brief and to the point. Give email bob@email.com if asked. Single story. Available weekends.",
    },
    {
        "name": "Gutter cleaning — not urgent (exception)",
        "prompt": "You are Diane Clark calling about gutter cleaning but there's no rush. You want it done sometime in the fall, just planning ahead. Address: 88 Timberlea Way, T9K 4E2. Phone: 780-555-4444. Email: diane.c@email.com. Two-story home. You're flexible on timing.",
    },

    # --- Emergency (1 test) ---
    {
        "name": "Emergency — active water leak",
        "prompt": "You are James Wilson and you have water POURING into your basement from the eaves right now. You need someone immediately. Your address is 55 Beacon Hill Crescent, T9K 1M3. Phone: 780-555-6666. Be urgent and stressed.",
    },

    # --- General inquiry (2 tests) ---
    {
        "name": "General inquiry — warranty question",
        "prompt": "You had gutters installed by Eaves and Extras about 2 years ago and you're wondering if they're still under warranty because some are sagging. Your name is Pat Thompson, phone 780-555-8888. Don't want a new quote, just want to know about the warranty.",
    },
    {
        "name": "General inquiry — ask for Craig",
        "prompt": "You want to speak to Craig directly. Say 'Hi, is Craig available? I need to talk to him about a job he looked at last week.' Your name is Steve if asked.",
    },

    # --- Anti-repetition (1 test) ---
    {
        "name": "Anti-repetition — timeline given early",
        "prompt": "You are Mark Davis calling about gutter repair. When describing the issue say 'my gutters are leaking and I need this fixed in the next week or two because we have rain coming.' If Emily asks about timeline after you already said it, point out you already mentioned it. Phone: 780-555-0000, address: 77 Timberlea Court T9K 2A1, email mark.d@email.com. Two-story home.",
    },

    # --- Services mention (1 test) ---
    {
        "name": "Services mention — should mention other services at end",
        "prompt": "You are a simple caller named Amy. You need gutter cleaning at 50 Beacon Hill Road T9K 1R2. Phone: 780-555-8080. Email: amy@email.com. Single story. Available anytime. Be cooperative and quick with answers so the call reaches the summary and closing.",
    },
]


def create_simulation(scenario):
    """Start a simulated call via Retell API."""
    payload = {
        "agent_id": SANDBOX_AGENT_ID,
        "simulate_user_config": {
            "prompt": scenario["prompt"],
        },
    }
    resp = requests.post(
        f"{API_BASE}/v2/create-web-call",
        headers=HEADERS,
        json=payload,
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        return data.get("call_id")
    else:
        print(f"  ERROR creating sim: {resp.status_code} — {resp.text[:200]}")
        return None


def get_call_details(call_id):
    """Fetch call details including transcript and analysis."""
    resp = requests.get(
        f"{API_BASE}/v2/get-call/{call_id}",
        headers=HEADERS,
    )
    if resp.status_code == 200:
        return resp.json()
    return None


def run_tests():
    print(f"Starting {len(TEST_SCENARIOS)} test simulations...")
    print(f"Agent: {SANDBOX_AGENT_ID} (Sandbox)")
    print(f"Estimated cost: ~${len(TEST_SCENARIOS) * 0.33:.2f}")
    print("=" * 60)

    call_ids = []

    # Launch all simulations
    for i, scenario in enumerate(TEST_SCENARIOS, 1):
        print(f"\n[{i}/{len(TEST_SCENARIOS)}] Launching: {scenario['name']}")
        call_id = create_simulation(scenario)
        if call_id:
            print(f"  Call ID: {call_id}")
            call_ids.append((scenario["name"], call_id))
        else:
            call_ids.append((scenario["name"], None))
        time.sleep(1)  # small delay between launches

    print(f"\n{'=' * 60}")
    print(f"Launched {sum(1 for _, cid in call_ids if cid)} / {len(TEST_SCENARIOS)} simulations")
    print("Waiting 3 minutes for calls to complete and analyze...")
    time.sleep(180)

    # Collect results
    print(f"\n{'=' * 60}")
    print("RESULTS")
    print("=" * 60)

    results = []
    for name, call_id in call_ids:
        if not call_id:
            results.append({"name": name, "status": "LAUNCH_FAILED"})
            print(f"\n--- {name} ---")
            print("  STATUS: LAUNCH FAILED")
            continue

        details = get_call_details(call_id)
        if not details:
            results.append({"name": name, "status": "FETCH_FAILED"})
            print(f"\n--- {name} ---")
            print("  STATUS: COULD NOT FETCH")
            continue

        analysis = details.get("call_analysis", {})
        custom = analysis.get("custom_analysis_data", {})
        transcript = details.get("transcript_object", [])
        duration_ms = details.get("duration_ms", 0)
        duration_sec = round(duration_ms / 1000)
        status = details.get("call_status", "unknown")
        disconnect = details.get("disconnection_reason", "unknown")

        # Build transcript text
        transcript_lines = []
        for entry in transcript:
            role = "Emily" if entry.get("role") == "agent" else "Caller"
            transcript_lines.append(f"  {role}: {entry.get('content', '')}")
        transcript_text = "\n".join(transcript_lines[-10:])  # last 10 lines

        result = {
            "name": name,
            "call_id": call_id,
            "status": status,
            "duration_sec": duration_sec,
            "disconnect_reason": disconnect,
            "caller_name": custom.get("caller_name", ""),
            "lead_temperature": custom.get("lead_temperature", ""),
            "support_type": custom.get("support_type", ""),
            "sentiment": analysis.get("user_sentiment", ""),
            "successful": analysis.get("call_successful", False),
            "summary": analysis.get("call_summary", "")[:200],
        }
        results.append(result)

        print(f"\n--- {name} ---")
        print(f"  Duration: {duration_sec}s | Status: {status} | Disconnect: {disconnect}")
        print(f"  Caller: {result['caller_name']} | Lead: {result['lead_temperature']} | Type: {result['support_type']}")
        print(f"  Sentiment: {result['sentiment']} | Successful: {result['successful']}")
        print(f"  Summary: {result['summary']}")
        if transcript_text:
            print(f"  --- Last exchanges ---")
            print(transcript_text)

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print("=" * 60)
    successful = sum(1 for r in results if r.get("successful"))
    failed = sum(1 for r in results if r.get("status") in ("LAUNCH_FAILED", "FETCH_FAILED"))
    total = len(results)
    print(f"Total: {total} | Successful: {successful} | Failed to launch/fetch: {failed}")

    # Check specific scenarios
    gutter_tests = [r for r in results if "gutter cleaning" in r["name"].lower() and r.get("lead_temperature")]
    hot_gutter = sum(1 for r in gutter_tests if r.get("lead_temperature", "").lower() == "hot")
    print(f"Gutter cleaning marked as hot lead: {hot_gutter}/{len(gutter_tests)}")

    # Save full results to file
    with open("test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nFull results saved to test_results.json")


if __name__ == "__main__":
    run_tests()
