import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ConversationSummary } from '../types'

interface Props {
  open: boolean
  conversations: ConversationSummary[]
  activeId: string | null
  fileCount: number
  onSelect: (id: string) => void
  onNew: () => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onClose: () => void
  onOpenFiles: () => void
}

export default function Sidebar({
  open,
  conversations,
  activeId,
  fileCount,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onClose,
  onOpenFiles,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [deleteTitle, setDeleteTitle] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId) editInputRef.current?.focus()
  }, [editingId])

  const commitEdit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim())
    setEditingId(null)
  }

  return (
    <>
      <div className={`sidebar-scrim ${open ? 'visible' : ''}`} onClick={onClose} aria-hidden />
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-head">
          <div className="brand">
            <span className="brand-mark" aria-hidden>
              <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M3 10h18M5 10V7a2 2 0 012-2h10a2 2 0 012 2v3" />
                <rect x="3" y="10" width="18" height="10" rx="2" />
                <path d="M8 14h3M13 14h3" />
              </svg>
            </span>
            <div className="brand-text">
              <span className="brand-name">Nexora Bank</span>
              <span className="brand-sub">AI ASSISTANT</span>
            </div>
          </div>
          <button className="icon-btn sidebar-close" onClick={onClose} aria-label="Close sidebar">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <button className="new-chat" onClick={() => { onNew(); onClose(); }}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
          New conversation
        </button>

        <nav className="conv-section" aria-label="Conversations">
          <div className="conv-section-label">CONVERSATIONS</div>
          {conversations.length === 0 && <div className="conv-empty">No conversations yet</div>}
          <ul className="conv-list">
            {conversations.map((conv) => {
              const active = conv.conversation_id === activeId
              return (
                <li key={conv.conversation_id} className={`conv-item ${active ? 'active' : ''}`}>
                  {editingId === conv.conversation_id ? (
                    <input
                      ref={editInputRef}
                      className="conv-edit-input"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={commitEdit}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitEdit()
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      maxLength={120}
                    />
                  ) : (
                    <>
                      <button className="conv-title" onClick={() => { onSelect(conv.conversation_id); onClose(); }} title={conv.title}>
                        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
                          <path d="M4 6h16M4 12h10M4 18h14" />
                        </svg>
                        <span>{conv.title}</span>
                      </button>
                      <span className="conv-actions">
                        <button
                          className="icon-btn tiny"
                          aria-label="Rename chat"
                          onClick={() => {
                            setEditingId(conv.conversation_id)
                            setDraft(conv.title)
                          }}
                        >
                          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M4 20h4l10-10-4-4L4 16v4zM14 6l4 4" />
                          </svg>
                        </button>
                        <button
                          className="icon-btn tiny danger"
                          aria-label="Delete chat"
                          onClick={() => {
                            setDeleteId(conv.conversation_id)
                            setDeleteTitle(conv.title)
                          }}
                        >
                          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
                          </svg>
                        </button>
                      </span>
                    </>
                  )}
                </li>
              )
            })}
          </ul>
        </nav>

        <div className="sidebar-foot">
          <button className="files-btn" onClick={() => { onOpenFiles(); onClose(); }}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
            </svg>
            Knowledge Library
            <span className="count">{fileCount}</span>
          </button>
        </div>
      </aside>

      {deleteId && createPortal(
        <div className="modal-scrim" onClick={() => setDeleteId(null)} style={{ zIndex: 1000 }}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="modal-head">
              <h2>Delete conversation?</h2>
              <button className="icon-btn" onClick={() => setDeleteId(null)} aria-label="Close">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
            <p style={{ margin: 0, color: 'var(--text-dim)' }}>
              "{deleteTitle}" will be permanently removed.
            </p>
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '8px' }}>
              <button className="ghost-btn" onClick={() => setDeleteId(null)}>Cancel</button>
              <button
                className="primary-btn"
                onClick={() => {
                  if (deleteId) onDelete(deleteId)
                  setDeleteId(null)
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
