import base64
import hashlib
import io
import logging
from collections.abc import Iterable
from threading import Lock

from pypdf import PdfReader

logger = logging.getLogger(__name__)

REQUESTS_PER_MINUTE = 30
DEFAULT_DELAY_SECONDS = 60.0 / REQUESTS_PER_MINUTE


class PDFIngestionService:
    _last_call_time = 0.0
    _lock = Lock()

    def __init__(
        self,
        max_file_bytes: int = 20 * 1024 * 1024,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        vector_store=None,
    ):
        self.max_file_bytes = max_file_bytes
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.vector_store = vector_store  # Optional DI

    # -------- Public API --------

    def extract_text_and_chunks(
        self,
        *,
        pdf_base64: str,
        user_id: str,
        filename: str | None = None,
    ) -> dict[str, any]:
        pdf_bytes = self._decode_pdf_base64(pdf_base64)
        self._validate_pdf(pdf_bytes)

        reader = PdfReader(io.BytesIO(pdf_bytes))
        meta = reader.metadata or {}
        # pypdf exposes friendly properties like meta.title (may be None)
        title = getattr(meta, "title", None) or meta.get("/Title")

        # Canonical IDs & names
        document_hash = hashlib.sha256(pdf_bytes).hexdigest()
        source_id = self._sha1_short(pdf_bytes)  # stable id for the whole doc
        source_machine = self._normalize_machine_name(filename or title or "Unknown Document")
        source_display = self._normalize_display_name(title, filename)

        full_text: list[str] = []
        chunks: list[dict] = []

        for page_idx, page in enumerate(reader.pages, start=1):  # 1-based
            text = page.extract_text() or ""
            full_text.append(text)

            for local_idx, (chunk, start, end) in enumerate(self._chunk_text_with_spans(text)):
                chunks.append(
                    {
                        # Retrieval text
                        "content": chunk,
                        # Stable identity
                        "source_id": source_id,
                        "source": source_machine,  # machine-friendly
                        "source_display": source_display,  # human-friendly
                        # Provenance
                        "page": page_idx,  # 1-based
                        "page_char_start": start,
                        "page_char_end": end,
                        "chunk_index": local_idx,
                        # Access control / ownership
                        "user_id": user_id,
                        # Optional debugging
                        "document_hash": document_hash,
                        "filename": filename,
                    }
                )

        return {
            "text": "\n".join(full_text),
            "chunks": chunks,
            "metadata": {
                "user_id": user_id,
                "hash": document_hash,
                "page_count": len(reader.pages),
                "chunk_count": len(chunks),
                "source_id": source_id,
                "source": source_machine,
                "source_display": source_display,
            },
        }

    # -------- Internals --------

    def _decode_pdf_base64(self, pdf_base64: str) -> bytes:
        try:
            return base64.b64decode(pdf_base64)
        except Exception as e:
            raise ValueError("Invalid base64-encoded PDF") from e

    def _validate_pdf(self, pdf_bytes: bytes):
        if len(pdf_bytes) > self.max_file_bytes:
            raise ValueError("PDF too large (max 20MB)")
        if not pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("Not a valid PDF file")

    def _chunk_text_with_spans(self, text: str) -> Iterable[tuple[str, int, int]]:
        """Yield (chunk, start, end) using sliding window with overlap."""
        n = len(text)
        if n == 0:
            return []
        start = 0
        out = []
        while start < n:
            end = min(n, start + self.chunk_size)
            out.append((text[start:end], start, end))
            new_start = end - self.chunk_overlap
            start = new_start if new_start > start else end
        return out

    def _sha1_short(self, data: bytes, n: int = 12) -> str:
        return hashlib.sha1(data).hexdigest()[:n]

    def _normalize_machine_name(self, raw: str | None) -> str:
        if not raw:
            return "unknown_document"
        name = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        # strip extension
        if "." in name:
            name = name.rsplit(".", 1)[0]
        return "_".join(x for x in name.strip().lower().replace(" ", "_") if x)

    @staticmethod
    def _normalize_display_name(title: str | None, filename: str | None) -> str:
        # Prefer clean PDF title if present and not generic; else derive from filename
        logger.info(f"Normalizing display name from title='{title}' and filename='{filename}'")

        GENERIC = {"untitled", "document", "unknown", "pdf"}
        t = (title or "").strip()
        if t and t.strip().lower() not in GENERIC:
            return t
        base = (filename or "Unknown Document").strip()
        # remove extension if present
        if "." in base:
            base = base.rsplit(".", 1)[0]
        return base
