"""Type-aware ingestion for files AND web pages.

Each supported format gets an appropriate loading + splitting strategy:

  pdf        -> page-aware recursive splitting (page metadata kept for citations)
  docx       -> paragraph recursive splitting; tables split row-wise
  txt        -> recursive character splitting
  markdown   -> markdown-aware splitter (keeps headings/code blocks intact)
  csv/excel  -> row-group splitting (header repeated per chunk, rows stay intact)
  pptx       -> slide-aware splitting (slide metadata kept)
  url        -> render the page with Playwright (so JavaScript-built content is
                present), then clean the final HTML with BeautifulSoup and split
                the readable text (recursive splitting)

Anything without special structure falls back to the same recursive splitter.
"""

from __future__ import annotations

import io
import os

import pandas as pd
import numpy as np
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownTextSplitter, RecursiveCharacterTextSplitter
from io import BytesIO
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from app.config import get_settings

# Shared chunking parameters
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150

# Max characters of table rows grouped into a single chunk
TABLE_CHUNK_BUDGET = 900


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _text_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def _md_splitter() -> MarkdownTextSplitter:
    return MarkdownTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)


def extension_of(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def is_supported(filename: str) -> bool:
    return extension_of(filename) in get_settings().supported_types


def _decode_text(data: bytes) -> str:
    # utf-8 first, then a lenient fallback so odd encodings never crash ingestion
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _split_plain_text(text: str, metadata: dict, md: bool = False) -> list[Document]:
    splitter = _md_splitter() if md else _text_splitter()
    return [
        Document(page_content=chunk, metadata=dict(metadata))
        for chunk in splitter.split_text(text)
        if chunk.strip()
    ]


def _cell(value) -> str:
    """Render one table cell as a compact string."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if pd.isna(value):
        return ""
    return " ".join(str(value).split())


def _split_table(
    headers: list[str],
    rows: list[list],
    metadata: dict,
) -> list[Document]:
    """Group data rows into self-contained chunks (header repeated in each).

    Rows are never split across chunks, so tabular data stays interpretable.
    """
    header_line = " | ".join(str(h) for h in headers)
    documents: list[Document] = []
    lines: list[str] = []
    size = 0
    start_row = 1

    def flush(end_row: int) -> None:
        if len(lines) <= 1:  # header only - nothing useful
            return
        documents.append(
            Document(
                page_content="\n".join(lines),
                metadata={**metadata, "rows": f"{start_row}-{end_row}"},
            )
        )

    for index, row in enumerate(rows, start=1):
        line = f"Row {index}: " + " | ".join(_cell(v) for v in row)
        if size + len(line) > TABLE_CHUNK_BUDGET and lines:
            flush(index - 1)
            lines, size = [], 0
            start_row = index
        lines.append(line)
        size += len(line) + 1

    flush(len(rows))
    return documents


def _split_dataframe(df: pd.DataFrame, metadata: dict) -> list[Document]:
    headers = [str(c) for c in df.columns]
    rows = df.astype(object).where(pd.notna(df), "").values.tolist()
    return _split_table(headers, rows, metadata)


# --------------------------------------------------------------------------
# Per-format loaders
# --------------------------------------------------------------------------
def _load_pdf(data: bytes, filename: str) -> list[Document]:
    import fitz  # PyMuPDF

    documents: list[Document] = []
    ocr = RapidOCR()

    with fitz.open(stream=data, filetype="pdf") as pdf:
        for page_number, page in enumerate(pdf, start=1):
            metadata = {"source": filename, "page": page_number}

            # 1. Detect tables and convert each row into a self-contained record.
            table_bboxes = []

            try:
                found_tables = page.find_tables().tables
            except Exception:
                found_tables = []

            for table_index, table in enumerate(found_tables):
                table_bbox = fitz.Rect(table.bbox)
                table_bboxes.append(table_bbox)

                try:
                    rows = table.extract()
                    if not rows:
                        continue

                    # Obtain column names from the detected table header.
                    headers = [
                        str(h).strip() if h is not None else ""
                        for h in table.header.names
                    ]

                    # If the header is part of the extracted rows, remove it.
                    if (
                        not table.header.external
                        and rows
                        and [str(c or "").strip() for c in rows[0]]
                        == headers
                    ):
                        rows = rows[1:]

                    for row_index, row in enumerate(rows, start=1):
                        if not row:
                            continue

                        # Ignore completely empty rows.
                        values = [
                            str(cell).strip() if cell is not None else ""
                            for cell in row
                        ]

                        if not any(values):
                            continue

                        # Build self-contained records.
                        fields = []
                        for column_index, value in enumerate(values):
                            if not value:
                                continue

                            label = (
                                headers[column_index]
                                if column_index < len(headers)
                                and headers[column_index]
                                else f"Column {column_index + 1}"
                            )
                            fields.append(f"{label}: {value}")

                        if not fields:
                            continue

                        table_text = "\n".join(fields)

                        table_metadata = {
                            **metadata,
                            "content_type": "table_row",
                            "table_index": table_index,
                            "row_index": row_index,
                        }

                        documents.extend(
                            _split_plain_text(table_text, table_metadata)
                        )

                except Exception as exc:
                    print(
                        f"Table extraction failed on page {page_number} "
                        f"of {filename}: {exc}"
                    )

            # 2. Extract ordinary text, excluding blocks inside detected tables.
            text_blocks = page.get_text("blocks")
            plain_text_parts = []

            for block in text_blocks:
                # Text blocks contain coordinates and block type.
                if len(block) < 7 or block[6] != 0:
                    continue

                block_bbox = fitz.Rect(block[:4])
                block_text = block[4].strip()

                if not block_text:
                    continue

                # Avoid extracting table contents twice.
                overlaps_table = any(
                    block_bbox.intersects(table_bbox)
                    and (
                        block_bbox & table_bbox
                    ).get_area() / max(block_bbox.get_area(), 1) > 0.5
                    for table_bbox in table_bboxes
                )

                if not overlaps_table:
                    plain_text_parts.append(block_text)

            text = "\n".join(plain_text_parts).strip()

            if text:
                documents.extend(_split_plain_text(text, metadata))

            # 3. OCR images, preserving the existing implementation.
            page_dict = page.get_text("dict")

            for block in page_dict["blocks"]:
                if block.get("type") != 1:
                    continue

                image_bytes = block.get("image")
                if not image_bytes:
                    continue

                try:
                    with Image.open(BytesIO(image_bytes)) as image:
                        image = image.convert("RGB")
                        image_array = np.array(image)
                        result, _ = ocr(image_array)

                    if not result:
                        continue

                    image_text = "\n".join(
                        str(item[1]).strip()
                        for item in result
                        if len(item) >= 2 and str(item[1]).strip()
                    )

                    if image_text:
                        image_metadata = {
                            **metadata,
                            "content_type": "image_ocr",
                            "image_bbox": block.get("bbox"),
                        }

                        documents.extend(
                            _split_plain_text(image_text, image_metadata)
                        )

                except Exception as exc:
                    print(
                        f"OCR failed on page {page_number} "
                        f"of {filename}: {exc}"
                    )

    return documents


def _load_docx(data: bytes, filename: str) -> list[Document]:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    documents: list[Document] = []

    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    if paragraphs:
        documents.extend(
            _split_plain_text(
                "\n\n".join(paragraphs), {"source": filename, "section": "body"}
            )
        )

    for table_index, table in enumerate(document.tables, start=1):
        matrix = [[cell.text for cell in row.cells] for row in table.rows]
        if not matrix:
            continue
        documents.extend(
            _split_table(
                matrix[0],
                matrix[1:],
                {"source": filename, "table": table_index},
            )
        )
    return documents


def _load_txt(data: bytes, filename: str) -> list[Document]:
    return _split_plain_text(_decode_text(data), {"source": filename})


def _load_markdown(data: bytes, filename: str) -> list[Document]:
    return _split_plain_text(_decode_text(data), {"source": filename}, md=True)


def _load_csv(data: bytes, filename: str) -> list[Document]:
    df = pd.read_csv(io.BytesIO(data))
    return _split_dataframe(df, {"source": filename})


def _load_excel(data: bytes, filename: str) -> list[Document]:
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)  # dict[name -> df]
    documents: list[Document] = []
    for sheet_name, df in sheets.items():
        if df.empty:
            continue
        documents.extend(
            _split_dataframe(df, {"source": filename, "sheet": str(sheet_name)})
        )
    return documents


def _load_pptx(data: bytes, filename: str) -> list[Document]:
    from pptx import Presentation

    presentation = Presentation(io.BytesIO(data))
    documents: list[Document] = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    parts.append(text)
            if getattr(shape, "has_table", False):  # tables inside slides
                table = shape.table
                header = [cell.text for cell in table.rows[0].cells]
                body = [
                    [cell.text for cell in row.cells] for row in list(table.rows)[1:]
                ]
                parts.append(
                    "\n".join(
                        [
                            " | ".join(str(h) for h in header),
                            *[f"Row {i}: " + " | ".join(_cell(v) for v in r) for i, r in enumerate(body, 1)],
                        ]
                    )
                )
        text = "\n\n".join(parts).strip()
        if text:
            documents.extend(
                _split_plain_text(text, {"source": filename, "slide": slide_number})
            )
    return documents


_LOADERS = {
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".txt": _load_txt,
    ".md": _load_markdown,
    ".csv": _load_csv,
    ".xlsx": _load_excel,
    ".pptx": _load_pptx,
}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def load_and_split(data: bytes, filename: str) -> list[Document]:
    """Load raw file bytes and return type-aware LangChain Documents.

    Raises ValueError for unsupported formats or empty content.
    """
    extension = extension_of(filename)
    loader = _LOADERS.get(extension)
    if loader is None:
        raise ValueError(
            f"Unsupported file type '{extension}'. "
            f"Supported: {', '.join(sorted(get_settings().supported_types))}"
        )

    documents = loader(data, filename)
    documents = [doc for doc in documents if doc.page_content.strip()]
    if not documents:
        raise ValueError("No extractable text found in the file")
    return documents


# --------------------------------------------------------------------------
# Web page ingestion (URL source)
# --------------------------------------------------------------------------
# HTML elements that carry no useful content for retrieval.
_STRIP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "form", "svg")


def fetch_url_text(url: str):
    """Render a web page and return (title, readable_text).

    JavaScript-built pages (React/Vue/etc.) don't expose their content in the
    raw HTML that `requests` downloads - it only appears after the browser runs
    the scripts. So we drive a headless Chromium via Playwright to get the
    fully-rendered DOM, then hand that HTML to BeautifulSoup to keep only the
    human-readable text (scripts/styles/navigation stripped).

    Falls back to a plain `requests` download if Playwright is unavailable.
    Raises ValueError on bad input or empty pages.
    """
    html = _render_html(url)
    title, text = _html_to_text(html)
    if not text.strip():
        raise ValueError("no readable text found at the URL")
    return title, text


def _render_html(url: str) -> str:
    """Return the fully-rendered HTML for a URL (Playwright, requests fallback)."""
    try:
        return _render_with_playwright(url)
    except ImportError:
        print("[ingestion] Playwright not installed - falling back to requests")
        return _fetch_with_requests(url)


def _render_with_playwright(url: str) -> str:
    """Load the URL in headless Chromium and return the rendered HTML.

    Many sites (WordPress/React/Next.js) inject their real content via a
    client-side API call *after* the initial HTML. So we: load the DOM, then
    best-effort wait for the network to go idle (that XHR/fetch settling), then
    apply a final settle delay before snapshotting the DOM.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    settings = get_settings()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (compatible; GenRAG/1.0; "
                        "+https://example.com)"
                    )
                )
                page.goto(
                    url,
                    timeout=settings.url_render_timeout_ms,
                    wait_until="domcontentloaded",
                )
                # Let client-side data XHRs finish. `networkidle` may never be
                # reached on sites with constant analytics/polling traffic, so a
                # timeout here is expected and simply means "keep going".
                try:
                    page.wait_for_load_state(
                        "networkidle", timeout=settings.url_render_timeout_ms
                    )
                except PlaywrightTimeoutError:
                    pass
                # Final settle delay for late DOM updates after the last XHR.
                page.wait_for_timeout(settings.url_render_wait_ms)
                html = page.content()
            finally:
                browser.close()
    except PlaywrightTimeoutError as exc:
        raise ValueError(f"timed out rendering URL: {exc}") from exc
    except PlaywrightError as exc:
        raise ValueError(f"could not render URL: {exc}") from exc

    if len(html.encode("utf-8", errors="ignore")) > settings.url_max_bytes:
        raise ValueError("page is too large to ingest")
    return html


def _fetch_with_requests(url: str) -> str:
    """Plain HTTP fallback (no JS execution) when Playwright isn't available."""
    import requests
    from requests.exceptions import RequestException

    settings = get_settings()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; GenRAG/1.0; +https://example.com)"}
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=settings.url_fetch_timeout_seconds,
        )
        response.raise_for_status()
    except RequestException as exc:
        raise ValueError(f"could not fetch URL: {exc}") from exc

    if len(response.content) > settings.url_max_bytes:
        raise ValueError("page is too large to ingest")
    return response.text


def _html_to_text(html: str) -> tuple[str, str]:
    """Return (title, readable_text) extracted from an HTML document."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Prefer the main article/body; fall back to the whole document.
    container = soup.find("article") or soup.body or soup
    text = container.get_text(separator="\n")
    # Collapse the runs of blank lines BeautifulSoup leaves behind.
    lines = [line.strip() for line in text.splitlines()]
    paragraphs = [line for line in lines if line]
    return title, "\n".join(paragraphs)


def normalize_url(url: str) -> str:
    """Trim whitespace and add a default scheme so short URLs work."""
    url = " ".join((url or "").split())
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def load_url(url: str) -> list[Document]:
    """Fetch a URL and return its content as split LangChain Documents.

    Each chunk carries `source` (the page title or URL) and `url` in metadata so
    citations can link back to the page.
    """
    url = normalize_url(url)
    if not url:
        raise ValueError("URL is empty")

    title, text = fetch_url_text(url)
    source = title or url
    metadata = {"source": source, "url": url}
    documents = _split_plain_text(text, metadata)
    if not documents:
        raise ValueError("no extractable text found at the URL")
    return documents
