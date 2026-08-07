"""
claude_messages.py — Claude Shannon Bot Framework HTTP Trigger

Receives incoming Teams activity at:
  POST /api/claude-messages

This is deliberately the plainest possible version of a Teams bot
endpoint. It proves one thing only: a message sent in Teams can reach
the Anthropic API and a reply can come back, end to end.

WHY this file is separate from messages.py (Leo):
  Leo's endpoint is intentionally open to future expansion — other
  people, other channels, other inputs. Claude Shannon is intentionally
  the opposite: a closed, single-person, single-channel pipe. Keeping
  it in its own file with its own bot identity means the two bots never
  share a failure mode, and Claude Shannon's security checks (below)
  don't need to accommodate Leo's more open future.

WHAT THIS FILE DOES:
  1. Verifies the incoming request really came from the Bot Framework,
     signed for this specific bot (JWT verification).
  2. Checks the sender is the one person this bot is allowed to talk to
     (sender allow-list).
  3. Sends the message text to Claude with no system prompt, no memory,
     no history — a plain call.
  4. Posts Claude's reply back to the same Teams conversation.

WHAT THIS FILE DELIBERATELY DOES NOT DO:
  - No Microsoft Graph calls of any kind.
  - No OneDrive file loading.
  - No conversation history or logging.
  - No triage/button handling.
  - No background threading — a single plain call is simple enough to
    finish comfortably inside the Bot Framework's ~5 second response
    window, and keeping it synchronous makes failures easier to see.

SECURITY MODEL:
  Two independent checks must both pass before Claude is ever called:
    a) JWT verification — proves the request was issued by the Bot
       Framework for this bot's App ID, not just sent to a guessed URL.
    b) Sender allow-list — proves the message came from the one
       Teams user this bot is meant to serve. Configured via the
       CLAUDE_ALLOWED_USER_ID app setting. If that setting is empty,
       the check is skipped and a warning is logged instead — this
       lets the very first test message through so its sender ID can
       be captured from the logs and then locked in as configuration,
       with no code change required.
"""
import os
import logging
import requests
import jwt
from jwt import PyJWKClient
import anthropic
import azure.functions as func

# ── Blueprint registration ──────────────────────────────────────────────────
bp = func.Blueprint()

# ── Bot Framework token verification constants ──────────────────────────────
# WHY these two fixed values:
#   Every Bot Framework activity is signed with a JWT issued by
#   Microsoft's Bot Framework token service. The metadata document below
#   tells us where to fetch the current public signing keys (they rotate
#   periodically, hence fetching rather than hardcoding). The issuer is
#   fixed and documented by Microsoft — it does not vary by bot or tenant.
BF_OPENID_METADATA_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
BF_TOKEN_ISSUER = "https://api.botframework.com"

# WHY a module-level cache:
#   Fetching the signing keys on every single request would be wasteful —
#   they change rarely. PyJWKClient caches internally once created, so we
#   only need to construct it once per warm Function instance.
_jwks_client = None


# ── HTTP Trigger ─────────────────────────────────────────────────────────────
@bp.route(route="claude-messages", methods=["POST"])
def claude_messages(req: func.HttpRequest) -> func.HttpResponse:
    """
    Receive an incoming Teams bot activity for Claude Shannon.

    WHY synchronous, unlike Leo's messages.py:
      Leo's triage actions make 6-7 sequential Graph API calls, which
      risks exceeding the Bot Framework's ~5 second timeout — that's why
      Leo replies 200 immediately and finishes work on a background
      thread. Claude Shannon makes exactly one outbound call (to the
      Anthropic API) with nothing else in between. Keeping this
      synchronous means if something fails, the failure is visible in
      the same request/response cycle — simpler to diagnose, which is
      the whole point of this first version.
    """
    logging.info("claude_messages: incoming request received")

    # ── Step 1: verify the request is genuinely from the Bot Framework ──────
    auth_header = req.headers.get("Authorization", "")
    if not _verify_bot_framework_token(auth_header):
        logging.warning("claude_messages: JWT verification failed — request rejected")
        return func.HttpResponse("Unauthorized", status_code=401)

    # ── Step 2: parse the Activity body ──────────────────────────────────────
    try:
        body = req.get_json()
    except ValueError:
        logging.warning("claude_messages: request body is not valid JSON")
        return func.HttpResponse("Bad Request", status_code=400)

    activity_type = body.get("type", "")
    if activity_type != "message":
        # WHY we don't reply to non-message activities:
        #   conversationUpdate (e.g. the bot being added to a chat) and
        #   other event types don't carry user text and don't need a
        #   Claude call. Acknowledging with 200 and no reply is correct.
        logging.info(f"claude_messages: activity type '{activity_type}' — no reply sent")
        return func.HttpResponse(status_code=200)

    sender_id = body.get("from", {}).get("id", "")
    logging.info(f"claude_messages: sender id = {sender_id}")

    # ── Step 3: check the sender against the allow-list ─────────────────────
    if not _sender_is_allowed(sender_id):
        logging.warning(f"claude_messages: sender '{sender_id}' not on allow-list — ignored")
        return func.HttpResponse(status_code=200)

    text_in = (body.get("text") or "").strip()
    if not text_in:
        logging.info("claude_messages: empty text — no reply sent")
        return func.HttpResponse(status_code=200)

    # ── Step 4: call Claude and reply ────────────────────────────────────────
    conversation = body.get("conversation", {})
    conversation_id = conversation.get("id", "")
    service_url = body.get("serviceUrl", "")

    try:
        reply_text = _call_claude(text_in)
        _send_reply(service_url, conversation_id, body, reply_text)
    except Exception as e:
        logging.error(f"claude_messages: failed to process message — {e}")
        return func.HttpResponse(status_code=200)

    return func.HttpResponse(status_code=200)


# ── Bot Framework JWT verification ───────────────────────────────────────────
def _get_jwks_client() -> PyJWKClient:
    """
    Fetch (and cache) the Bot Framework's current public signing keys.

    WHY we fetch the metadata document rather than hardcoding a JWKS URL:
      Microsoft documents the metadata endpoint as the stable entry
      point; the actual key-set URL it points to is what can change
      over time. Following the metadata document is the resilient
      approach.
    """
    global _jwks_client
    if _jwks_client is None:
        resp = requests.get(BF_OPENID_METADATA_URL, timeout=10)
        resp.raise_for_status()
        jwks_uri = resp.json()["jwks_uri"]
        _jwks_client = PyJWKClient(jwks_uri)
    return _jwks_client


def _verify_bot_framework_token(auth_header: str) -> bool:
    """
    Verify the Authorization header carries a valid Bot Framework JWT
    issued specifically for this bot.

    WHY we check audience against CLAUDE_BOT_APP_ID:
      The audience claim identifies which bot the token was issued for.
      Checking it here means a token issued for a different bot (Leo,
      or any other bot in the tenant) is rejected even if it is
      otherwise validly signed by Microsoft.
    WHY we check issuer:
      Confirms the token came from the Bot Framework's own token
      service, not some other JWT issuer that happens to reuse the
      same signing algorithm.
    WHY we return False on any exception:
      Expired tokens, malformed tokens, signature mismatches, and
      network failures fetching the keys are all treated the same way —
      as "not verified". This endpoint should fail closed, not open.
    """
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(" ", 1)[1]

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=os.environ["CLAUDE_BOT_APP_ID"],
            issuer=BF_TOKEN_ISSUER,
        )
        return True
    except Exception as e:
        logging.error(f"claude_messages: JWT verification error — {e}")
        return False


# ── Sender allow-list ─────────────────────────────────────────────────────────
def _sender_is_allowed(sender_id: str) -> bool:
    """
    Check whether the message sender is the one person this bot serves.

    WHY an empty CLAUDE_ALLOWED_USER_ID setting is treated as "allow,
    but warn" rather than "deny":
      On the very first test message, we don't yet know Phillip's Teams
      user ID for this conversation — it has to come from a real
      message. Denying everything until the setting exists would create
      a chicken-and-egg problem. Logging the sender ID (done in the main
      handler above) combined with allowing through when unconfigured
      means the first message both succeeds and reveals the ID needed
      to lock the check down immediately afterwards.
    """
    allowed_id = os.environ.get("CLAUDE_ALLOWED_USER_ID", "")
    if not allowed_id:
        logging.warning(
            "claude_messages: CLAUDE_ALLOWED_USER_ID not yet set — "
            "allowing this message through unchecked"
        )
        return True
    return sender_id == allowed_id


# ── Claude API caller ─────────────────────────────────────────────────────────
def _call_claude(user_text: str) -> str:
    """
    Send the user's message straight to Claude and return the reply text.

    WHY no system prompt:
      This first version is deliberately plain — no voice file, no
      personality, no memory of past messages. That keeps the surface
      area small: if something breaks, it's obviously plumbing
      (Teams ↔ Function ↔ Anthropic API) rather than prompting.
    WHY max_tokens 1000:
      Generous enough for a normal conversational reply without being
      unbounded. Matches the ceiling used elsewhere in this project.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[
            {"role": "user", "content": user_text},
        ],
    )
    return message.content[0].text


# ── Reply sender ───────────────────────────────────────────────────────────────
def _send_reply(
    service_url: str,
    conversation_id: str,
    incoming_body: dict,
    text: str,
) -> None:
    """
    Post a reply to the originating Teams conversation.

    WHY this mirrors Leo's _send_reply pattern:
      It's a proven, working approach already confirmed in production
      (messages.py). No reason to invent a different mechanism for the
      same job — only the bot identity (App ID, secret) differs.
    """
    bot_token = _get_bot_token()
    bot_app_id = os.environ["CLAUDE_BOT_APP_ID"]

    url = (
        f"{service_url.rstrip('/')}/v3/conversations/"
        f"{conversation_id}/activities/{incoming_body.get('id', '')}"
    )
    payload = {
        "type": "message",
        "from": {"id": bot_app_id, "name": "Claude Shannon"},
        "recipient": incoming_body.get("from", {}),
        "replyToId": incoming_body.get("id", ""),
        "text": text,
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    logging.info(f"claude_messages: reply delivered, status {resp.status_code}")


# ── Bot Framework authentication (outbound) ──────────────────────────────────
def _get_bot_token() -> str:
    """
    Obtain a Bot Framework access token for sending replies, using
    Claude Shannon's own App ID and client secret.

    WHY separate app settings from Leo's bot credentials:
      CLAUDE_BOT_APP_ID and CLAUDE_BOT_CLIENT_SECRET are Claude
      Shannon's own values, sourced from the claude-bot-app-id and
      claude-bot-client-secret Key Vault secrets. Keeping them fully
      separate from BOT_APP_ID / BOT_CLIENT_SECRET (Leo's) is what
      keeps the two bots' identities from ever being mixed up.
    """
    bot_app_id = os.environ["CLAUDE_BOT_APP_ID"]
    bot_secret = os.environ["CLAUDE_BOT_CLIENT_SECRET"]
    tenant_id = os.environ["TENANT_ID"]

    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": bot_app_id,
            "client_secret": bot_secret,
            "scope": "https://api.botframework.com/.default",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]
