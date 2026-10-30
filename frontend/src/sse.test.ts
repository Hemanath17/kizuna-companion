import { describe, expect, it } from 'vitest'
import { parseSseStream } from './sse'

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]))
        i += 1
      } else {
        controller.close()
      }
    },
  })
}

describe('parseSseStream', () => {
  it('parses complete frames, including one split across chunk boundaries', async () => {
    const stream = streamFrom(['event: token\ndata: {"text":"a"}\n\n', 'event: do', 'ne\ndata: {}\n\n'])
    const events = []
    for await (const evt of parseSseStream(stream)) {
      events.push(evt)
    }
    expect(events).toEqual([
      { event: 'token', data: '{"text":"a"}' },
      { event: 'done', data: '{}' },
    ])
  })

  it('yields nothing for a stream with no complete frame', async () => {
    const stream = streamFrom(['event: token\ndata: {"text":"a"}'])
    const events = []
    for await (const evt of parseSseStream(stream)) {
      events.push(evt)
    }
    expect(events).toEqual([])
  })
})
