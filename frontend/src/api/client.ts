// Thin fetch wrapper around the FastAPI backend (relative /api URLs).

import type {
  BankAccount,
  BankAccountInput,
  BankingMeta,
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  FileRecord,
  Health,
  Transaction,
  TransactionInput,
  TransactionPage,
  UploadResponse,
} from '../types'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {}
  const isFormData = init?.body instanceof FormData
  if (!isFormData && init?.body) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as object) },
  })

  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`
    try {
      const data = (await res.json()) as { detail?: string }
      if (data.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      /* non-JSON error body - keep statusText */
    }
    throw new ApiError(detail, res.status)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  health: () => request<Health>('/health'),

  // ---- conversations ----------------------------------------------------
  listConversations: () => request<ConversationSummary[]>('/conversations'),
  getConversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  createConversation: (title: string) =>
    request<ConversationSummary>('/conversations', { method: 'POST', body: JSON.stringify({ title }) }),
  renameConversation: (id: string, title: string) =>
    request<ConversationSummary>(`/conversations/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  deleteConversation: (id: string) =>
    request<{ conversation_id: string; status: string }>(`/conversations/${id}`, { method: 'DELETE' }),

  chat: (message: string, conversationId: string | null) =>
    request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id: conversationId }),
    }),

  // ---- files ------------------------------------------------------------
  listFiles: () => request<FileRecord[]>('/files'),
  uploadFiles: (files: File[]) => {
    const form = new FormData()
    for (const file of files) form.append('files', file, file.name)
    return request<UploadResponse>('/files/upload', { method: 'POST', body: form })
  },
  addUrl: (url: string) =>
    request<FileRecord>('/files/url', { method: 'POST', body: JSON.stringify({ url }) }),
  deleteFile: (id: string) =>
    request<{ file_id: string; deleted_chunks: number }>(`/files/${id}`, { method: 'DELETE' }),

  // ---- banking ----------------------------------------------------------
  bankingMeta: () => request<BankingMeta>('/banking/meta'),

  listAccounts: () => request<BankAccount[]>('/accounts'),
  createAccount: (body: BankAccountInput) =>
    request<BankAccount>('/accounts', { method: 'POST', body: JSON.stringify(body) }),
  updateAccount: (id: string, body: Partial<BankAccountInput>) =>
    request<BankAccount>(`/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteAccount: (id: string) =>
    request<{ account_id: string; deleted_transactions: number }>(`/accounts/${id}`, {
      method: 'DELETE',
    }),

  listTransactions: (params?: { account_id?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams()
    if (params?.account_id) query.set('account_id', params.account_id)
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.offset) query.set('offset', String(params.offset))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<TransactionPage>(`/transactions${suffix}`)
  },
  createTransaction: (body: TransactionInput) =>
    request<Transaction>('/transactions', { method: 'POST', body: JSON.stringify(body) }),
  updateTransaction: (id: string, body: Partial<TransactionInput>) =>
    request<Transaction>(`/transactions/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteTransaction: (id: string) =>
    request<{ transaction_id: string; status: string }>(`/transactions/${id}`, {
      method: 'DELETE',
    }),
}
