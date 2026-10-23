# General Purpose RAG

This is a general purpose RAG application/chatbot that can ingest multiple files of various types (and web page URLs) and answers queries based on the data in those sources.

### Types of files:
- PDF
- Docs
- txt
- markdown
- csv
- excel
- ppt

### Requirements
- There will be a frontend UI that looks similar to ChatGPT UI, should be able to upload files, and maintain multiple conversations/chats.
- The frontend can be in vite + react
- Answers are based only on the ingested documents and URLs (no live web search/scraping at query time).
- Web URLs can be added as a knowledge source; the page is rendered with Playwright (headless Chromium) so JavaScript-built content is captured, cleaned with BeautifulSoup, chunked, and indexed like a document.
- MongoDB Atlas is the database used for saving all things - chunks, embeddings, conversations, etc.
- Use langchain wherever can be useful.
- Use appropriate splitting technique based upon the type of data (text/table/etc) - use same technique if no special treatment is required.
- Use Groq for LLM (openai/gpt-oss-120b)
- Groq API key is given in env file, but do not call LLM for test. I will test when code is ready.
- For embedding, use Jina (jina-embeddings-v3, size=1024)
- .venv is virtual environment used to install packages for this project.
- use fastapi to build suitable api endpoints that will be hit from frontend.
- Use proper system/user prompts.
- Keep guard-rails in prompts.
- Maintain modular and configurable style of coding.
- Give comments wherever necessary.

### Single user (no login)
- There is no login or signup; the RAG page opens directly.
- Every record (`files`, `chunks`, `conversations`, banking data) still carries a `user_id`; all queries use `get_owner_user()` (`backend/app/services/owner.py`) so the data stays scoped to one account.
- The owner is `OWNER_USERNAME` from `.env` when set, otherwise the oldest account in `users`, otherwise a `local` account created on first use.
- The Atlas Vector Search index still declares `user_id` as a *filter* field - retrieval uses a `pre_filter` on `user_id`.
- When a question asks for bank details, the workflow asks for authentication first (registered email + dummy OTP).
- Chat turns run through the LangGraph workflow in `backend/app/workflow/` (intent routing, auth, slot filling, confirmation, tools). Thread id = `conversation_id`; checkpoints live in `workflow_checkpoints` / `workflow_writes`.
- Bank authorization is server-side only (`bank_sessions`, `BANK_SESSION_TTL_MINUTES`, default 15). The OTP is the static development code in `BANK_OTP_CODE` (default `123456`) and is compared in Python.
- Tests: `python -m pytest tests` from `backend/`; they use fakes + `InMemorySaver` and never call Groq or MongoDB.
- While a bank prompt is open (email, OTP, slot value, confirmation), a document question is routed to document Q&A instead and the pending bank request is reset (the 15-minute auth session is kept).
- Every backend startup resets the demo banking data (`scripts/seed_demo_data.py`): users `vp5` (`vp5@xyz.com`) and `demo` (`demo@xyz.com`), one account each, sample transactions, active debit cards and chequebook requests. Their bank sessions and prior bank records are deleted first; other users are untouched.

### Considerations
- So far there ar no image files, and document files do not contain images.
- Documents ingested before the multi-user feature have no `user_id`; assign them to an account with `python -m scripts.claim_legacy --username <name>` (run from `backend/`).