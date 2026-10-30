import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

type AuthCallback = (event: string, session: unknown) => void
const authStateCallbacks: AuthCallback[] = []

vi.mock('./supabaseClient', () => ({
  supabase: {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session: null } }),
      onAuthStateChange: vi.fn((cb: AuthCallback) => {
        authStateCallbacks.push(cb)
        return { data: { subscription: { unsubscribe: vi.fn() } } }
      }),
      signInWithPassword: vi.fn().mockImplementation(async ({ email }: { email: string; password: string }) => {
        authStateCallbacks.forEach((cb) => cb('SIGNED_IN', { access_token: 'token-123', user: { email } }))
        return { error: null }
      }),
      signOut: vi.fn().mockResolvedValue({ error: null }),
    },
  },
}))

import App from './App'

describe('App auth gate', () => {
  beforeEach(() => {
    authStateCallbacks.length = 0
  })

  it('shows the home page when unauthenticated, then login, then the chat screen after sign-in', async () => {
    render(<App />)

    // Unauthenticated visitors land on the public home page first, not login.
    // (There are two "Get started" CTAs on the page — hero and closing band.)
    expect(await screen.findAllByRole('button', { name: 'Get started' })).toHaveLength(2)
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getAllByRole('button', { name: 'Get started' })[0])

    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Message Kizuna…')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('Email'), 'demo@example.com')
    await user.type(screen.getByLabelText('Password'), 'secret123')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByPlaceholderText('Message Kizuna…')).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
  })
})
