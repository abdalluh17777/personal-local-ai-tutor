"""Course material: text extraction, chunking into passages, and search-query building."""

import codecs
import io
import re

from pypdf import PdfReader

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf")

CHUNK_WORDS = 180
CHUNK_OVERLAP = 30

_ARABIC = re.compile(r"[؀-ۿ]")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")

# Common English words that carry no topic meaning; kept short on purpose
STOPWORDS = set("""
about above after again against all also and any are because been before being below between both but
can could did does doing down during each few for from further had has have having her here hers herself
him himself his how into its itself just let like more most much must myself nor not now off once only
other our ours ourselves out over own same she should some such than that the their theirs them themselves
then there these they this those through too under until very was were what when where which while who
whom why will with would you your yours yourself yourselves yes okay please thanks thank tell explain
give another example mean means meaning know think want need help understand again really something
lecture lectures chapter professor guest part lesson course topic start continue
""".split())


class UnsupportedFile(ValueError):
    pass


class EmptyDocument(ValueError):
    pass


def extract_pages(filename: str, data: bytes) -> tuple[list[tuple[int | None, str]], int | None]:
    """Return ([(page_number or None, text)], page count) for a supported file. Pages without text are left
    out of the list but still counted; text files have no page count (None)."""
    name = filename.lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
        page_count = len(pages)
    elif name.endswith((".txt", ".md")):
        pages, page_count = [(None, _decode(data))], None
    else:
        raise UnsupportedFile(f"Unsupported file type. Use one of: {', '.join(SUPPORTED_EXTENSIONS)}")

    pages = [(n, _clean(t)) for n, t in pages]
    pages = [(n, t) for n, t in pages if t]
    if not pages:
        raise EmptyDocument("No text found. Scanned PDFs need OCR before they can be imported.")
    return pages, page_count


def _decode(data: bytes) -> str:
    """UTF-8, then UTF-16 when the file starts with its byte-order mark, then Windows Arabic (cp1256).
    Measured: cp1256 maps all 256 byte values, so it never fails and is the last step (a latin-1 step
    after it could never run)."""
    encodings = ["utf-8-sig"]
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings.append("utf-16")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1256")


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"-\n(\w)", r"\1", text)      # re-join words hyphenated across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_pages(pages: list[tuple[int | None, str]], words: int = CHUNK_WORDS,
                overlap: int = CHUNK_OVERLAP) -> list[tuple[int | None, str]]:
    """Split each page into overlapping word windows so every passage keeps its page number."""
    step = words - overlap
    chunks = []
    for page, text in pages:
        tokens = text.split()
        for start in range(0, len(tokens), step):
            piece = tokens[start:start + words]
            # Skip a tail that is fully contained in the previous window
            if start > 0 and len(piece) <= overlap:
                break
            chunks.append((page, " ".join(piece)))
    return chunks


def has_arabic(text: str) -> bool:
    return bool(_ARABIC.search(text))


def keywords(text: str, limit: int = 12) -> list[str]:
    """English content words from text, in order of first appearance."""
    seen: dict[str, None] = {}
    for match in _WORD.finditer(text):
        word = match.group().lower().strip("'-")
        if len(word) >= 3 and word not in STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)[:limit]


def fts_query(words: list[str]) -> str | None:
    """An FTS5 OR-query; each term is quoted so user text can never inject FTS syntax."""
    terms = []
    for w in words:
        w = re.sub(r"[^A-Za-z' -]", " ", w).strip()
        if w:
            terms.append('"' + w.replace('"', "") + '"')
    return " OR ".join(terms) if terms else None
