"""Read-only audit: list all Jobber clients, group by normalized phone, flag dupes.

Run with the E&E Agent venv so .env (Jobber creds) loads:
    .venv/bin/python audit_jobber_duplicates.py
"""
import os
import re
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

import jobber_client


def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:] if len(digits) >= 10 else digits


def list_all_clients():
    """Paginated GraphQL fetch of every client with their phones + email."""
    query = """
        query AllClients($after: String) {
            clients(first: 100, after: $after) {
                pageInfo { hasNextPage endCursor }
                nodes {
                    id
                    firstName
                    lastName
                    createdAt
                    phones { number description }
                    emails { address }
                }
            }
        }
    """
    after = None
    while True:
        data = jobber_client._graphql(query, {"after": after})
        block = data.get("clients", {})
        for node in block.get("nodes", []):
            yield node
        page = block.get("pageInfo", {})
        if not page.get("hasNextPage"):
            return
        after = page.get("endCursor")


def main():
    if not jobber_client.is_configured():
        print("ERROR: Jobber env vars not set. Check .env.")
        sys.exit(1)

    by_phone = defaultdict(list)
    total = 0
    no_phone = 0

    for c in list_all_clients():
        total += 1
        phones = c.get("phones") or []
        if not phones:
            no_phone += 1
            continue
        for p in phones:
            norm = normalize_phone(p.get("number"))
            if norm:
                by_phone[norm].append({
                    "id": c["id"],
                    "name": f"{c.get('firstName','')} {c.get('lastName','')}".strip(),
                    "raw_phone": p.get("number"),
                    "created": c.get("createdAt"),
                })

    dupes = {k: v for k, v in by_phone.items() if len({entry["id"] for entry in v}) > 1}

    print(f"Total clients scanned: {total}")
    print(f"Clients with no phone:  {no_phone}")
    print(f"Distinct normalized phones: {len(by_phone)}")
    print(f"Duplicate phone groups:     {len(dupes)}")
    print()

    if not dupes:
        print("No duplicates found.")
        return

    for norm, entries in sorted(dupes.items()):
        uniq_ids = {e["id"] for e in entries}
        print(f"--- phone …{norm[-4:]} ({len(uniq_ids)} clients) ---")
        seen = set()
        for e in entries:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            print(f"  • {e['name']!r:40} raw={e['raw_phone']!r:18} created={e['created']}  id={e['id']}")
        print()


if __name__ == "__main__":
    main()
