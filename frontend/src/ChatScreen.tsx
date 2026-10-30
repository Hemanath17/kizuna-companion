import { useRef, useState, type KeyboardEvent } from 'react'
import { LogOut, RotateCw } from 'lucide-react'
import { useAuth } from './AuthContext'
import { AuthExpiredError, postChat } from './api'
import { parseSseStream } from './sse'

type TurnStatus = 'streaming' | 'complete' | 'interrupted' | 'error'

interface Turn {
  id: string
  userText: string
  assistantText: string
  status: TurnStatus
  errorMessage?: string
}

export function ChatScreen() {
  const { signOut } = useAuth()
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  // A ref, not state: read synchronously inside sendMessage and only ever
  // written once per successful call, so a render race can't make it stale.
  const conversationIdRef = useRef<string | null>(null)

  function appendAssistantText(id: string, chunk: string) {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, assistantText: t.assistantText + chunk } : t)))
  }

  function setTurnStatus(id: string, status: TurnStatus) {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, status } : t)))
  }

  function setTurnError(id: string, message: string) {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, status: 'error', errorMessage: message } : t)))
  }

  async function describeFailure(response: Response): Promise<string> {
    try {
      const body = await response.json()
      if (typeof body?.message === 'string') return body.message
    } catch {
      // Body wasn't JSON; fall through to the generic message below.
    }
    return `Request failed (${response.status})`
  }

  async function sendMessage(text: string) {
    const id = crypto.randomUUID()
    const idempotencyKey = crypto.randomUUID()
    setTurns((prev) => [...prev, { id, userText: text, assistantText: '', status: 'streaming' }])
    setIsStreaming(true)

    try {
      const { response, conversationId } = await postChat({
        text,
        idempotencyKey,
        conversationId: conversationIdRef.current,
      })
      if (conversationId) {
        conversationIdRef.current = conversationId
      }

      if (!response.ok) {
        setTurnError(id, await describeFailure(response))
        return
      }

      let sawTerminal = false
      for await (const evt of parseSseStream(response.body!)) {
        if (evt.event === 'token') {
          const { text: chunk } = JSON.parse(evt.data) as { text: string }
          appendAssistantText(id, chunk)
        } else if (evt.event === 'done') {
          sawTerminal = true
          setTurnStatus(id, 'complete')
        } else if (evt.event === 'error') {
          sawTerminal = true
          const { message } = JSON.parse(evt.data) as { message: string }
          setTurnError(id, message)
        }
      }
      // The stream ended without a 'done' or 'error' frame: a dropped or
      // stalled connection, not a clean server-reported outcome.
      if (!sawTerminal) {
        setTurnStatus(id, 'interrupted')
      }
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        await signOut()
        return
      }
      setTurnStatus(id, 'interrupted')
    } finally {
      setIsStreaming(false)
    }
  }

  function handleSend() {
    const text = input.trim()
    if (!text || isStreaming) return
    setInput('')
    void sendMessage(text)
  }

  function handleResend(turn: Turn) {
    if (isStreaming) return
    void sendMessage(turn.userText)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="chat-screen">
      <header className="chat-header">
        <h1 className="wordmark">Kizuna</h1>
        <button type="button" className="icon-button" onClick={() => void signOut()}>
          <LogOut size={18} aria-hidden="true" />
          Log out
        </button>
      </header>

      <div className="message-list">
        {turns.map((turn) => (
          <div key={turn.id} className="turn">
            <div className="bubble bubble-user">{turn.userText}</div>
            <div className="bubble bubble-assistant">
              {turn.assistantText}
              {turn.status === 'streaming' && turn.assistantText === '' && (
                <span className="typing-indicator" aria-label="Kizuna is typing">
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                </span>
              )}
              {(turn.status === 'interrupted' || turn.status === 'error') && (
                <div className="turn-status">
                  <span>
                    {turn.status === 'error' ? turn.errorMessage ?? 'Something went wrong.' : 'Interrupted — connection dropped.'}
                  </span>
                  <button type="button" className="icon-button" onClick={() => handleResend(turn)} disabled={isStreaming}>
                    <RotateCw size={14} aria-hidden="true" />
                    Resend
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          handleSend()
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Message Kizuna…"
          disabled={isStreaming}
          rows={1}
        />
        <button type="submit" className="btn btn-primary" disabled={isStreaming || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
