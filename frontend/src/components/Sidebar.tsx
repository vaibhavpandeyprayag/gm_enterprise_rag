import { useEffect, useRef, useState } from 'react'
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

/** Left pane: brand, new-chat action, conversation history, files entry. */
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
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
                <circle cx="12" cy="12" r="4" />
              </svg>
            </span>
            <span className="brand-name">GenRAG</span>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close sidebar">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <button className="new-chat" onClick={onNew}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
          New chat
        </button>

        <nav className="conv-section" aria-label="Conversations">
          <div className="conv-section-label">Chats</div>
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
                      <button className="conv-title" onClick={() => onSelect(conv.conversation_id)} title={conv.title}>
                        {conv.title}
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
                            if (window.confirm(`Delete chat "${conv.title}"?`)) onDelete(conv.conversation_id)
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
          <button className="files-btn" onClick={onOpenFiles}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M4 6a2 2 0 012-2h4l2 2h6a2 2 0 012 2v10a2 2 0 01-2 2H6a2 2 0 01-2-2V6z" />
            </svg>
            Knowledge files
            <span className="count">{fileCount}</span>
          </button>
        </div>
      </aside>
    </>
  )
}
