import httpx
import os
from typing import Optional

WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def send_text(to: str, body: str) -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    r = httpx.post(f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages", json=payload, headers=_headers())
    r.raise_for_status()
    return r.json()


def send_buttons(to: str, body: str, buttons: list[dict]) -> dict:
    """Send interactive reply buttons (max 3)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons[:3]
                ]
            },
        },
    }
    r = httpx.post(f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages", json=payload, headers=_headers())
    r.raise_for_status()
    return r.json()


def send_list(to: str, body: str, button_label: str, sections: list[dict]) -> dict:
    """Send interactive list menu."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_label,
                "sections": sections,
            },
        },
    }
    r = httpx.post(f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages", json=payload, headers=_headers())
    r.raise_for_status()
    return r.json()


def download_media(media_id: str) -> tuple[bytes, str]:
    """Download a media file by ID. Returns (content_bytes, mime_type)."""
    r = httpx.get(f"{WHATSAPP_API_URL}/{media_id}", headers=_headers())
    r.raise_for_status()
    media_url = r.json()["url"]
    file_r = httpx.get(media_url, headers=_headers(), follow_redirects=True)
    file_r.raise_for_status()
    mime = file_r.headers.get("content-type", "application/octet-stream")
    return file_r.content, mime


def parse_incoming(payload: dict) -> Optional[dict]:
    """Extract a normalised message dict from a webhook payload."""
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        msg = entry["messages"][0]
        contact = entry["contacts"][0]
        name = contact.get("profile", {}).get("name", "Customer")
        result = {
            "from": msg["from"],
            "name": name,
            "type": msg["type"],
            "message_id": msg["id"],
        }
        if msg["type"] == "text":
            result["text"] = msg["text"]["body"]
        elif msg["type"] in ("image", "document", "video"):
            media_obj = msg[msg["type"]]
            result["media_id"] = media_obj.get("id")
            result["mime_type"] = media_obj.get("mime_type", "")
            result["filename"] = media_obj.get("filename", "")
        elif msg["type"] == "interactive":
            interactive = msg["interactive"]
            if interactive["type"] == "button_reply":
                result["text"] = interactive["button_reply"]["id"]
            elif interactive["type"] == "list_reply":
                result["text"] = interactive["list_reply"]["id"]
        return result
    except (KeyError, IndexError):
        return None
