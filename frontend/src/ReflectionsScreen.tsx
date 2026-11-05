import { useEffect, useState } from 'react'
import { useAuth } from './AuthContext'
import {
  AuthExpiredError,
  addReflectionNote,
  createReflection,
  deleteReflection,
  fetchReflectionNotes,
  fetchReflections,
  type Reflection,
  type ReflectionNote,
} from './api'

function describeError(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

function periodLabel(reflection: Reflection): string {
  return `${formatDate(reflection.period_start)} – ${formatDate(reflection.period_end)}`
}

interface ReflectionCardProps {
  reflection: Reflection
  onDeleted: (id: string) => void
}

function ReflectionCard({ reflection, onDeleted }: ReflectionCardProps) {
  const [open, setOpen] = useState(false)
  const [notes, setNotes] = useState<ReflectionNote[] | null>(null)
  const [notesError, setNotesError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function loadNotes() {
    try {
      setNotes(await fetchReflectionNotes(reflection.id))
    } catch (err) {
      setNotesError(describeError(err, 'Could not load notes.'))
    }
  }

  async function toggleOpen() {
    const next = !open
    setOpen(next)
    if (next && reflection.status === 'ready' && notes === null) {
      await loadNotes()
    }
  }

  async function handleAddNote() {
    const body = draft.trim()
    if (!body) return
    setBusy(true)
    setError(null)
    try {
      await addReflectionNote(reflection.id, body)
      setDraft('')
      await loadNotes()
    } catch (err) {
      setError(describeError(err, 'Could not save your note.'))
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    setBusy(true)
    try {
      await deleteReflection(reflection.id)
      onDeleted(reflection.id)
    } catch (err) {
      setError(describeError(err, 'Could not delete this reflection.'))
      setBusy(false)
    }
  }

  return (
    <li className="journal-entry-card">
      <button type="button" className="journal-entry-summary" onClick={() => void toggleOpen()} aria-expanded={open}>
        <span className="journal-entry-date">{periodLabel(reflection)}</span>
        <span className={`pill pill-reflection-${reflection.status}`}>
          {reflection.status === 'pending' ? 'generating' : reflection.status}
        </span>
      </button>

      {open && (
        <div className="journal-entry-body">
          {reflection.status === 'pending' && (
            <p className="memory-status">Still putting this together — check back in a bit.</p>
          )}
          {reflection.status === 'failed' && <p className="memory-status">This one didn't generate. You can delete it and ask again.</p>}
          {reflection.status === 'ready' && (
            <>
              <p className="journal-entry-full-text">{reflection.body}</p>

              <div className="reflection-notes">
                {notesError && <p className="journal-error">{notesError}</p>}
                {notes?.map((note) => (
                  <p key={note.id} className="memory-history-entry">
                    {note.body} <span className="memory-provenance">({formatDate(note.created_at)})</span>
                  </p>
                ))}
                <div className="journal-composer-actions-inline">
                  <textarea
                    className="journal-composer-textarea"
                    placeholder="Add a note of your own…"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={busy}
                    rows={2}
                  />
                  <button type="button" className="btn btn-secondary" disabled={busy || !draft.trim()} onClick={() => void handleAddNote()}>
                    Add note
                  </button>
                </div>
              </div>
            </>
          )}

          {error && <p className="journal-error">{error}</p>}

          {confirmingDelete ? (
            <div className="memory-confirm-forget">
              <span>This is permanent.</span>
              <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setConfirmingDelete(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void handleDelete()}>
                Delete it
              </button>
            </div>
          ) : (
            <div className="memory-actions">
              <button type="button" className="icon-button" disabled={busy} onClick={() => setConfirmingDelete(true)}>
                Delete
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

export function ReflectionsScreen() {
  const { signOut } = useAuth()
  const [reflections, setReflections] = useState<Reflection[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [requesting, setRequesting] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)

  async function load() {
    setLoadError(null)
    try {
      setReflections(await fetchReflections())
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        await signOut()
        return
      }
      setLoadError(describeError(err, 'Could not load your reflections.'))
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleRequest(period: 'day' | 'week') {
    setRequesting(true)
    setRequestError(null)
    try {
      const created = await createReflection(period)
      setReflections((prev) => {
        const withoutDuplicate = (prev ?? []).filter((r) => r.id !== created.id)
        return [created, ...withoutDuplicate]
      })
    } catch (err) {
      setRequestError(describeError(err, 'Could not request a reflection.'))
    } finally {
      setRequesting(false)
    }
  }

  function handleDeleted(id: string) {
    setReflections((prev) => (prev ? prev.filter((r) => r.id !== id) : prev))
  }

  return (
    <div className="journal-screen">
      <div className="journal-header-row">
        <h2 className="memory-group-title">Kizuna's Reflections</h2>
        <div className="reflection-request-actions">
          <button type="button" className="btn btn-secondary" disabled={requesting} onClick={() => void handleRequest('day')}>
            Reflect on today
          </button>
          <button type="button" className="btn btn-primary" disabled={requesting} onClick={() => void handleRequest('week')}>
            Reflect on this week
          </button>
        </div>
      </div>

      <div className="journal-list">
        {requestError && <p className="journal-error">{requestError}</p>}

        {reflections === null && !loadError && <p className="memory-status">Loading your reflections…</p>}

        {loadError && (
          <div className="memory-status">
            <p>{loadError}</p>
            <button type="button" className="btn btn-secondary" onClick={() => void load()}>
              Try again
            </button>
          </div>
        )}

        {reflections !== null && !loadError && reflections.length === 0 && (
          <p className="memory-status">No reflections yet — ask for one above whenever you'd like.</p>
        )}

        {reflections !== null && reflections.length > 0 && (
          <ul className="memory-rows">
            {reflections.map((reflection) => (
              <ReflectionCard key={reflection.id} reflection={reflection} onDeleted={handleDeleted} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
