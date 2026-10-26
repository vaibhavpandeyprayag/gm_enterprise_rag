import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { ConversationSummary, FileRecord, Message } from '../types'
import Sidebar from '../components/Sidebar'
import MessageBubble from '../components/MessageBubble'
import Composer from '../components/Composer'
import FilesPanel from '../components/FilesPanel'
import BankOpsModal from '../components/BankOpsModal'
import EmptyState from '../components/EmptyState'
import ThemeToggle from '../theme/ThemeToggle'

/** Local-only flag: marks an optimistic user bubble that the server has not confirmed. */
type LocalMessage = Message & { temp?: boolean }

interface Notice {
  kind: 'error' | 'info'
  text: string
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/**
 * The RAG workspace: chat, conversations and knowledge files.
 * Reachable only for an authenticated user (see `App`).
 */
export default function ChatPage() {
  // ---- state -----------------------------------------------------------
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<LocalMessage[]>([])
  const [pending, setPending] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [files, setFiles] = useState<FileRecord[]>([])
  const [filesOpen, setFilesOpen] = useState(false)
  const [filesMode, setFilesMode] = useState<'upload' | 'library'>('upload')
  const [bankOpsOpen, setBankOpsOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)

  // ---- data loading ----------------------------------------------------
  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api.listConversations())
    } catch (error) {
      setNotice({ kind: 'error', text: `Backend unreachable: ${errorText(error)}` })
    }
  }, [])

  const refreshFiles = useCallback(async () => {
    try {
      setFiles(await api.listFiles())
    } catch {
      /* backend offline - panel simply shows an empty list */
    }
  }, [])

  useEffect(() => {
    void refreshConversations()
    void refreshFiles()
  }, [refreshConversations, refreshFiles])

  // ---- keep the newest message in view ---------------------------------
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, pending])

  // ---- actions ---------------------------------------------------------
  const selectConversation = useCallback(async (id: string) => {
    setActiveId(id)
    setSidebarOpen(false)
    setNotice(null)
    try {
      const detail = await api.getConversation(id)
      setMessages(detail.messages)
    } catch (error) {
      setNotice({ kind: 'error', text: `Could not load chat: ${errorText(error)}` })
    }
  }, [])

  const openLibrary = useCallback(() => {
    setFilesMode('library')
    setFilesOpen(true)
    setSidebarOpen(false)
  }, [])

  const openUploader = useCallback(() => {
    setFilesMode('upload')
    setFilesOpen(true)
  }, [])

  const newChat = useCallback(() => {
    setActiveId(null)
    setMessages([])
    setNotice(null)
    setSidebarOpen(false)
  }, [])

  const send = useCallback(
    async (text: string) => {
      const question = text.trim()
      if (!question || pending) return
      setNotice(null)

      // Optimistic user bubble; replaced by the server-confirmed messages on success
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: question, created_at: new Date().toISOString(), temp: true },
      ])
      setPending(true)
      try {
        const response = await api.chat(question, activeId)
        setMessages((prev) => [
          ...prev.filter((m) => !m.temp),
          response.user_message,
          response.assistant_message,
        ])
        setActiveId(response.conversation_id)
        await refreshConversations()
      } catch (error) {
        setNotice({ kind: 'error', text: errorText(error) })
      } finally {
        setPending(false)
      }
    },
    [activeId, pending, refreshConversations],
  )

  const removeConversation = useCallback(
    async (id: string) => {
      try {
        await api.deleteConversation(id)
        setConversations((prev) => prev.filter((c) => c.conversation_id !== id))
        if (id === activeId) newChat()
      } catch (error) {
        setNotice({ kind: 'error', text: errorText(error) } )
      }
    },
    [activeId, newChat],
  )

  const renameConversation = useCallback(async (id: string, title: string) => {
    try {
      await api.renameConversation(id, title)
      setConversations((prev) => prev.map((c) => (c.conversation_id === id ? { ...c, title } : c)))
    } catch (error) {
      setNotice({ kind: 'error', text: errorText(error) })
    }
  }, [])

  const handleUpload = useCallback(async (uploads: File[]) => {
    if (uploads.length === 0) return
    try {
      const result = await api.uploadFiles(uploads)
      await refreshFiles()
      if (result.errors.length > 0) {
        const details = result.errors.map((e) => `${e.filename}: ${e.error}`).join(' · ')
        setNotice({ kind: 'error', text: `Some files failed - ${details}` })
      } else if (result.uploaded.length > 0) {
        setNotice({ kind: 'info', text: `Ingested ${result.uploaded.length} file(s) into the knowledge base.` })
      }
    } catch (error) {
      setNotice({ kind: 'error', text: errorText(error) })
    }
  }, [refreshFiles])

  const handleAddUrl = useCallback(
    async (url: string) => {
      try {
        const record = await api.addUrl(url)
        await refreshFiles()
        setNotice({ kind: 'info', text: `Indexed "${record.filename}" (${record.chunk_count} chunks).` })
      } catch (error) {
        setNotice({ kind: 'error', text: errorText(error) })
      }
    },
    [refreshFiles],
  )

  const handleDeleteFile = useCallback(
    async (fileId: string) => {
      try {
        await api.deleteFile(fileId)
        setFiles((prev) => prev.filter((f) => f.file_id !== fileId))
      } catch (error) {
        setNotice({ kind: 'error', text: errorText(error) })
      }
    },
    [],
  )

  // ---- derived ---------------------------------------------------------
  const activeTitle =
    conversations.find((c) => c.conversation_id === activeId)?.title ?? 'New chat'

  // ---- render ----------------------------------------------------------
  return (
    <div className="app">
      <Sidebar
        open={sidebarOpen}
        conversations={conversations}
        activeId={activeId}
        fileCount={files.length}
        onSelect={(id) => void selectConversation(id)}
        onNew={newChat}
        onRename={(id, title) => void renameConversation(id, title)}
        onDelete={(id) => void removeConversation(id)}
        onClose={() => setSidebarOpen(false)}
        onOpenFiles={openLibrary}
      />

      <div className="main">
        <header className="topbar">
          <button className="icon-btn menu-btn" onClick={() => setSidebarOpen((v) => !v)} aria-label={sidebarOpen ? 'Close menu' : 'Open menu'}>
            {sidebarOpen ? (
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            )}
          </button>
          <div className="chat-title-group">
            <span className="chat-kicker">Nexora Bank AI</span>
            <h1 className="chat-title" title={activeTitle}>
              {activeTitle}
            </h1>
          </div>
          <div className="topbar-actions">
            <ThemeToggle />
            <button className="ghost-btn" onClick={openLibrary}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
              </svg>
              Knowledge Library
              <span className="count">{files.length}</span>
            </button>
          </div>
        </header>

        {notice && (
          <div className={`notice notice-${notice.kind}`} role="status">
            <span>{notice.text}</span>
            <button className="icon-btn tiny" onClick={() => setNotice(null)} aria-label="Dismiss">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>
        )}

        <div className="chat-scroll" ref={scrollRef}>
          <div className="chat-inner" key={activeId ?? 'new-chat'}>
            {messages.length === 0 && !pending ? (
              <EmptyState />
            ) : (
              messages.map((message, index) => <MessageBubble key={index} message={message} />)
            )}
            {pending && (
              <article className="msg msg-assistant">
                <div className="avatar" aria-hidden>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <path d="M4 10V7a2 2 0 012-2h12a2 2 0 012 2v3" />
                    <rect x="3" y="10" width="18" height="10" rx="2" />
                    <path d="M9 14h6" />
                  </svg>
                </div>
                <div className="msg-body">
                  <div className="typing" aria-label="Generating answer">
                    <span className="dot" />
                    <span className="dot" />
                    <span className="dot" />
                  </div>
                </div>
              </article>
            )}
          </div>
        </div>

        <Composer
          disabled={pending}
          onSubmit={(text) => void send(text)}
          onAttach={openUploader}
          onBankOps={() => setBankOpsOpen(true)}
        />
      </div>

      {filesOpen && (
        <FilesPanel
          files={files}
          onClose={() => setFilesOpen(false)}
          onUpload={handleUpload}
          onAddUrl={handleAddUrl}
          onDelete={(id) => void handleDeleteFile(id)}
          mode={filesMode}
        />
      )}

      {bankOpsOpen && (
        <BankOpsModal
          open={bankOpsOpen}
          onClose={() => setBankOpsOpen(false)}
          onSelect={(prompt) => void send(prompt)}
        />
      )}
    </div>
  )
}
