import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { JournalScreen } from './JournalScreen'
import { createJournalEntry, deleteJournalEntry, fetchJournalEntries, updateJournalEntry, type JournalEntry } from './api'

vi.mock('./AuthContext', () => ({
  useAuth: () => ({ signOut: vi.fn() }),
}))

vi.mock('./api', () => ({
  AuthExpiredError: class AuthExpiredError extends Error {},
  fetchJournalEntries: vi.fn(),
  createJournalEntry: vi.fn(),
  updateJournalEntry: vi.fn(),
  deleteJournalEntry: vi.fn(),
}))

function entry(overrides: Partial<JournalEntry> = {}): JournalEntry {
  return {
    id: 'entry-1',
    body: 'Had a long day but it ended well.',
    mood: 'good',
    created_at: '2026-10-12T00:00:00Z',
    updated_at: '2026-10-12T00:00:00Z',
    ...overrides,
  }
}

describe('JournalScreen', () => {
  it('shows an empty state, then composes and saves a new entry with a skippable mood', async () => {
    vi.mocked(fetchJournalEntries).mockResolvedValue([])
    vi.mocked(createJournalEntry).mockResolvedValue(entry({ id: 'new-1', mood: null }))
    render(<JournalScreen />)

    expect(await screen.findByText(/Nothing written yet/)).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'New entry' }))
    await user.type(screen.getByPlaceholderText("What's on your mind today?"), 'Had a long day but it ended well.')
    // Mood is clearly skippable: save works with none selected.
    await user.click(screen.getByRole('button', { name: 'Save entry' }))

    await waitFor(() =>
      expect(createJournalEntry).toHaveBeenCalledWith('Had a long day but it ended well.', null),
    )
    expect(screen.queryByText(/Nothing written yet/)).not.toBeInTheDocument()
  })

  it('attaches a mood when one is picked, and toggles it off on a second click', async () => {
    vi.mocked(fetchJournalEntries).mockResolvedValue([])
    vi.mocked(createJournalEntry).mockResolvedValue(entry())
    render(<JournalScreen />)
    await screen.findByText(/Nothing written yet/)

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'New entry' }))
    await user.type(screen.getByPlaceholderText("What's on your mind today?"), 'Feeling good today.')
    await user.click(screen.getByRole('button', { name: 'Good' }))
    await user.click(screen.getByRole('button', { name: 'Save entry' }))

    await waitFor(() => expect(createJournalEntry).toHaveBeenCalledWith('Feeling good today.', 'good'))
  })

  it('lists entries with date, mood pill, and excerpt, then expands to the full text', async () => {
    vi.mocked(fetchJournalEntries).mockResolvedValue([entry()])
    render(<JournalScreen />)

    const summary = await screen.findByRole('button', { name: /Had a long day/ })
    expect(screen.getByText('good')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(summary)
    expect(screen.getByText('Had a long day but it ended well.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('edits an entry and deletes one only after an explicit confirm', async () => {
    vi.mocked(fetchJournalEntries).mockResolvedValue([entry()])
    vi.mocked(updateJournalEntry).mockResolvedValue(entry({ body: 'Updated text.', mood: 'low' }))
    vi.mocked(deleteJournalEntry).mockResolvedValue(undefined)
    render(<JournalScreen />)

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Had a long day/ }))
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    const textarea = screen.getByDisplayValue('Had a long day but it ended well.')
    await user.clear(textarea)
    await user.type(textarea, 'Updated text.')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(updateJournalEntry).toHaveBeenCalledWith('entry-1', 'Updated text.', 'good'))
    expect(await screen.findByText('low')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByText(/permanent/)).toBeInTheDocument()
    expect(deleteJournalEntry).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Delete it' }))
    await waitFor(() => expect(deleteJournalEntry).toHaveBeenCalledWith('entry-1'))
  })

  it('shows an error state with retry on load failure', async () => {
    vi.mocked(fetchJournalEntries).mockRejectedValueOnce(new Error('Loading journal entries failed (500)'))
    render(<JournalScreen />)
    expect(await screen.findByText('Loading journal entries failed (500)')).toBeInTheDocument()

    vi.mocked(fetchJournalEntries).mockResolvedValueOnce([entry()])
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: /Had a long day/ })).toBeInTheDocument()
  })
})
