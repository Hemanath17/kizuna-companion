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

// Sends one turn to the .NET gateway. On a 401 (expired token) it refreshes
// the Supabase session once and retries; a second 401 means the session is
// genuinely gone, so the caller should sign the user out.
export async function postChat(params: {
  text: string
  idempotencyKey: string
  conversationId: string | null
  signal?: AbortSignal
}): Promise<ChatResponse> {
  const attempt = async (): Promise<Response> => {
    const { data } = await supabase.auth.getSession()
    const token = data.session?.access_token
    if (!token) {
      throw new AuthExpiredError()
    }
    return fetch(`${API_BASE_URL}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        conversation_id: params.conversationId,
        text: params.text,
        idempotency_key: params.idempotencyKey,
      }),
      signal: params.signal,
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

  return {
    response,
    conversationId: response.headers.get('X-Conversation-Id'),
    turnId: response.headers.get('X-Turn-Id'),
  }
}
