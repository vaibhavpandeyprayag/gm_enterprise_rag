/** Landing screen shown when the active conversation has no messages. */
export default function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-mark" aria-hidden>
        <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      </div>
      <h2>How can I help you today?</h2>
      <p>Ask questions about the files and web pages you have added.</p>
    </div>
  )
}
