import { useEffect, useState } from 'react'
import { useAuth } from './AuthContext'
import {
  AuthExpiredError,
  createJournalEntry,
  deleteJournalEntry,
  fetchJournalEntries,
  updateJournalEntry,
  type JournalEntry,
} from './api'

const MOODS: { value: NonNullable<JournalEntry['mood']>; label: string }[] = [
  { value: 'great', label: 'Great' },
  { value: 'good', label: 'Good' },
  { value: 'okay', label: 'Okay' },
  { value: 'low', label: 'Low' },
  { value: 'rough', label: 'Rough' },
]

function describeError(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

function excerpt(body: string, max = 140): string {
  const flattened = body.replace(/\s+/g, ' ').trim()
  return flattened.length > max ? `${flattened.slice(0, max).trimEnd()}…` : flattened
}

interface MoodPickerProps {
  value: JournalEntry['mood']
  onChange: (mood: JournalEntry['mood']) => void
  disabled?: boolean
}

function MoodPicker({ value, onChange, disabled }: MoodPickerProps) {
  return (
    <div className="mood-picker" role="group" aria-label="Mood (optional)">
      {MOODS.map((mood) => (
        <button
          key={mood.value}
          type="button"
          className={`mood-pill${value === mood.value ? ' mood-pill-active' : ''}`}
          aria-pressed={value === mood.value}
          disabled={disabled}
          onClick={() => onChange(value === mood.value ? null : mood.value)}
        >
          {mood.label}
        </button>
      ))}
    </div>
  )
}

interface ComposerProps {
  initialBody?: string
  initialMood?: JournalEntry['mood']
  busy: boolean
  error: string | null
  onSave: (body: string, mood: JournalEntry['mood']) => void
  onCancel?: () => void
  saveLabel: string
}

function Composer({ initialBody = '', initialMood = null, busy, error, onSave, onCancel, saveLabel }: ComposerProps) {
  const [body, setBody] = useState(initialBody)
  const [mood, setMood] = useState<JournalEntry['mood']>(initialMood)

  return (
    <div className="journal-composer">
      <textarea
        className="journal-composer-textarea"
        placeholder="What's on your mind today?"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={busy}
        rows={6}
      />
      <MoodPicker value={mood} onChange={setMood} disabled={busy} />
      {error && <p className="journal-error">{error}</p>}
      <div className="journal-composer-actions">
        {onCancel && (
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        )}
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !body.trim()}
          onClick={() => onSave(body.trim(), mood)}
        >
          {saveLabel}
        </button>
      </div>
    </div>
  )
}

interface EntryCardProps {
  entry: JournalEntry
  onUpdated: (entry: JournalEntry) => void
  onDeleted: (id: string) => void
}

function EntryCard({ entry, onUpdated, onDeleted }: EntryCardProps) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSave(body: string, mood: JournalEntry['mood']) {
    setBusy(true)
    setError(null)
    try {
      onUpdated(await updateJournalEntry(entry.id, body, mood))
      setEditing(false)
    } catch (err) {
      setError(describeError(err, 'Could not save this entry.'))
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    setBusy(true)
    try {
      await deleteJournalEntry(entry.id)
      onDeleted(entry.id)
    } catch (err) {
      setError(describeError(err, 'Could not delete this entry.'))
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <li className="journal-entry-card">
        <Composer
          initialBody={entry.body}
          initialMood={entry.mood}
          busy={busy}
          error={error}
          saveLabel="Save"
          onCancel={() => {
            setEditing(false)
            setError(null)
          }}
          onSave={handleSave}
        />
      </li>
    )
  }

  return (
    <li className="journal-entry-card">
      <button type="button" className="journal-entry-summary" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="journal-entry-date">{formatDate(entry.created_at)}</span>
        {entry.mood && <span className={`pill pill-mood-${entry.mood}`}>{entry.mood}</span>}
        <span className="journal-entry-excerpt">{open ? '' : excerpt(entry.body)}</span>
      </button>

      {open && (
        <div className="journal-entry-body">
          <p className="journal-entry-full-text">{entry.body}</p>
          {error && <p className="journal-error">{error}</p>}
          {confirmingDelete ? (
            <div className="memory-confirm-forget">
              <span>This is permanent — the entry will not come back.</span>
              <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setConfirmingDelete(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void handleDelete()}>
                Delete it
              </button>
            </div>
          ) : (
            <div className="memory-actions">
              <button type="button" className="icon-button" disabled={busy} onClick={() => setEditing(true)}>
                Edit
              </button>
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

export function JournalScreen() {
  const { signOut } = useAuth()
  const [entries, setEntries] = useState<JournalEntry[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const [composeBusy, setComposeBusy] = useState(false)
  const [composeError, setComposeError] = useState<string | null>(null)

  async function load() {
    setLoadError(null)
    try {
      setEntries(await fetchJournalEntries())
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        await signOut()
        return
      }
      setLoadError(describeError(err, 'Could not load your journal.'))
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleCreate(body: string, mood: JournalEntry['mood']) {
    setComposeBusy(true)
    setComposeError(null)
    try {
      const created = await createJournalEntry(body, mood)
      setEntries((prev) => [created, ...(prev ?? [])])
      setComposing(false)
    } catch (err) {
      setComposeError(describeError(err, 'Could not save this entry.'))
    } finally {
      setComposeBusy(false)
    }
  }

  function handleUpdated(updated: JournalEntry) {
    setEntries((prev) => (prev ? prev.map((e) => (e.id === updated.id ? updated : e)) : prev))
  }

  function handleDeleted(id: string) {
    setEntries((prev) => (prev ? prev.filter((e) => e.id !== id) : prev))
  }

  return (
    <div className="journal-screen">
      <div className="journal-header-row">
        <h2 className="memory-group-title">My Journal</h2>
        {!composing && (
          <button type="button" className="btn btn-primary" onClick={() => setComposing(true)}>
            New entry
          </button>
        )}
      </div>

      <div className="journal-list">
        {composing && (
          <div className="journal-entry-card journal-entry-card-new">
            <Composer
              busy={composeBusy}
              error={composeError}
              saveLabel="Save entry"
              onCancel={() => {
                setComposing(false)
                setComposeError(null)
              }}
              onSave={handleCreate}
            />
          </div>
        )}

        {entries === null && !loadError && <p className="memory-status">Loading your journal…</p>}

        {loadError && (
          <div className="memory-status">
            <p>{loadError}</p>
            <button type="button" className="btn btn-secondary" onClick={() => void load()}>
              Try again
            </button>
          </div>
        )}

        {entries !== null && !loadError && entries.length === 0 && !composing && (
          <p className="memory-status">Nothing written yet — start with whatever's on your mind.</p>
        )}

        {entries !== null && entries.length > 0 && (
          <ul className="memory-rows">
            {entries.map((entry) => (
              <EntryCard key={entry.id} entry={entry} onUpdated={handleUpdated} onDeleted={handleDeleted} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
