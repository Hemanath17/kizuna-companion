import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ChatScreen } from './ChatScreen'
import { postChat, type ChatResponse } from './api'

vi.mock('./AuthContext', () => ({
  useAuth: () => ({ signOut: vi.fn() }),
}))

vi.mock('./api', () => ({
  AuthExpiredError: class AuthExpiredError extends Error {},
  postChat: vi.fn(),
}))

function sseStreamFrom(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(controller) {
      if (i < frames.length) {
        controller.enqueue(encoder.encode(frames[i]))
        i += 1
      } else {
        controller.close()
      }
    },
  })
}

function okResponse(body: ReadableStream<Uint8Array>): ChatResponse {
  return {
    response: { ok: true, status: 200, json: async () => ({}), body } as unknown as Response,
    conversationId: 'conv-1',
    turnId: 'turn-1',
  }
}

describe('ChatScreen', () => {
  it('renders streamed tokens incrementally and finalizes on done', async () => {
    const mockedPostChat = vi.mocked(postChat)
    mockedPostChat.mockResolvedValue(
      okResponse(
        sseStreamFrom([
          'event: token\ndata: {"text":"Hel"}\n\n',
          'event: token\ndata: {"text":"lo"}\n\n',
          'event: done\ndata: {}\n\n',
        ]),
      ),
    )

    render(<ChatScreen />)
    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Message Kizuna…'), 'hi there')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.getByText('Hello')).toBeInTheDocument())
    // Re-enabled on 'done': typing again makes Send clickable (it was
    // disabled during streaming regardless of input content).
    await user.type(screen.getByPlaceholderText('Message Kizuna…'), 'again')
    expect(screen.getByRole('button', { name: 'Send' })).not.toBeDisabled()
  })

  it('shows an inline error on an error event', async () => {
    const mockedPostChat = vi.mocked(postChat)
    mockedPostChat.mockResolvedValue(
      okResponse(sseStreamFrom(['event: error\ndata: {"message":"boom"}\n\n'])),
    )

    render(<ChatScreen />)
    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Message Kizuna…'), 'hi')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('boom')).toBeInTheDocument()
  })

  it('marks a turn interrupted when the stream ends with no terminal event', async () => {
    const mockedPostChat = vi.mocked(postChat)
    mockedPostChat.mockResolvedValue(
      okResponse(sseStreamFrom(['event: token\ndata: {"text":"partial"}\n\n'])),
    )

    render(<ChatScreen />)
    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Message Kizuna…'), 'hi')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('Interrupted — connection dropped.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resend' })).toBeInTheDocument()
  })

  it('sends on Enter and inserts a newline on Shift+Enter', async () => {
    const mockedPostChat = vi.mocked(postChat)
    mockedPostChat.mockResolvedValue(okResponse(sseStreamFrom(['event: done\ndata: {}\n\n'])))

    render(<ChatScreen />)
    const user = userEvent.setup()
    const textarea = screen.getByPlaceholderText('Message Kizuna…')
    await user.type(textarea, 'line one{Shift>}{Enter}{/Shift}line two')
    expect(textarea).toHaveValue('line one\nline two')

    await user.keyboard('{Enter}')
    await waitFor(() =>
      expect(mockedPostChat).toHaveBeenCalledWith(expect.objectContaining({ text: 'line one\nline two' })),
    )
  })
})
