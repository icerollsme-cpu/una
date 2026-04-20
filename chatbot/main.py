"""
FastAPI WhatsApp webhook server for Una Print Services chatbot.

Start with:
    uvicorn chatbot.main:app --host 0.0.0.0 --port 8000 --reload

Expose publicly (for Meta webhook verification) using ngrok or a VPS.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse

from chatbot.app import orders as db
from chatbot.app import whatsapp as wa
from chatbot.app.chatbot import handle_message

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "una_print_verify")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    logger.info("Database initialised.")
    yield


app = FastAPI(title="Una Print WhatsApp Bot", lifespan=lifespan)


# ── Webhook verification (GET) ───────────────────────────────────────────────

@app.get("/webhook")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified.")
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Incoming messages (POST) ─────────────────────────────────────────────────

@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()
    logger.debug("Payload: %s", payload)

    try:
        msg = wa.parse_incoming(payload)
        if msg:
            handle_message(msg)
    except Exception as e:
        logger.error("Error handling message: %s", e, exc_info=True)

    # Always return 200 to Meta, even on errors, to prevent retries
    return {"status": "ok"}


# ── Admin: list orders ───────────────────────────────────────────────────────

@app.get("/admin/orders")
def admin_orders(secret: str = Query(...)):
    admin_secret = os.getenv("ADMIN_SECRET", "changeme")
    if secret != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    return db.list_pending_orders()


@app.get("/health")
def health():
    return {"status": "ok", "service": "Una Print Bot"}
