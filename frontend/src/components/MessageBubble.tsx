import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message, Source } from '../types'

/**
 * Some models emit an entire markdown table on one line, using "| |" between
 * rows instead of newlines, so it renders as a run-on paragraph. Detect such
 * lines and put each row on its own line so GFM can parse a real table.
 */
function normalizeTables(markdown: string): string {
  return markdown
    .split('\n')
    .map((line) => {
      const pipes = (line.match(/\|/g) || []).length
      const looksLikeTable =
        pipes >= 3 && (line.trim().startsWith('|') || /\|[\s:-]*-{3,}/.test(line))
      if (!looksLikeTable) return line
      return line.replace(/\|\s*\|/g, '|\n|')
    })
    .join('\n')
}

function SourceChips({ sources }: { sources: Source[] }) {
  return (
    <div className="sources">
      <div className="sources-label">Sources</div>
      <div className="source-chips">
        {sources.map((s) =>
          s.url ? (
            <a
              key={s.index}
              className="chip chip-url"
              href={s.url}
              target="_blank"
              rel="noreferrer"
              title={s.url}
            >
              <span className="chip-idx">{s.index}</span>
              <span className="chip-text">{s.source}</span>
            </a>
          ) : (
            <span key={s.index} className="chip" title={s.snippet || ''}>
              <span className="chip-idx">{s.index}</span>
              <span className="chip-text">
                {s.source}
                {s.locator ? ` (${s.locator})` : ''}
              </span>
            </span>
          ),
        )}
      </div>
    </div>
  )
}

/** One chat turn: user bubble (right) or assistant answer with markdown + sources. */
export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'

  return (
    <article className={`msg ${isUser ? 'msg-user' : 'msg-assistant'}`}>
        {!isUser && (
          <div className="avatar" aria-hidden>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M4 10V7a2 2 0 012-2h12a2 2 0 012 2v3" />
              <rect x="3" y="10" width="18" height="10" rx="2" />
              <path d="M9 14h6" />
            </svg>
          </div>
        )}
      <div className="msg-body">
        {isUser ? (
          <div className="user-bubble">{message.content}</div>
        ) : (
          <>
            <div className="md">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: (props) => {
                    const { href, children } = props
                    return (
                      <a href={href} target="_blank" rel="noreferrer">
                        {children}
                      </a>
                    )
                  },
                }}
              >
                {normalizeTables(message.content)}
              </ReactMarkdown>
            </div>
            {message.sources && message.sources.length > 0 && <SourceChips sources={message.sources} />}
          </>
        )}
      </div>
    </article>
  )
}
