import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryScreen } from './MemoryScreen'
import { correctMemory, fetchMemories, fetchMemoryHistory, forgetMemory, type MemoryRecord } from './api'

vi.mock('./AuthContext', () => ({
  useAuth: () => ({ signOut: vi.fn() }),
}))

vi.mock('./api', () => ({
  AuthExpiredError: class AuthExpiredError extends Error {},
  fetchMemories: vi.fn(),
  fetchMemoryHistory: vi.fn(),
  correctMemory: vi.fn(),
  forgetMemory: vi.fn(),
}))

function memory(overrides: Partial<MemoryRecord> = {}): MemoryRecord {
  return {
    id: 'mem-1',
    memory_type: 'episodic',
    content: 'has a big interview Friday at Acme',
    confidence: 0.9,
    status: 'active',
    user_verified: false,
    is_sensitive: false,
    pending_confirmation: false,
    source_kind: 'stated',
    source_message_id: null,
    source_journal_entry_id: null,
    source_timestamp: null,
    superseded_by: null,
    created_at: '2026-10-12T00:00:00Z',
    updated_at: '2026-10-12T00:00:00Z',
    ...overrides,
  }
}

describe('MemoryScreen', () => {
  it('shows a loading state then the grouped list with badges and provenance', async () => {
    vi.mocked(fetchMemories).mockResolvedValue([
      memory({ id: 'a', memory_type: 'profile', content: 'works as a data engineer', source_kind: 'inferred' }),
      memory({ id: 'b', memory_type: 'episodic', content: 'has a big interview Friday at Acme', user_verified: true }),
    ])

    render(<MemoryScreen />)
    expect(screen.getByText('Loading what Kizuna remembers…')).toBeInTheDocument()

    expect(await screen.findByRole('heading', { name: 'Profile' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Episodic' })).toBeInTheDocument()
    expect(screen.getByText('works as a data engineer')).toBeInTheDocument()
    expect(screen.getByText('inferred')).toBeInTheDocument()
    expect(screen.getByText('Confirmed by you')).toBeInTheDocument()
    expect(screen.getAllByText(/Added on/)).toHaveLength(2)
  })

  it('distinguishes journal-sourced memories from chat-sourced ones by provenance', async () => {
    vi.mocked(fetchMemories).mockResolvedValue([
      memory({ id: 'a', content: 'works as a data engineer', source_journal_entry_id: 'journal-1' }),
      memory({ id: 'b', content: 'has a big interview Friday at Acme', source_message_id: 'msg-1' }),
    ])
    render(<MemoryScreen />)

    expect(await screen.findByText(/From a journal entry on/)).toBeInTheDocument()
    expect(screen.getByText(/From a conversation on/)).toBeInTheDocument()
  })

  it('shows an empty state when there are no memories', async () => {
    vi.mocked(fetchMemories).mockResolvedValue([])
    render(<MemoryScreen />)
    expect(await screen.findByText(/Nothing remembered yet/)).toBeInTheDocument()
  })

  it('shows an error state with retry on load failure', async () => {
    vi.mocked(fetchMemories).mockRejectedValueOnce(new Error('Loading memories failed (500)'))
    render(<MemoryScreen />)
    expect(await screen.findByText('Loading memories failed (500)')).toBeInTheDocument()

    vi.mocked(fetchMemories).mockResolvedValueOnce([memory()])
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('has a big interview Friday at Acme')).toBeInTheDocument()
  })

  it('filters by search text and by type', async () => {
    vi.mocked(fetchMemories).mockResolvedValue([
      memory({ id: 'a', memory_type: 'profile', content: 'works as a data engineer' }),
      memory({ id: 'b', memory_type: 'episodic', content: 'has a big interview Friday at Acme' }),
    ])
    render(<MemoryScreen />)
    await screen.findByText('works as a data engineer')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Search memories'), 'interview')
    expect(screen.queryByText('works as a data engineer')).not.toBeInTheDocument()
    expect(screen.getByText('has a big interview Friday at Acme')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Search memories'))
    await user.click(screen.getByRole('button', { name: 'Profile' }))
    expect(screen.getByText('works as a data engineer')).toBeInTheDocument()
    expect(screen.queryByText('has a big interview Friday at Acme')).not.toBeInTheDocument()
  })

  it('corrects a memory inline and replaces it with the superseding row', async () => {
    vi.mocked(fetchMemories).mockResolvedValue([memory()])
    vi.mocked(correctMemory).mockResolvedValue(
      memory({ id: 'mem-2', content: 'has a big interview Thursday at Acme', user_verified: true }),
    )
    render(<MemoryScreen />)
    await screen.findByText('has a big interview Friday at Acme')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Correct' }))
    const textarea = screen.getByDisplayValue('has a big interview Friday at Acme')
    await user.clear(textarea)
    await user.type(textarea, 'has a big interview Thursday at Acme')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(correctMemory).toHaveBeenCalledWith('mem-1', 'has a big interview Thursday at Acme'))
    expect(await screen.findByText('has a big interview Thursday at Acme')).toBeInTheDocument()
    expect(screen.queryByText('has a big interview Friday at Acme')).not.toBeInTheDocument()
  })

  it('forgets a memory only after an explicit confirm step', async () => {
    vi.mocked(fetchMemories).mockResolvedValue([memory()])
    vi.mocked(forgetMemory).mockResolvedValue(undefined)
    render(<MemoryScreen />)
    await screen.findByText('has a big interview Friday at Acme')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Forget' }))
    expect(screen.getByText(/permanent/)).toBeInTheDocument()
    expect(forgetMemory).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Forget it' }))
    await waitFor(() => expect(forgetMemory).toHaveBeenCalledWith('mem-1'))
    expect(screen.queryByText('has a big interview Friday at Acme')).not.toBeInTheDocument()
  })

  it('expands a row to show its supersede history', async () => {
    vi.mocked(fetchMemories).mockResolvedValue([memory({ id: 'mem-2', content: 'has a big interview Thursday at Acme' })])
    vi.mocked(fetchMemoryHistory).mockResolvedValue([memory({ id: 'mem-1', content: 'has a big interview Friday at Acme' })])
    render(<MemoryScreen />)
    await screen.findByText('has a big interview Thursday at Acme')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Show history' }))
    expect(await screen.findByText(/Corrected from: “has a big interview Friday at Acme”/)).toBeInTheDocument()
  })
})
