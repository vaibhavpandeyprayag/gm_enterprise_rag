# GenRAG — End-to-End RAG Flow (Viva Summary)

Here is the full end-to-end flow, written so it can be walked through step by step.

---

# Architecture at a glance

```
React UI  ──HTTP──>  FastAPI  ──>  Retrieval (Atlas Vector Search)
                       │                    │
                       │            numbered context + history
                       │                    │
                       └──>  Prompt assembly  ──>  Groq LLM  ──>  Answer + citations  ──>  MongoDB Atlas
```

Two independent pipelines: **ingestion** (upload → chunk → embed → store) and **chat** (message → embed → retrieve → prompt → LLM answer → cite → persist).

---

# Phase A — Application startup

### A1. Load configuration
- **What:** Read all settings (Mongo URI, Jina key/model, Groq key/model, RAG tuning) from the project-root `.env`.
- **How:** A frozen dataclass `Settings` populated by `python-dotenv` (`load_dotenv`) + `os.getenv`, cached with `functools.lru_cache`.
- **Library:** `python-dotenv`.
- **Output:** One immutable `Settings` object (`backend/app/config.py`).
- **File:** `backend/app/config.py`

### A2. Build the API application
- **What:** Create the FastAPI app, register CORS, mount the three routers, add `/api/health`, and (in production) serve the built React bundle.
- **How:** `create_app()` factory; `CORSMiddleware`; `app.include_router(...)`; `StaticFiles(html=True)` mounted at `/` last so `/api/*` wins.
- **Library:** `fastapi` (+ `fastapi.middleware.cors`, `fastapi.staticfiles`).
- **Output:** A runnable ASGI app (`app`).
- **File:** `backend/app/main.py:34`

### A3. Startup hook (DB + indexes)
- **What:** Verify Atlas connectivity, create regular indexes, and ensure the Atlas Vector Search index exists.
- **How:** `lifespan` async context manager runs `mongo.ping()` → `ensure_indexes()` → `ensure_vector_index()`. The vector index is created via the helper; if the existing index dimension ≠ configured (1024), it is dropped and rebuilt (guards against stale embedding models).
- **Library:** `pymongo`; `langchain_mongodb.index.create_vector_search_index`.
- **Output:** 3 collections (`files`, `chunks`, `conversations`) + Atlas vector index on `chunks.embedding` (cosine, 1024-dim).
- **File:** `backend/app/main.py:22`, `backend/app/services/mongo.py:78`

---

# Phase B — Document ingestion (the "write" side)
### B1. Upload from the UI
- **What:** User picks files in the Files panel; the browser POSTs them as multipart.
- **How:** `FormData` with `files` field → `fetch('/api/files/upload')`.
- **Library:** native `fetch` / `FormData` (React + TS).
- **Output:** HTTP request to the backend.
- **File:** `frontend/src/api/client.ts:50`, `frontend/src/components/FilesPanel.tsx`

### B2. Validate the batch
- **What:** Accept one or more files; reject unsupported types or empty files without failing the whole batch.
- **How:** `UploadFile` list; extension checked against `settings.supported_types` (`.pdf .docx .txt .md .csv .xlsx .pptx`); per-file `try/except` collects errors.
- **Library:** `fastapi` (`UploadFile`, `File`), `pydantic`.
- **Output:** `data: bytes` per valid file; `errors[]` for bad ones.
- **File:** `backend/app/api/files.py:42`

### B3. Type-aware loading + splitting
- **What:** Convert raw bytes into clean, semantically-coherent text chunks, using the right strategy per format.
- **How:** `load_and_split()` dispatches by extension:
  - **PDF** → PyMuPDF extracts per-page text, then `RecursiveCharacterTextSplitter`, keeping `page` in metadata.
  - **DOCX** → `python-docx` paragraphs split recursively; tables split **row-group-wise** with the header repeated in every chunk.
  - **TXT** → decode (utf-8 → utf-8-sig → cp1252 → latin-1 fallback) then recursive split.
  - **Markdown** → `MarkdownTextSplitter` (keeps headings/code blocks intact).
  - **CSV** → `pandas.read_csv`, rows grouped into ~900-char chunks (rows never split across chunks).
  - **XLSX** → `pandas.read_excel(sheet_name=None)`, per-sheet row-grouping.
  - **PPTX** → `python-pptx` slide text + embedded tables, recursive split per slide.
- **Chunking params:** size 1200 chars, overlap 150.
- **Library:** `langchain-text-splitters`, `PyMuPDF`, `python-docx`, `python-pptx`, `pandas`, `openpyxl`.
- **Output:** `list[Document]` (LangChain), each with `page_content` + metadata (`source`, and `page`/`slide`/`sheet`/`table`/`rows`/`section`).
- **File:** `backend/app/services/ingestion.py`

### B4. Replace duplicates
- **What:** Re-uploading the same filename replaces the old version instead of duplicating.
- **How:** Look up `files` by `filename`; delete old chunks (`delete_many({"metadata.file_id": ...})`) and the old record.
- **Library:** `pymongo`.
- **Output:** Clean slate for that filename.
- **File:** `backend/app/api/files.py:68`

### B5. Embed the chunks
- **What:** Turn each chunk into a 1024-dimension vector.
- **How:** A `JinaEmbeddings` subclass of LangChain's `OpenAIEmbeddings` is pointed at Jina's OpenAI-compatible endpoint; it injects Jina's `task="retrieval.passage"` for documents. `check_embedding_ctx_length=False` guarantees raw text (not token IDs) is sent.
- **Library:** `langchain-openai` (`OpenAIEmbeddings`), Jina API (`jina-embeddings-v3`).
- **Output:** `list[float]` of length 1024 per chunk.
- **File:** `backend/app/services/embeddings.py`

### B6. Store chunks + vectors
- **What:** Persist each chunk's text, embedding, and metadata.
- **How:** `MongoDBAtlasVectorSearch.add_documents()` — LangChain batches (32) the embed call and inserts documents; `file_id` is injected into metadata so deletes stay a single query.
- **Library:** `langchain-mongodb` (`MongoDBAtlasVectorSearch`), `pymongo`.
- **Output:** Documents in `chunks` = `{text, embedding[1024], metadata{source, file_id, page/…}}`.
- **File:** `backend/app/services/vectorstore.py:35`

### B7. Save the file record
- **What:** Register the file so the UI can list/delete it.
- **How:** Insert into `files` with `file_id`, filename, size, chunk count, upload time; return `FileRecord` JSON.
- **Library:** `pymongo`, `pydantic`.
- **Output:** `UploadResponse{uploaded[], errors[]}` → UI refresh.
- **File:** `backend/app/api/files.py`

### B8. Add a web URL as a source
- **What:** A URL can be added alongside files; its readable text is rendered, cleaned, chunked, and indexed exactly like a document.
- **How:** `POST /api/files/url {url}` → `ingestion.load_url()`:
  - Playwright drives headless Chromium to load the page (`domcontentloaded` + a settle delay, so JavaScript-built content is present), and returns the fully-rendered HTML. It falls back to a plain `requests` download if Playwright is unavailable,
  - BeautifulSoup then strips `script/style/nav/header/footer/form/svg`, prefers `<article>`/`<body>`, and collapses blank lines,
  - the text is split with the same recursive splitter; each chunk keeps `source` (page `<title>` or URL) and `url` in metadata (so citations link back).
  The title is stored as the source `filename`, and `kind="url"` marks it in the UI.
- **Library:** `playwright` (render), `beautifulsoup4` (extract), `requests` (fallback), `langchain-text-splitters`.
- **Output:** Indexed chunks + a `FileRecord(kind="url", url=...)`.
- **File:** `backend/app/services/ingestion.py`, `backend/app/api/files.py`

> Note: **no LLM call happens during ingestion** — only embeddings. Cheap and deterministic. URL pages are rendered at *ingest* time only; there is no live web search at query time.

---

# Phase C — Chat / RAG (the "read" side)

### C1. Send a message
- **What:** User types a question.
- **How:** `POST /api/chat {message, conversation_id}`.
- **Library:** native `fetch`.
- **Output:** JSON request.
- **File:** `frontend/src/api/client.ts:43`, `frontend/src/components/Composer.tsx`

### C2. Resolve conversation + load state
- **What:** Find the existing chat (or create one) and load its message history.
- **How:** Upsert into `conversations`; read `messages` for recent history.
- **Library:** `pymongo`.
- **Output:** `history[]`.
- **File:** `backend/app/api/chat.py:43`

### C3. Embed the query
- **What:** Convert the message into a query vector using the **same** model as ingestion.
- **How:** `JinaEmbeddings.embed_query()` with `task="retrieval.query"` (asymmetric retrieval: passage vs query).
- **Library:** `langchain-openai`, Jina.
- **Output:** 1024-dim query vector.
- **File:** `backend/app/services/embeddings.py`

### C4. Vector retrieval
- **What:** Find the most relevant chunks.
- **How:** `MongoDBAtlasVectorSearch.similarity_search_with_score()` runs Mongo's `$vectorSearch`; cosine similarity is normalized to [0,1].
- **Library:** `langchain-mongodb`, `pymongo`.
- **Output:** `[(Document, score)]` (k = 6 by default).
- **File:** `backend/app/services/vectorstore.py:43`

### C5. Build numbered context
- **What:** Turn hits into a citable, size-bounded context.
- **How:** Drop hits below `retrieval_min_score` (0.30); number each block `[1] filename (page: N)`; stop at `max_context_chars` (12000). Table row/sheet/slide locators come from metadata.
- **Library:** pure Python.
- **Output:** `context` string + `sources[]` (for UI chips).
- **File:** `backend/app/services/rag.py:124`

### C6. Prompt assembly
- **What:** Compose the system + user prompts with context, history, and the question.
- **How:** `SYSTEM_PROMPT` (documents-only answering, inline `[n]` citations, refusal wording, table formatting, safety, privacy) + `USER_PROMPT_TEMPLATE` filled with `context`, the last 10 messages, and the question.
- **Library:** `langchain-core` (`SystemMessage`, `HumanMessage`).
- **Output:** Two chat messages ready to send.
- **File:** `backend/app/prompts.py`, `backend/app/services/rag.py`

### C7. Generate the answer
- **What:** Produce the grounded answer.
- **How:** `ChatOpenAI` pointed at Groq's OpenAI-compatible endpoint, model `openai/gpt-oss-120b`, `temperature=0.2`, `timeout=120`; a single `.invoke(messages)` call. There is no intent classification or structured output — every message is answered from the retrieved documents.
- **Library:** `langchain-openai` (`ChatOpenAI`), Groq API.
- **Output:** Response content → normalized to plain text.
- **File:** `backend/app/services/llm.py`, `backend/app/services/rag.py`

### C8. Citation resolution
- **What:** Show only the sources the answer actually used, numbered to match the text.
- **How:** Collect the `[n]` / `【n】` markers in the answer, keep only those sources, drop duplicates, and **re-number** them sequentially — the answer's citation numbers are rewritten to match so the inline markers and the chip list always agree. If the model refuses ("I could not find that…"), return **no** sources; if it forgot to cite a grounded answer, fall back to the top-3 retrieved sources.
- **Library:** Python `re`.
- **Output:** `RagResult{answer, sources[]}`.
- **File:** `backend/app/services/rag.py`

---

# Phase D — Persistence & rendering

### D1. Persist both turns
- **What:** Save the user + assistant messages.
- **How:** `$push` both messages (assistant message carries `sources`) and `$set` `updated_at`.
- **Library:** `pymongo`.
- **Output:** Updated conversation document.
- **File:** `backend/app/api/chat.py:82`

### D2. Render in the UI
- **What:** Show the answer and its citations.
- **How:** Assistant text rendered as Markdown; source chips shown **only if `sources.length > 0`**.
- **Library:** React, `react-markdown`, TypeScript.
- **Output:** Chat bubble + optional "Sources" chips.
- **File:** `frontend/src/components/MessageBubble.tsx:73`

---

# Phase E — Authentication & multi-tenancy

### E1. Sign up / sign in
- **What:** A user creates an account or signs in with username + password.
- **How:** `POST /api/auth/register` and `POST /api/auth/login` (`backend/app/api/auth.py`). The password is hashed with **bcrypt** (cost 12) and never stored in plain text; a missing user burns the same CPU time as a wrong password so accounts cannot be probed by timing.
- **Library:** `bcrypt`, `fastapi`, `pydantic`.
- **Output:** A `users` document `{user_id, username, password_hash, created_at}` + an open session.
- **File:** `backend/app/services/auth.py`

### E2. The 24-hour session
- **What:** The session survives a browser restart and ends automatically after 24 hours.
- **How:** A random token (`secrets.token_urlsafe(32)`) is stored in a `sessions` collection with `expires_at = now + 24h` and handed to the browser in an **HttpOnly** cookie (`SameSite=Lax`, `Secure` behind HTTPS). A MongoDB **TTL index** on `expires_at` purges the session row automatically. `SESSION_TTL_HOURS` makes the lifetime configurable.
- **Library:** `secrets`, `pymongo`.
- **Output:** `sessions` document `{token, user_id, created_at, expires_at}`.
- **File:** `backend/app/services/auth.py`, `backend/app/config.py`

### E3. The auth gate
- **What:** Every knowledge endpoint requires a valid session.
- **How:** A FastAPI dependency (`get_current_user`) reads the cookie (or an `Authorization: Bearer` header), resolves the session, and raises **401** when it is missing or expired. The frontend treats a 401 as "session expired" and returns to the login screen.
- **Library:** `fastapi` (`Depends`).
- **Output:** A `dict` with `user_id` + `username` injected into every route.
- **File:** `backend/app/api/deps.py`

### E4. Per-user data
- **What:** Two users never see each other's documents, chunks or chats.
- **How:** `files`, `chunks` and `conversations` all carry `user_id`; every query filters on it (read *and* delete). Chunks get `user_id` as a top-level field, and the Atlas Vector Search index declares it as a **filter** field, so retrieval runs `$vectorSearch` with `pre_filter={"user_id": {"$eq": ...}}`.
- **Library:** `pymongo`, `langchain-mongodb`.
- **Output:** Strictly per-user knowledge bases and chats.
- **File:** `backend/app/services/vectorstore.py`, `backend/app/services/mongo.py`

### E5. The UI
- **What:** The RAG workspace only opens for a signed-in user.
- **How:** `AuthProvider` checks `/api/auth/me` on startup and holds the session state; `App` renders the login screen when there is no session and the chat workspace when there is one. A timer logs the user out exactly when the server-side session expires, and the sidebar shows the account plus a logout button.
- **Library:** React (context + hooks).
- **Output:** Login / register screen, then the ChatGPT-style workspace.
- **File:** `frontend/src/auth/AuthContext.tsx`, `frontend/src/pages/LoginPage.tsx`, `frontend/src/pages/ChatPage.tsx`

---

# Provider & library map (quick reference)

| Concern | Choice | Library / API |
|---|---|---|
| API server | FastAPI + Uvicorn | `fastapi`, `uvicorn` |
| Validation | Pydantic models | `pydantic` |
| Config | root `.env` | `python-dotenv` |
| Database | MongoDB Atlas | `pymongo` |
| Vector store | Atlas Vector Search | `langchain-mongodb` |
| Embeddings | Jina `jina-embeddings-v3`, 1024-d | `langchain-openai` → Jina API |
| LLM | Groq `openai/gpt-oss-120b` | `langchain-openai` → Groq API |
| Chunking | type-aware | `langchain-text-splitters` |
| Parsers | PDF/DOCX/PPTX/XLSX/CSV | `PyMuPDF`, `python-docx`, `python-pptx`, `pandas`, `openpyxl` |
| URL ingestion | render JS pages + clean (at ingest time) | `playwright` (Chromium), `beautifulsoup4`, `requests` (fallback) |
| Auth | username/password, bcrypt + 24h cookie session | `bcrypt`, `fastapi` (`Depends`) |
| Multi-tenancy | `user_id` on files/chunks/conversations + Atlas filter | `pymongo`, `langchain-mongodb` |
| Frontend | React + Vite + TS | `react`, `react-markdown`, `remark-gfm` |

---

# Good talking points / likely viva questions

- **Why two embedding tasks?** Jina v3 is asymmetric — `retrieval.passage` for chunks, `retrieval.query` for questions — which improves match quality.
- **Why type-aware splitting?** Tables must stay row-intact with repeated headers; PDFs need page metadata for citations; Markdown must not split headings/code.
- **What does the assistant answer from?** Only the ingested sources — uploaded documents *and* web pages added as URLs. There is no live web search/scraping at query time; URLs are rendered once at ingest time (Playwright headless Chromium + BeautifulSoup), then chunked and embedded like documents.
- **Cost/latency trade-off?** One LLM call per turn, plus one embedding call for the query. No routing/classification overhead.
- **How are hallucinations bounded?** Grounding rule + strict refusal wording + only-cited-sources display + low temperature. Yes/no questions ("Do they offer X?") get a definite negative ("No — the sources don't mention it", citing what *is* offered); open questions with no match get the stock refusal line.
- **How is consistency maintained?** Retrieval uses the same 1024-dim model as ingestion; startup rebuilds the Atlas index if the dimension ever changes.
- **How are users kept apart?** Every record carries `user_id`; MongoDB queries filter on it and the vector search uses an Atlas `pre_filter` on the indexed `user_id` filter field, so a user can only ever retrieve their own chunks.
