interface Props {
  open: boolean
  onClose: () => void
  onSelect: (prompt: string) => void
}

const OPERATIONS = [
  { id: 'balance', label: 'View account balance', prompt: 'Show me my current bank account balance.' },
  { id: 'recent_txn', label: 'View last 5 transactions', prompt: 'Show my last 5 bank transactions.' },
  { id: 'chequebook', label: 'Issue cheque book', prompt: 'I need to issue a new cheque book.' },
]

export default function BankOpsModal({ open, onClose, onSelect }: Props) {
  if (!open) return null

  const handlePick = (prompt: string) => {
    onSelect(prompt)
    onClose()
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Bank operations">
        <div className="modal-head">
          <h2>Bank operations</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <p style={{ margin: 0, color: 'var(--text-faint)', fontSize: '14px' }}>
          Choose one of the following operations to proceed
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px', marginTop: '8px' }}>
          {OPERATIONS.map((op) => (
            <button
              key={op.id}
              onClick={() => handlePick(op.prompt)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                textAlign: 'left',
                padding: '14px 16px',
                borderRadius: '14px',
                border: '1px solid var(--border)',
                background: 'linear-gradient(135deg, var(--bg-surface) 0%, var(--bg-inset) 100%)',
                color: 'var(--text)',
                fontWeight: 600,
                fontSize: '14px',
                boxShadow: 'var(--shadow-card)',
                transition: 'transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)'
                e.currentTarget.style.borderColor = 'var(--accent)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)'
                e.currentTarget.style.borderColor = 'var(--border)'
              }}
            >
            
              {op.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}