"""
Central conversation handler. Each incoming message goes through handle_message()
which reads the session state, decides the next action, and sends WhatsApp replies.
"""

import os
import tempfile
import logging
from . import whatsapp as wa
from . import orders as db
from .states import OrderState
from .pricing import calculate_quote, format_quote
from .file_analyzer import analyze_file

logger = logging.getLogger(__name__)

MOMO_NUMBERS = {
    "MTN MoMo": os.getenv("MOMO_MTN", "024XXXXXXX"),
    "Vodafone Cash": os.getenv("MOMO_VODAFONE", "050XXXXXXX"),
    "AirtelTigo Money": os.getenv("MOMO_AIRTELTIGO", "027XXXXXXX"),
}
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Una Print Services")


# ── Public entry point ───────────────────────────────────────────────────────

def handle_message(msg: dict):
    phone = msg["from"]
    name = msg.get("name", "Customer")
    session = db.get_session(phone)

    state = session["state"]
    ctx = session.get("context", {})
    order_ref = session.get("order_ref")

    # Allow restart at any time
    text = (msg.get("text") or "").strip().lower()
    if text in ("hi", "hello", "start", "menu", "restart", "new order"):
        _start_welcome(phone, name)
        return

    next_state, ctx, order_ref = _dispatch(state, msg, ctx, order_ref, phone, name)
    db.save_session(phone, name, next_state, order_ref, ctx)


# ── Dispatcher ───────────────────────────────────────────────────────────────

def _dispatch(state, msg, ctx, order_ref, phone, name):
    text = (msg.get("text") or "").strip()

    if state == OrderState.WELCOME:
        return _handle_welcome(phone, name, ctx, order_ref)

    if state == OrderState.SELECT_PRINT_TYPE:
        return _handle_print_type(phone, name, ctx, order_ref, text)

    if state == OrderState.SELECT_PAPER_SIZE:
        return _handle_paper_size(phone, name, ctx, order_ref, text)

    if state == OrderState.SELECT_COPIES:
        return _handle_copies(phone, name, ctx, order_ref, text)

    if state == OrderState.SELECT_EXTRAS:
        return _handle_extras(phone, name, ctx, order_ref, text)

    if state == OrderState.UPLOAD_FILE:
        return _handle_file_upload(phone, name, ctx, order_ref, msg)

    if state == OrderState.QUOTE_REVIEW:
        return _handle_quote_review(phone, name, ctx, order_ref, text)

    if state == OrderState.PAYMENT_PENDING:
        return _handle_payment_pending(phone, name, ctx, order_ref, text)

    if state == OrderState.PAYMENT_PROOF:
        return _handle_payment_proof(phone, name, ctx, order_ref, msg)

    # Default fallback
    _start_welcome(phone, name)
    return OrderState.SELECT_PRINT_TYPE, {}, order_ref


# ── Step handlers ────────────────────────────────────────────────────────────

def _start_welcome(phone, name):
    wa.send_text(phone, (
        f"👋 Welcome to *{BUSINESS_NAME}*, {name}!\n\n"
        "We handle all your printing needs — fast, affordable, and delivered.\n\n"
        "Type *hi* or *menu* anytime to restart."
    ))
    wa.send_list(
        to=phone,
        body="What would you like to print today?",
        button_label="Choose type",
        sections=[{
            "title": "Print Type",
            "rows": [
                {"id": "bw", "title": "Black & White", "description": "From GHS 0.50/page"},
                {"id": "colour", "title": "Full Colour", "description": "From GHS 2.00/page"},
            ],
        }],
    )
    db.save_session(phone, name, OrderState.SELECT_PRINT_TYPE, None, {})


def _handle_welcome(phone, name, ctx, order_ref):
    _start_welcome(phone, name)
    return OrderState.SELECT_PRINT_TYPE, {}, None


def _handle_print_type(phone, name, ctx, order_ref, text):
    if text not in ("bw", "colour"):
        wa.send_text(phone, "Please choose *Black & White* or *Full Colour* from the menu.")
        return OrderState.SELECT_PRINT_TYPE, ctx, order_ref

    ctx["print_type"] = text
    wa.send_buttons(
        to=phone,
        body="Which paper size do you need?",
        buttons=[
            {"id": "a4", "title": "A4 (standard)"},
            {"id": "a3", "title": "A3 (large)"},
        ],
    )
    return OrderState.SELECT_PAPER_SIZE, ctx, order_ref


def _handle_paper_size(phone, name, ctx, order_ref, text):
    if text not in ("a4", "a3"):
        wa.send_text(phone, "Please select *A4* or *A3* from the buttons above.")
        return OrderState.SELECT_PAPER_SIZE, ctx, order_ref

    ctx["paper_size"] = text
    wa.send_text(phone, "How many *copies* do you need? (Reply with a number, e.g. *1*)")
    return OrderState.SELECT_COPIES, ctx, order_ref


def _handle_copies(phone, name, ctx, order_ref, text):
    try:
        copies = int(text)
        if copies < 1:
            raise ValueError
    except ValueError:
        wa.send_text(phone, "Please enter a valid number of copies (e.g. *2*).")
        return OrderState.SELECT_COPIES, ctx, order_ref

    ctx["copies"] = copies
    wa.send_list(
        to=phone,
        body="Any extras? (Select all that apply, or choose *None*)\nReply with the option ID.",
        button_label="Choose extras",
        sections=[{
            "title": "Finishing Options",
            "rows": [
                {"id": "none", "title": "None", "description": "No extras"},
                {"id": "binding", "title": "Binding", "description": f"GHS 5.00"},
                {"id": "spiral", "title": "Spiral Binding", "description": f"GHS 8.00"},
                {"id": "lamination", "title": "Lamination", "description": f"GHS 3.00/page"},
            ],
        }],
    )
    return OrderState.SELECT_EXTRAS, ctx, order_ref


def _handle_extras(phone, name, ctx, order_ref, text):
    valid = {"none", "binding", "spiral", "lamination"}
    if text not in valid:
        wa.send_text(phone, "Please choose from the options in the menu.")
        return OrderState.SELECT_EXTRAS, ctx, order_ref

    ctx["binding"] = text == "binding"
    ctx["spiral"] = text == "spiral"
    ctx["lamination"] = text == "lamination"

    # Create order record now
    if not order_ref:
        order_ref = db.create_order(phone, name)
    db.update_order(
        order_ref,
        print_type=ctx["print_type"],
        paper_size=ctx["paper_size"],
        copies=ctx["copies"],
        binding=int(ctx["binding"]),
        spiral=int(ctx["spiral"]),
        lamination=int(ctx["lamination"]),
    )

    wa.send_text(phone, (
        f"✅ Great! Now please *send your file* to print.\n\n"
        f"Accepted formats: *PDF, Word (.docx), JPG, PNG*\n"
        f"Max size: 100 MB\n\n"
        f"_Your order reference: *{order_ref}*_"
    ))
    return OrderState.UPLOAD_FILE, ctx, order_ref


def _handle_file_upload(phone, name, ctx, order_ref, msg):
    media_id = msg.get("media_id")
    if not media_id:
        wa.send_text(phone, "Please *send a file* (PDF, Word, or image). Text messages won't work here.")
        return OrderState.UPLOAD_FILE, ctx, order_ref

    wa.send_text(phone, "⏳ Analysing your file, please wait...")

    try:
        content, mime_type = wa.download_media(media_id)
        filename = msg.get("filename", "")
        analysis = analyze_file(content, mime_type, filename)
    except Exception as e:
        logger.error("File analysis failed: %s", e)
        wa.send_text(phone, "❌ Could not read your file. Please send a valid PDF, Word doc, or image.")
        return OrderState.UPLOAD_FILE, ctx, order_ref

    pages = analysis["pages"]
    has_colour = analysis["has_colour"]
    ctx["pages"] = pages
    ctx["detected_colour"] = has_colour

    # Auto-upgrade to colour if colour content detected and user chose B&W
    if has_colour and ctx.get("print_type") == "bw":
        ctx["print_type"] = "colour"
        wa.send_text(phone, "ℹ️ Colour content detected in your file — switched to *Colour printing* automatically.")

    # Save file temporarily
    suffix = _mime_to_ext(mime_type, filename)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=_file_store())
    tmp.write(content)
    tmp.close()
    ctx["file_path"] = tmp.name
    db.update_order(order_ref, pages=pages, file_path=tmp.name)

    # Build quote
    q = calculate_quote(
        print_type=ctx["print_type"],
        paper_size=ctx["paper_size"],
        pages=pages,
        copies=ctx["copies"],
        binding=ctx.get("binding", False),
        spiral=ctx.get("spiral", False),
        lamination=ctx.get("lamination", False),
    )
    ctx["total"] = q.total
    db.update_order(order_ref, total=q.total, print_type=ctx["print_type"])

    quote_text = format_quote(q, order_ref)
    wa.send_text(phone, f"📄 File received! *{pages} page(s)* detected.\n\n{quote_text}")
    wa.send_buttons(
        to=phone,
        body="Do you want to proceed with this order?",
        buttons=[
            {"id": "confirm_order", "title": "✅ Confirm"},
            {"id": "cancel_order", "title": "❌ Cancel"},
        ],
    )
    return OrderState.QUOTE_REVIEW, ctx, order_ref


def _handle_quote_review(phone, name, ctx, order_ref, text):
    if text == "cancel_order":
        db.update_order(order_ref, status="cancelled")
        wa.send_text(phone, "Order cancelled. Type *hi* to start a new order.")
        db.reset_session(phone)
        return OrderState.DONE, {}, None

    if text != "confirm_order":
        wa.send_text(phone, "Please tap *Confirm* or *Cancel*.")
        return OrderState.QUOTE_REVIEW, ctx, order_ref

    total = ctx.get("total", 0)
    momo_lines = "\n".join(f"• {net}: *{num}*" for net, num in MOMO_NUMBERS.items())
    wa.send_text(phone, (
        f"💳 *Payment Instructions — Order #{order_ref}*\n\n"
        f"Amount: *GHS {total:.2f}*\n\n"
        f"Send payment to any of these numbers:\n{momo_lines}\n\n"
        f"📌 Use *{order_ref}* as your payment reference/narration.\n\n"
        f"⏰ You have *30 minutes* to complete payment."
    ))
    wa.send_text(phone, "After paying, please send a *screenshot* or the *transaction ID* as proof of payment.")
    db.update_order(order_ref, status="pending_payment")
    return OrderState.PAYMENT_PROOF, ctx, order_ref


def _handle_payment_pending(phone, name, ctx, order_ref, text):
    wa.send_text(phone, "Please send your *payment screenshot* or *transaction ID* to confirm your order.")
    return OrderState.PAYMENT_PROOF, ctx, order_ref


def _handle_payment_proof(phone, name, ctx, order_ref, msg):
    text = (msg.get("text") or "").strip()
    media_id = msg.get("media_id")

    if not text and not media_id:
        wa.send_text(phone, "Please send your *payment screenshot* or type the *transaction ID*.")
        return OrderState.PAYMENT_PROOF, ctx, order_ref

    payment_ref = text if text else f"screenshot:{media_id}"
    db.update_order(order_ref, status="payment_received", payment_ref=payment_ref)

    total = ctx.get("total", 0)
    print_type = "Colour" if ctx.get("print_type") == "colour" else "B&W"
    size = (ctx.get("paper_size") or "a4").upper()
    pages = ctx.get("pages", 0)
    copies = ctx.get("copies", 1)

    wa.send_text(phone, (
        f"✅ *Payment Received! Order Confirmed.*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *Order Summary*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Order Ref: *#{order_ref}*\n"
        f"Print Type: *{print_type} — {size}*\n"
        f"Pages: *{pages}*\n"
        f"Copies: *{copies}*\n"
        f"Total Paid: *GHS {total:.2f}*\n"
        f"Payment Ref: _{payment_ref}_\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖨️ Your document is now in the print queue.\n"
        f"⏱️ Estimated ready time: *1–2 hours*\n\n"
        f"We will notify you once it's ready for pickup/delivery.\n"
        f"Thank you for choosing *{BUSINESS_NAME}*! 🙏"
    ))

    db.reset_session(phone)
    return OrderState.DONE, {}, None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _file_store() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "data", "uploads")
    os.makedirs(path, exist_ok=True)
    return path


def _mime_to_ext(mime_type: str, filename: str) -> str:
    if filename:
        _, ext = os.path.splitext(filename)
        if ext:
            return ext
    mapping = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "image/jpeg": ".jpg",
        "image/png": ".png",
    }
    return mapping.get(mime_type, ".bin")
