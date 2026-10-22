import { useEffect, useRef, useState } from 'react'

interface Props {
  disabled: boolean
  onSubmit: (text: string) => void
  onAttach: () => void
}

/** Bottom input area: textarea, attach + send buttons. */
export default function Composer({ disabled, onSubmit, onAttach }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-grow with the content (bounded so the chat area keeps its space)
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [value])

  const submit = () => {
    const text = value.trim()
    if (!text || disabled) return
    onSubmit(text)
    setValue('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        <button className="icon-btn" onClick={onAttach} aria-label="Manage files" title="Manage files">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M21 12.5l-8.4 8.4a5 5 0 01-7.1-7.1l8.5-8.5a3.4 3.4 0 014.8 4.8l-8.5 8.5a1.8 1.8 0 01-2.5-2.5l7.8-7.8" />
          </svg>
        </button>

        <textarea
          ref={textareaRef}
          className="composer-input"
          rows={1}
          placeholder="Ask anything about your files…"
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />

        <button
          className="send-btn"
          onClick={submit}
          disabled={disabled || !value.trim()}
          aria-label="Send message"
          title="Send"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </button>
      </div>

      <div className="composer-meta">
        <span className="composer-hint">Enter to send · Shift+Enter for a new line</span>
      </div>
    </div>
  )
}
