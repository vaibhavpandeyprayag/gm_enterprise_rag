"""File + URL endpoints: ingest, list, delete knowledge sources.

Every source belongs to the authenticated user: records carry `user_id` and all
queries are filtered by it, so users never see each other's documents.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services import ingestion, mongo, vectorstore
from app.services.owner import get_owner_user

router = APIRouter(prefix="/api/files", tags=["files"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class FileRecord(BaseModel):
    file_id: str
    filename: str
    size_bytes: int
    chunk_count: int
    content_type: str | None = None
    uploaded_at: datetime
    kind: str = "file"  # "file" | "url"
    url: str | None = None


class UploadResponse(BaseModel):
    uploaded: list[FileRecord] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class UrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------------
# Ingestion helper (shared by file upload and URL add)
# --------------------------------------------------------------------------
def _persist_source(
    *,
    documents: list[dict],
    filename: str,
    size_bytes: int,
    content_type: str | None,
    kind: str,
    user_id: str,
    url: str | None = None,
) -> FileRecord:
    """Replace any existing source with the same name, embed, and store it.

    `documents` is the already-split chunk list from the ingestion layer.
    The replacement only affects the same user's copy of that name.
    """
    # Re-ingesting the same name (filename or URL) replaces the old copy.
    existing = mongo.files_collection().find_one(
        {"filename": filename, "user_id": user_id}
    )
    if existing:
        vectorstore.delete_by_file(str(existing["file_id"]), user_id)
        mongo.files_collection().delete_one({"_id": existing["_id"]})

    file_id = str(uuid.uuid4())
    vectorstore.add_documents(documents, file_id=file_id, user_id=user_id)

    record = {
        "file_id": file_id,
        "filename": filename,
        "size_bytes": size_bytes,
        "chunk_count": len(documents),
        "content_type": content_type,
        "uploaded_at": _utcnow(),
        "kind": kind,
        "url": url,
        "user_id": user_id,
    }
    mongo.files_collection().insert_one({**record, "_id": file_id})
    return FileRecord(**record)


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.post("/upload", response_model=UploadResponse)
def upload_files(
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_owner_user),
) -> UploadResponse:
    """Ingest one or more files (chunk + embed + store in MongoDB Atlas).

    Files with the same name replace the previous version. A single bad file
    does not fail the whole batch - it is reported in `errors`.
    """
    user_id = user["user_id"]
    uploaded: list[FileRecord] = []
    errors: list[dict[str, str]] = []

    for upload in files:
        filename = upload.filename or "unnamed"
        try:
            if not ingestion.is_supported(filename):
                raise ValueError(
                    f"unsupported type '{ingestion.extension_of(filename)}' - "
                    f"use {', '.join(sorted(get_supported()))}"
                )

            data = upload.file.read()
            if not data:
                raise ValueError("file is empty")

            documents = ingestion.load_and_split(data, filename)  # no network
            record = _persist_source(
                documents=documents,
                filename=filename,
                size_bytes=len(data),
                content_type=upload.content_type,
                kind="file",
                user_id=user_id,
            )
            uploaded.append(record)
        except Exception as exc:  # keep batch resilient
            errors.append({"filename": filename, "error": str(exc)})

    return UploadResponse(uploaded=uploaded, errors=errors)


@router.post("/url", response_model=FileRecord)
def add_url(
    body: UrlRequest,
    user: dict = Depends(get_owner_user),
) -> FileRecord:
    """Fetch a web page, ingest its readable text, and index it for RAG.

    The page is chunked with the same splitter as text documents; each chunk
    keeps the page title (or URL) as `source` and the URL for citation links.
    """
    try:
        documents = ingestion.load_url(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    url = ingestion.normalize_url(body.url)
    title = documents[0].metadata.get("source") if documents else url
    return _persist_source(
        documents=documents,
        filename=title or url,
        size_bytes=sum(len(d.page_content) for d in documents),
        content_type="text/html",
        kind="url",
        url=url,
        user_id=user["user_id"],
    )


@router.get("", response_model=list[FileRecord])
def list_files(user: dict = Depends(get_owner_user)) -> list[FileRecord]:
    """All ingested sources of the current user (files + URLs), newest first."""
    records = (
        mongo.files_collection()
        .find({"user_id": user["user_id"]})
        .sort("uploaded_at", -1)
    )
    out = []
    for doc in records:
        doc.pop("_id", None)
        doc.pop("user_id", None)
        out.append(FileRecord(**doc))
    return out


@router.delete("/{file_id}")
def delete_file(
    file_id: str,
    user: dict = Depends(get_owner_user),
) -> dict[str, Any]:
    """Delete a source together with all of its chunks."""
    user_id = user["user_id"]
    deleted_chunks = vectorstore.delete_by_file(file_id, user_id)
    result = mongo.files_collection().delete_one(
        {"_id": file_id, "user_id": user_id}
    )
    if result.deleted_count == 0 and deleted_chunks == 0:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"file_id": file_id, "deleted_chunks": deleted_chunks}


def get_supported() -> tuple[str, ...]:
    from app.config import get_settings

    return get_settings().supported_types
