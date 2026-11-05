import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useAuth } from './AuthContext'
import {
  AuthExpiredError,
  correctMemory,
  fetchMemories,
  fetchMemoryHistory,
  forgetMemory,
  type MemoryRecord,
} from './api'

const TYPE_LABELS: Record<MemoryRecord['memory_type'], string> = {
  profile: 'Profile',
  episodic: 'Episodic',
}

function provenanceText(memory: MemoryRecord): string {
  const date = memory.source_timestamp ?? memory.created_at
  const formatted = new Date(date).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  if (memory.source_journal_entry_id) return `From a journal entry on ${formatted}`
  if (memory.source_message_id) return `From a conversation on ${formatted}`
  return `Added on ${formatted}`
}

function describeError(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

interface MemoryRowProps {
  memory: MemoryRecord
  onCorrected: (updated: MemoryRecord, previousId: string) => void
  onForgotten: (id: string) => void
}

function MemoryRow({ memory, onCorrected, onForgotten }: MemoryRowProps) {
  const [expanded, setExpanded] = useState(false)
  const [history, setHistory] = useState<MemoryRecord[] | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(memory.content)
  const [confirmingForget, setConfirmingForget] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function toggleExpanded() {
    const next = !expanded
    setExpanded(next)
    if (next && history === null) {
      try {
        setHistory(await fetchMemoryHistory(memory.id))
      } catch (err) {
        setHistoryError(describeError(err, 'Could not load history.'))
      }
    }
  }

  async function handleSave() {
    const content = draft.trim()
    if (!content || content === memory.content) {
      setEditing(false)
      setDraft(memory.content)
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      const updated = await correctMemory(memory.id, content)
      onCorrected(updated, memory.id)
      setEditing(false)
    } catch (err) {
      setActionError(describeError(err, 'Could not save the correction.'))
    } finally {
      setBusy(false)
    }
  }

  async function handleForget() {
    setBusy(true)
    setActionError(null)
    try {
      await forgetMemory(memory.id)
      onForgotten(memory.id)
    } catch (err) {
      setActionError(describeError(err, 'Could not forget this memory.'))
      setBusy(false)
    }
  }

  return (
    <li className="memory-row">
      <div className="memory-row-main">
        <button
          type="button"
          className="memory-expand-toggle"
          onClick={() => void toggleExpanded()}
          aria-expanded={expanded}
          aria-label={expanded ? 'Hide history' : 'Show history'}
        >
          {expanded ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
        </button>

        <div className="memory-row-body">
          {editing ? (
            <textarea
              className="memory-edit-textarea"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={busy}
              rows={2}
              autoFocus
            />
          ) : (
            <p className="memory-content">{memory.content}</p>
          )}

          <div className="memory-meta">
            <span className={`pill pill-${memory.source_kind}`}>{memory.source_kind}</span>
            {memory.confidence !== null && (
              <span className="pill pill-muted">{Math.round(memory.confidence * 100)}% confidence</span>
            )}
            {memory.user_verified && <span className="pill pill-verified">Confirmed by you</span>}
            {memory.pending_confirmation && <span className="pill pill-pending">Needs your confirmation</span>}
            <span className="memory-provenance">{provenanceText(memory)}</span>
          </div>

          {actionError && <p className="memory-row-error">{actionError}</p>}

          {confirmingForget ? (
            <div className="memory-confirm-forget">
              <span>This is permanent — the fact will not come back.</span>
              <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setConfirmingForget(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void handleForget()}>
                Forget it
              </button>
            </div>
          ) : (
            <div className="memory-actions">
              {editing ? (
                <>
                  <button
                    type="button"
                    className="icon-button"
                    disabled={busy}
                    onClick={() => {
                      setEditing(false)
                      setDraft(memory.content)
                      setActionError(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button type="button" className="icon-button" disabled={busy} onClick={() => void handleSave()}>
                    Save
                  </button>
                </>
              ) : (
                <>
                  <button type="button" className="icon-button" disabled={busy} onClick={() => setEditing(true)}>
                    Correct
                  </button>
                  <button type="button" className="icon-button" disabled={busy} onClick={() => setConfirmingForget(true)}>
                    Forget
                  </button>
                </>
              )}
            </div>
          )}

          {expanded && (
            <div className="memory-history">
              {historyError && <p className="memory-row-error">{historyError}</p>}
              {history === null && !historyError && <p className="memory-history-loading">Loading history…</p>}
              {history !== null && history.length === 0 && <p className="memory-history-empty">No prior versions.</p>}
              {history !== null &&
                history.map((prior) => (
                  <p key={prior.id} className="memory-history-entry">
                    Corrected from: “{prior.content}” ({provenanceText(prior)})
                  </p>
                ))}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

type TypeFilter = 'all' | MemoryRecord['memory_type']

export function MemoryScreen() {
  const { signOut } = useAuth()
  const [memories, setMemories] = useState<MemoryRecord[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')

  async function load() {
    setLoadError(null)
    try {
      setMemories(await fetchMemories())
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        await signOut()
        return
      }
      setLoadError(describeError(err, 'Could not load your memories.'))
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleCorrected(updated: MemoryRecord, previousId: string) {
    setMemories((prev) => (prev ? [updated, ...prev.filter((m) => m.id !== previousId)] : prev))
  }

  function handleForgotten(id: string) {
    setMemories((prev) => (prev ? prev.filter((m) => m.id !== id) : prev))
  }

  const filtered = (memories ?? []).filter((m) => {
    if (typeFilter !== 'all' && m.memory_type !== typeFilter) return false
    if (search.trim() && !m.content.toLowerCase().includes(search.trim().toLowerCase())) return false
    return true
  })

  const grouped: Record<MemoryRecord['memory_type'], MemoryRecord[]> = { profile: [], episodic: [] }
  for (const memory of filtered) {
    grouped[memory.memory_type].push(memory)
  }

  return (
    <div className="memory-screen">
      <div className="memory-toolbar">
        <input
          type="search"
          placeholder="Search what Kizuna remembers…"
          aria-label="Search memories"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="memory-type-filter" role="group" aria-label="Filter by type">
          {(['all', 'profile', 'episodic'] as const).map((option) => (
            <button
              key={option}
              type="button"
              className={`nav-link${typeFilter === option ? ' nav-link-active' : ''}`}
              aria-pressed={typeFilter === option}
              onClick={() => setTypeFilter(option)}
            >
              {option === 'all' ? 'All' : TYPE_LABELS[option]}
            </button>
          ))}
        </div>
      </div>

      <div className="memory-list">
        {memories === null && !loadError && <p className="memory-status">Loading what Kizuna remembers…</p>}

        {loadError && (
          <div className="memory-status">
            <p>{loadError}</p>
            <button type="button" className="btn btn-secondary" onClick={() => void load()}>
              Try again
            </button>
          </div>
        )}

        {memories !== null && !loadError && filtered.length === 0 && (
          <p className="memory-status">
            {memories.length === 0 ? "Nothing remembered yet — it'll show up here as you talk." : 'No memories match that search.'}
          </p>
        )}

        {(['profile', 'episodic'] as const).map((type) =>
          grouped[type].length > 0 ? (
            <section key={type} className="memory-group">
              <h2 className="memory-group-title">{TYPE_LABELS[type]}</h2>
              <ul className="memory-rows">
                {grouped[type].map((memory) => (
                  <MemoryRow key={memory.id} memory={memory} onCorrected={handleCorrected} onForgotten={handleForgotten} />
                ))}
              </ul>
            </section>
          ) : null,
        )}
      </div>
    </div>
  )
}
