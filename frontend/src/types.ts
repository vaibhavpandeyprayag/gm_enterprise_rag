// Shared API types (mirror the FastAPI response models).

export type Role = 'user' | 'assistant'

export interface User {
  user_id: string
  username: string
}

export interface AuthResponse {
  user: User
  expires_at: string | null
}

export interface Source {
  index: number
  source: string
  locator?: string
  score?: number | null
  snippet?: string
  url?: string
}

export interface Message {
  role: Role
  content: string
  created_at: string
  sources?: Source[]
  /** User turns only: the standalone (rephrased) query used for retrieval. */
  rephrased?: string | null
}

export interface ConversationSummary {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[]
}

export interface FileRecord {
  file_id: string
  filename: string
  size_bytes: number
  chunk_count: number
  content_type?: string | null
  uploaded_at: string
  kind?: 'file' | 'url'
  url?: string | null
}

export interface UploadError {
  filename: string
  error: string
}

export interface UploadResponse {
  uploaded: FileRecord[]
  errors: UploadError[]
}

export interface ChatResponse {
  conversation_id: string
  title: string
  user_message: Message
  assistant_message: Message
}

export interface Health {
  status: string
  mongo: string
  llm_model: string
  embedding_model: string
  embedding_dim: number
  supported_types: string[]
}

/* ---------- banking ---------------------------------------------------- */

export type AccountType =
  | 'checking'
  | 'savings'
  | 'credit_card'
  | 'investment'
  | 'loan'
  | 'other'

export type TransactionStatus = 'posted' | 'pending' | 'reversed'

export interface BankAccount {
  account_id: string
  bank_name: string
  account_name: string
  account_type: AccountType
  /** The full account number - only ever sent to its owner. */
  account_number: string
  account_number_masked: string
  account_number_last4: string
  routing_number: string
  currency: string
  balance: number
  is_primary: boolean
  notes: string | null
  opened_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface BankAccountInput {
  bank_name: string
  account_name: string
  account_number: string
  account_type: AccountType
  routing_number?: string | null
  currency?: string
  balance?: number
  is_primary?: boolean
  notes?: string | null
}

export interface Transaction {
  transaction_id: string
  account_id: string | null
  amount: number
  direction: 'debit' | 'credit'
  currency: string
  description: string
  merchant: string | null
  category: string | null
  status: TransactionStatus
  posted_at: string | null
  balance_after: number | null
  notes: string | null
  created_at: string | null
}

export interface TransactionInput {
  account_id: string
  /** Signed: negative = money out, positive = money in. */
  amount: number
  description: string
  posted_at?: string | null
  merchant?: string | null
  category?: string | null
  currency?: string | null
  status?: TransactionStatus
}

export interface TransactionPage {
  items: Transaction[]
  total: number
  limit: number
  offset: number
}

export interface BankingMeta {
  account_types: AccountType[]
  transaction_statuses: TransactionStatus[]
}
