import { supabase } from './supabaseClient'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

export class AuthExpiredError extends Error {
  constructor() {
    super('Session expired')
  }
}

export interface ChatResponse {
  response: Response
  conversationId: string | null
  turnId: string | null
}

// Shared by every authenticated call: attaches the current Supabase access
// token, and on a 401 (expired token) refreshes the session once and
// retries. A second 401 means the session is genuinely gone, so the caller
// should sign the user out.
async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const attempt = async (): Promise<Response> => {
    const { data } = await supabase.auth.getSession()
    const token = data.session?.access_token
    if (!token) {
      throw new AuthExpiredError()
    }
    return fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { ...init.headers, Authorization: `Bearer ${token}` },
    })
  }

  let response = await attempt()
  if (response.status === 401) {
    const { error } = await supabase.auth.refreshSession()
    if (error) {
      throw new AuthExpiredError()
    }
    response = await attempt()
    if (response.status === 401) {
      throw new AuthExpiredError()
    }
  }
  return response
}

export async function postChat(params: {
  text: string
  idempotencyKey: string
  conversationId: string | null
  signal?: AbortSignal
}): Promise<ChatResponse> {
  const response = await authorizedFetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      conversation_id: params.conversationId,
      text: params.text,
      idempotency_key: params.idempotencyKey,
    }),
    signal: params.signal,
  })

  return {
    response,
    conversationId: response.headers.get('X-Conversation-Id'),
    turnId: response.headers.get('X-Turn-Id'),
  }
}

export interface MemoryRecord {
  id: string
  memory_type: 'profile' | 'episodic'
  content: string
  confidence: number | null
  status: string
  user_verified: boolean
  is_sensitive: boolean
  pending_confirmation: boolean
  source_kind: 'stated' | 'inferred'
  source_message_id: string | null
  source_journal_entry_id: string | null
  source_timestamp: string | null
  superseded_by: string | null
  created_at: string
  updated_at: string
}

async function parseJsonResponseOrThrow(response: Response, action: string): Promise<unknown> {
  if (!response.ok) {
    throw new Error(`${action} failed (${response.status})`)
  }
  return response.status === 204 ? null : response.json()
}

export async function fetchMemories(): Promise<MemoryRecord[]> {
  const response = await authorizedFetch('/memory')
  return (await parseJsonResponseOrThrow(response, 'Loading memories')) as MemoryRecord[]
}

export async function fetchMemoryHistory(id: string): Promise<MemoryRecord[]> {
  const response = await authorizedFetch(`/memory/${id}/history`)
  return (await parseJsonResponseOrThrow(response, 'Loading memory history')) as MemoryRecord[]
}

export async function correctMemory(id: string, content: string): Promise<MemoryRecord> {
  const response = await authorizedFetch(`/memory/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  return (await parseJsonResponseOrThrow(response, 'Correcting memory')) as MemoryRecord
}

export async function forgetMemory(id: string): Promise<void> {
  const response = await authorizedFetch(`/memory/${id}`, { method: 'DELETE' })
  await parseJsonResponseOrThrow(response, 'Forgetting memory')
}

export interface JournalEntry {
  id: string
  body: string
  mood: 'great' | 'good' | 'okay' | 'low' | 'rough' | null
  created_at: string
  updated_at: string
}

export async function fetchJournalEntries(): Promise<JournalEntry[]> {
  const response = await authorizedFetch('/journal')
  return (await parseJsonResponseOrThrow(response, 'Loading journal entries')) as JournalEntry[]
}

export async function createJournalEntry(body: string, mood: JournalEntry['mood']): Promise<JournalEntry> {
  const response = await authorizedFetch('/journal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body, mood, idempotency_key: crypto.randomUUID() }),
  })
  return (await parseJsonResponseOrThrow(response, 'Saving journal entry')) as JournalEntry
}

export async function updateJournalEntry(id: string, body: string, mood: JournalEntry['mood']): Promise<JournalEntry> {
  const response = await authorizedFetch(`/journal/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body, mood }),
  })
  return (await parseJsonResponseOrThrow(response, 'Saving journal entry')) as JournalEntry
}

export async function deleteJournalEntry(id: string): Promise<void> {
  const response = await authorizedFetch(`/journal/${id}`, { method: 'DELETE' })
  await parseJsonResponseOrThrow(response, 'Deleting journal entry')
}

export interface Reflection {
  id: string
  period_start: string
  period_end: string
  body: string | null
  status: 'pending' | 'ready' | 'rejected' | 'failed'
  rejection_reason: string | null
  created_at: string
  generated_at: string | null
}

export interface ReflectionNote {
  id: string
  body: string
  created_at: string
}

export async function fetchReflections(): Promise<Reflection[]> {
  const response = await authorizedFetch('/reflections')
  return (await parseJsonResponseOrThrow(response, 'Loading reflections')) as Reflection[]
}

export async function createReflection(period: 'day' | 'week'): Promise<Reflection> {
  const response = await authorizedFetch('/reflections', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ period }),
  })
  return (await parseJsonResponseOrThrow(response, 'Requesting a reflection')) as Reflection
}

export async function deleteReflection(id: string): Promise<void> {
  const response = await authorizedFetch(`/reflections/${id}`, { method: 'DELETE' })
  await parseJsonResponseOrThrow(response, 'Deleting reflection')
}

export async function fetchReflectionNotes(id: string): Promise<ReflectionNote[]> {
  const response = await authorizedFetch(`/reflections/${id}/notes`)
  return (await parseJsonResponseOrThrow(response, 'Loading notes')) as ReflectionNote[]
}

export async function addReflectionNote(id: string, body: string): Promise<void> {
  const response = await authorizedFetch(`/reflections/${id}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  })
  await parseJsonResponseOrThrow(response, 'Saving note')
}
