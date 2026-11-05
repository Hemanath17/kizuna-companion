import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ReflectionsScreen } from './ReflectionsScreen'
import {
  addReflectionNote,
  createReflection,
  deleteReflection,
  fetchReflectionNotes,
  fetchReflections,
  type Reflection,
} from './api'

vi.mock('./AuthContext', () => ({
  useAuth: () => ({ signOut: vi.fn() }),
}))

vi.mock('./api', () => ({
  AuthExpiredError: class AuthExpiredError extends Error {},
  fetchReflections: vi.fn(),
  createReflection: vi.fn(),
  deleteReflection: vi.fn(),
  fetchReflectionNotes: vi.fn(),
  addReflectionNote: vi.fn(),
}))

function reflection(overrides: Partial<Reflection> = {}): Reflection {
  return {
    id: 'refl-1',
    period_start: '2026-10-05T00:00:00Z',
    period_end: '2026-10-12T00:00:00Z',
    body: 'This week felt steady overall.',
    status: 'ready',
    rejection_reason: null,
    created_at: '2026-10-12T01:00:00Z',
    generated_at: '2026-10-12T01:05:00Z',
    ...overrides,
  }
}

describe('ReflectionsScreen', () => {
  it('shows an empty state, then requests a reflection with no notification chrome anywhere', async () => {
    vi.mocked(fetchReflections).mockResolvedValue([])
    vi.mocked(createReflection).mockResolvedValue(reflection({ status: 'pending', body: null }))
    render(<ReflectionsScreen />)

    expect(await screen.findByText(/No reflections yet/)).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Reflect on this week' }))

    await waitFor(() => expect(createReflection).toHaveBeenCalledWith('week'))
    expect(screen.queryByText(/No reflections yet/)).not.toBeInTheDocument()

    // Explicit product requirement: no badges, no "new reflection" nudge text anywhere.
    expect(screen.queryByText(/unread/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/wrote something new/i)).not.toBeInTheDocument()
  })

  it('shows a quiet generating state for a pending reflection, not an error', async () => {
    vi.mocked(fetchReflections).mockResolvedValue([reflection({ status: 'pending', body: null })])
    render(<ReflectionsScreen />)

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /generating/ }))
    expect(await screen.findByText(/Still putting this together/)).toBeInTheDocument()
  })

  it('reads a ready reflection and adds a note, which then appears in the list', async () => {
    vi.mocked(fetchReflections).mockResolvedValue([reflection()])
    vi.mocked(fetchReflectionNotes).mockResolvedValue([])
    vi.mocked(addReflectionNote).mockResolvedValue(undefined)
    render(<ReflectionsScreen />)

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /ready/ }))
    expect(screen.getByText('This week felt steady overall.')).toBeInTheDocument()

    vi.mocked(fetchReflectionNotes).mockResolvedValue([
      { id: 'note-1', body: 'Glad to hear that.', created_at: '2026-10-12T02:00:00Z' },
    ])
    await user.type(screen.getByPlaceholderText(/Add a note/), 'Glad to hear that.')
    await user.click(screen.getByRole('button', { name: 'Add note' }))

    await waitFor(() => expect(addReflectionNote).toHaveBeenCalledWith('refl-1', 'Glad to hear that.'))
    expect(await screen.findByText(/Glad to hear that\./)).toBeInTheDocument()
  })

  it('deletes a reflection only after an explicit confirm step', async () => {
    vi.mocked(fetchReflections).mockResolvedValue([reflection()])
    vi.mocked(fetchReflectionNotes).mockResolvedValue([])
    vi.mocked(deleteReflection).mockResolvedValue(undefined)
    render(<ReflectionsScreen />)

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /ready/ }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByText(/permanent/)).toBeInTheDocument()
    expect(deleteReflection).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Delete it' }))
    await waitFor(() => expect(deleteReflection).toHaveBeenCalledWith('refl-1'))
    expect(screen.queryByText('This week felt steady overall.')).not.toBeInTheDocument()
  })
})
