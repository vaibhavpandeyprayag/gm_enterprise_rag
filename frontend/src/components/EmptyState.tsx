/** Landing screen shown when the active conversation has no messages. */
export default function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-mark" aria-hidden>
        <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M3 10h18M5 10V7a2 2 0 012-2h10a2 2 0 012 2v3" />
          <rect x="3" y="10" width="18" height="10" rx="2" />
          <path d="M8 14h3M13 14h3" />
        </svg>
      </div>
      <span className="empty-kicker">NEXORA BANK AI ASSISTANT</span>
      <h2>Your Intelligent Banking Assistant</h2>
      <p>
        Ask questions about your uploaded documents.
      </p>
      <p>
        For banking operations such as checking your balance or viewing transactions, select
         <span style={{margin: "0.5rem"}}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 15a3 3 0 100-6 3 3 0 000 6z" />
            <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
          </svg>  
        </span>below.
      </p>
    </div>
  )
}