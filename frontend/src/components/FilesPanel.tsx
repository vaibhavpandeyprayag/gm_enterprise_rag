import { useRef, useState } from 'react'
import type { FileRecord } from '../types'

interface Props {
  files: FileRecord[]
  onClose: () => void
  onUpload: (files: File[]) => Promise<void>
  onAddUrl: (url: string) => Promise<void>
  onDelete: (fileId: string) => void
  /** 'upload' = show only the dropzone + URL input; 'library' = show only the list. */
  mode?: 'upload' | 'library'
}

const SUPPORTED = '.pdf, .docx, .txt, .md, .csv, .xlsx, .pptx'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Modal panel: ingest files (click or drag & drop) and web URLs, list + remove sources. */
export default function FilesPanel({
  files,
  onClose,
  onUpload,
  onAddUrl,
  onDelete,
  mode = 'upload',
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [url, setUrl] = useState('')
  const [addingUrl, setAddingUrl] = useState(false)

  const showUpload = mode === 'upload'
  const showLibrary = mode === 'library'

  const handleFiles = async (list: FileList | null) => {
    if (!list || list.length === 0) return
    setUploading(true)
    try {
      await onUpload(Array.from(list))
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const handleAddUrl = async () => {
    const value = url.trim()
    if (!value || addingUrl) return
    setAddingUrl(true)
    try {
      await onAddUrl(value)
      setUrl('')
    } finally {
      setAddingUrl(false)
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={showLibrary ? 'Knowledge Library' : 'Add knowledge sources'}
      >
        <div className="modal-head">
          <h2>{showLibrary ? 'Knowledge Library' : 'Add documents or URL'}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        {showUpload && (
          <div className="upload-section">
            <div
              className={`dropzone ${dragging ? 'dragging' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                void handleFiles(e.dataTransfer.files)
              }}
            >
              <p className="dropzone-title">{uploading ? 'Ingesting…' : 'Drag & drop files here'}</p>
              <p className="dropzone-sub">Supported: {SUPPORTED}</p>
              <button className="primary-btn" onClick={() => inputRef.current?.click()} disabled={uploading}>
                {uploading ? 'Uploading…' : 'Choose files'}
              </button>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.pptx"
                hidden
                onChange={(e) => void handleFiles(e.target.files)}
              />
            </div>

            <div className="url-add">
              <input
                className="url-input"
                type="url"
                placeholder="Add a web page URL (https://…)"
                value={url}
                disabled={addingUrl}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleAddUrl()
                }}
              />
              <button className="primary-btn" onClick={() => void handleAddUrl()} disabled={addingUrl || !url.trim()}>
                {addingUrl ? 'Fetching…' : 'Add URL'}
              </button>
            </div>
          </div>
        )}

        {showLibrary && (
          <>
          <p className="modal-foot" style={{textAlign: "start"}}>Manage your documents and web pages here.</p>
            <div className="file-list">
              {files.length === 0 && <div className="conv-empty">No sources ingested yet</div>}
              {files.map((file) => (
                <div className="file-row" key={file.file_id}>
                  <div className="file-info">
                    <div className="file-name" title={file.url || file.filename}>
                      {file.kind === 'url' && <span className="source-tag">URL</span>}
                      {file.filename}
                    </div>
                    <div className="file-meta">
                      {new Date(file.uploaded_at).toLocaleString()}
                      {file.kind !== 'url' && ` · ${formatSize(file.size_bytes)}`}
                    </div>
                  </div>
                  <button
                    className="icon-btn tiny danger"
                    aria-label={`Delete ${file.filename}`}
                    onClick={() => {
                      if (window.confirm(`Remove "${file.filename}" and its chunks?`)) onDelete(file.file_id)
                    }}
                  >
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
            
          </>
        )}
      </div>
    </div>
  )
}
