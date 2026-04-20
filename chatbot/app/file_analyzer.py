import io
import tempfile
import os


def analyze_pdf(content: bytes) -> dict:
    """Return page count and colour flag for a PDF."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=content, filetype="pdf")
        pages = doc.page_count
        has_colour = False
        for page in doc:
            pix = page.get_pixmap(colorspace=fitz.csRGB, alpha=False)
            # Sample pixels to detect colour (non-greyscale values)
            samples = pix.samples
            for i in range(0, min(len(samples), 3000), 3):
                r, g, b = samples[i], samples[i + 1], samples[i + 2]
                if abs(int(r) - int(g)) > 10 or abs(int(g) - int(b)) > 10:
                    has_colour = True
                    break
            if has_colour:
                break
        doc.close()
        return {"pages": pages, "has_colour": has_colour, "format": "pdf"}
    except Exception as e:
        return {"pages": 1, "has_colour": False, "format": "pdf", "error": str(e)}


def analyze_docx(content: bytes) -> dict:
    """Return approximate page count for a DOCX (1 page per 500 words heuristic)."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(content))
        text = " ".join(p.text for p in doc.paragraphs)
        word_count = len(text.split())
        pages = max(1, round(word_count / 500))
        return {"pages": pages, "has_colour": False, "format": "docx"}
    except Exception as e:
        return {"pages": 1, "has_colour": False, "format": "docx", "error": str(e)}


def analyze_image(mime_type: str) -> dict:
    """Images are single-page and assumed colour."""
    return {"pages": 1, "has_colour": True, "format": "image"}


def analyze_file(content: bytes, mime_type: str, filename: str = "") -> dict:
    fname = filename.lower()
    if "pdf" in mime_type or fname.endswith(".pdf"):
        return analyze_pdf(content)
    if "word" in mime_type or fname.endswith((".docx", ".doc")):
        return analyze_docx(content)
    if mime_type.startswith("image/"):
        return analyze_image(mime_type)
    # Fallback: treat as 1 page
    return {"pages": 1, "has_colour": False, "format": "unknown"}
