export interface SseEvent {
  event: string
  data: string
}

// Matches the frame shape both backends write: "event: <name>\ndata: <json>\n\n".
function parseFrame(frame: string): SseEvent | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trim())
    }
  }
  if (dataLines.length === 0) {
    return null
  }
  return { event, data: dataLines.join('\n') }
}

// Reads the stream incrementally and yields each SSE frame as soon as its
// trailing blank line arrives, instead of waiting for the response to finish.
export async function* parseSseStream(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        return
      }
      buffer += decoder.decode(value, { stream: true })
      let separatorIndex = buffer.indexOf('\n\n')
      while (separatorIndex !== -1) {
        const frame = buffer.slice(0, separatorIndex)
        buffer = buffer.slice(separatorIndex + 2)
        const event = parseFrame(frame)
        if (event) {
          yield event
        }
        separatorIndex = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}
