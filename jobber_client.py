"""Jobber API client — OAuth refresh + GraphQL helpers for lead capture."""
import os
import time
import requests

JOBBER_AUTH_URL = "https://api.getjobber.com/api/oauth/authorize"
JOBBER_TOKEN_URL = "https://api.getjobber.com/api/oauth/token"
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
JOBBER_API_VERSION = "2025-04-16"

_token_cache = {"access_token": None, "expires_at": 0}


def _client_id():
    return os.getenv("JOBBER_CLIENT_ID", "")


def _client_secret():
    return os.getenv("JOBBER_CLIENT_SECRET", "")


def _refresh_token():
    return os.getenv("JOBBER_REFRESH_TOKEN", "")


def is_configured():
    return bool(_client_id() and _client_secret() and _refresh_token())


def authorize_url(state=""):
    redirect_uri = os.getenv("JOBBER_REDIRECT_URI", "")
    return (
        f"{JOBBER_AUTH_URL}"
        f"?client_id={_client_id()}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&state={state}"
    )


def exchange_code_for_tokens(code):
    """Exchange auth code from Jobber callback for access + refresh tokens."""
    resp = requests.post(
        JOBBER_TOKEN_URL,
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": os.getenv("JOBBER_REDIRECT_URI", ""),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _get_access_token():
    """Returns a valid access token, refreshing from the stored refresh token if needed."""
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    resp = requests.post(
        JOBBER_TOKEN_URL,
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "refresh_token",
            "refresh_token": _refresh_token(),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["access_token"] = payload["access_token"]
    _token_cache["expires_at"] = now + payload.get("expires_in", 3600) - 60
    return _token_cache["access_token"]


def _graphql(query, variables=None):
    token = _get_access_token()
    resp = requests.post(
        JOBBER_GRAPHQL_URL,
        json={"query": query, "variables": variables or {}},
        headers={
            "Authorization": f"bearer {token}",
            "X-JOBBER-GRAPHQL-VERSION": JOBBER_API_VERSION,
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Jobber GraphQL errors: {body['errors']}")
    return body.get("data", {})


def _digits_only(phone):
    """Strip everything but digits. '+1 (780) 555-1234' -> '17805551234'."""
    return "".join(c for c in (phone or "") if c.isdigit())


def _last10(phone):
    """Last 10 digits — robust to country-code presence/absence."""
    d = _digits_only(phone)
    return d[-10:] if len(d) >= 10 else d


def find_client_by_phone(phone):
    """Search Jobber by phone using last-10-digit match. Returns the first true match's id, or None.

    Why: Jobber's searchTerm is fuzzy text search; a raw phone like '+17805551234' may miss
    a stored number formatted as '(780) 555-1234'. We search with the last-10-digit form
    and then verify each result's phone digits actually contain our number — preventing
    false-positive matches from substring noise.
    """
    target = _last10(phone)
    if not target:
        return None
    query = """
        query SearchClients($term: String!) {
            clients(searchTerm: $term, first: 10) {
                nodes { id firstName lastName phones { number } }
            }
        }
    """
    try:
        data = _graphql(query, {"term": target})
        nodes = data.get("clients", {}).get("nodes", []) or []
        for node in nodes:
            for ph in node.get("phones", []) or []:
                if _last10(ph.get("number", "")) == target:
                    return node["id"]
        return None
    except Exception:
        return None


def _split_name(full_name):
    full_name = (full_name or "").strip()
    if not full_name:
        return ("New", "Lead")
    parts = full_name.split(None, 1)
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], parts[1])


def create_client(name, phone="", email="", address=""):
    first, last = _split_name(name)
    client_input = {"firstName": first, "lastName": last}
    if phone:
        client_input["phones"] = [
            {"description": "MAIN", "primary": True, "number": phone}
        ]
    if email:
        client_input["emails"] = [
            {"description": "MAIN", "primary": True, "address": email}
        ]
    if address:
        client_input["billingAddress"] = {"street1": address, "country": "Canada"}

    mutation = """
        mutation CreateClient($input: ClientCreateInput!) {
            clientCreate(input: $input) {
                client { id firstName lastName }
                userErrors { message path }
            }
        }
    """
    data = _graphql(mutation, {"input": client_input})
    result = data.get("clientCreate", {})
    errors = result.get("userErrors") or []
    if errors:
        raise RuntimeError(f"Jobber clientCreate userErrors: {errors}")
    return result.get("client", {}).get("id")


def create_note(client_id, body):
    """Attach a note to a client. Returns True on success, False on failure (non-fatal)."""
    if not client_id or not body:
        return False
    mutation = """
        mutation AddClientNote($clientId: EncodedId!, $input: ClientCreateNoteInput!) {
            clientCreateNote(clientId: $clientId, input: $input) {
                clientNote { id }
                userErrors { message path }
            }
        }
    """
    try:
        _graphql(mutation, {"clientId": client_id, "input": {"message": body}})
        return True
    except Exception:
        return False


def create_request(client_id, title):
    """Create a Jobber Request for a client. Returns the request id, or None on failure.

    Why: a Request is what flips Jobber's automatic 'lead' tag on a new client. Creating a
    bare Client via clientCreate leaves them un-tagged, which is why Sara/Craig saw every
    caller showing up as a regular client. Routing every Emily intake through requestCreate
    puts new callers into the Requests inbox as leads.
    """
    if not client_id:
        return None
    mutation = """
        mutation CreateRequest($input: RequestCreateInput!) {
            requestCreate(input: $input) {
                request { id }
                userErrors { message path }
            }
        }
    """
    try:
        data = _graphql(mutation, {"input": {"clientId": client_id, "title": title or "Phone intake"}})
        result = data.get("requestCreate", {})
        if result.get("userErrors"):
            return None
        return (result.get("request") or {}).get("id")
    except Exception:
        return None


def create_request_note(request_id, body):
    """Attach a note to a Request. Returns True on success, False on failure (non-fatal)."""
    if not request_id or not body:
        return False
    mutation = """
        mutation AddRequestNote($requestId: EncodedId!, $input: RequestCreateNoteInput!) {
            requestCreateNote(requestId: $requestId, input: $input) {
                requestNote { id }
                userErrors { message path }
            }
        }
    """
    try:
        _graphql(mutation, {"requestId": request_id, "input": {"message": body}})
        return True
    except Exception:
        return False


def introspect_input(type_name):
    """Return the input fields for a GraphQL input type. Returns {} on failure."""
    query = """
        query Introspect($name: String!) {
            __type(name: $name) {
                name
                inputFields {
                    name
                    type { name kind ofType { name kind ofType { name kind } } }
                }
            }
        }
    """
    try:
        data = _graphql(query, {"name": type_name})
        t = data.get("__type") or {}
        out = {"type": t.get("name"), "fields": []}
        for f in (t.get("inputFields") or []):
            tinfo = f["type"]
            tname = tinfo.get("name") or (tinfo.get("ofType") or {}).get("name") or (((tinfo.get("ofType") or {}).get("ofType")) or {}).get("name")
            out["fields"].append({"name": f["name"], "type": tname, "kind": tinfo.get("kind")})
        return out
    except Exception as e:
        return {"type": type_name, "error": str(e), "fields": []}


def introspect_mutation_args(field_name):
    """Return the argument list for a mutation field."""
    query = """
        query Args {
            __schema {
                mutationType {
                    fields {
                        name
                        args { name type { name kind ofType { name kind } } }
                    }
                }
            }
        }
    """
    try:
        data = _graphql(query)
        fields = data.get("__schema", {}).get("mutationType", {}).get("fields", [])
        for f in fields:
            if f["name"] == field_name:
                args = []
                for a in f.get("args", []):
                    t = a["type"]
                    tname = t.get("name") or (t.get("ofType") or {}).get("name")
                    args.append({"name": a["name"], "type": tname, "kind": t.get("kind")})
                return {"name": field_name, "args": args}
        return {"name": field_name, "error": "field not found"}
    except Exception as e:
        return {"name": field_name, "error": str(e)}


def list_mutation_names():
    """Return mutation field names that contain 'create' or 'request' — for discovery."""
    query = """
        query { __schema { mutationType { fields { name } } } }
    """
    try:
        data = _graphql(query)
        fields = data.get("__schema", {}).get("mutationType", {}).get("fields", [])
        names = [f["name"] for f in fields]
        relevant = [n for n in names if "create" in n.lower() or "request" in n.lower() or "lead" in n.lower()]
        return {"relevant": sorted(relevant), "total": len(names)}
    except Exception as e:
        return {"error": str(e)}


def upsert_lead(name, phone="", email="", address="", call_summary=""):
    """Find or create a client, create a Request for the call, attach the call summary.

    Returns dict {client_id, request_id, was_existing_client} so the caller can log/alert.

    Flow:
      1. Search Jobber by last-10-digit phone match (false-positive resistant).
      2. If no match: clientCreate.
      3. requestCreate against the client — this is what auto-tags new clients as leads
         and surfaces the call in Sara/Craig's Requests inbox.
      4. requestCreateNote with the full call summary (so the Request page shows what was
         discussed). If the Request couldn't be created, fall back to attaching the note
         directly on the Client so the summary still lands somewhere.
    """
    was_existing = False
    client_id = find_client_by_phone(phone) if phone else None
    if client_id:
        was_existing = True
    else:
        client_id = create_client(name, phone=phone, email=email, address=address)

    request_title = f"Phone intake — {name}" if name else "Phone intake"
    request_id = create_request(client_id, request_title)

    if call_summary:
        attached = False
        if request_id:
            attached = create_request_note(request_id, call_summary)
        if not attached:
            create_note(client_id, call_summary)

    return {"client_id": client_id, "request_id": request_id, "was_existing_client": was_existing}
