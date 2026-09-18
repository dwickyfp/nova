import { describe, expect, it } from 'vitest'
import { parseAssistantEvent, readSseFrames } from './events'

describe('parseAssistantEvent', () => {
  it('parses a text delta', () => {
    expect(parseAssistantEvent('text_delta', JSON.stringify({ text: 'hello' }))).toEqual({
      type: 'text_delta',
      text: 'hello',
    })
  })

  it('parses a tool call with classification and status', () => {
    const event = parseAssistantEvent(
      'tool_call',
      JSON.stringify({
        tool_call_id: 'tc-1',
        tool_name: 'query_execute',
        sql_preview: 'SELECT 1',
        classification: 'read_only',
        status: 'pending',
      })
    )
    expect(event).toEqual({
      type: 'tool_call',
      payload: {
        tool_call_id: 'tc-1',
        tool_name: 'query_execute',
        sql_preview: 'SELECT 1',
        classification: 'read_only',
        status: 'pending',
        result_summary: null,
        error: null,
      },
    })
  })

  it('parses a tool status transition', () => {
    expect(
      parseAssistantEvent('tool_status', JSON.stringify({ tool_call_id: 'tc-1', status: 'running' }))
    ).toEqual({ type: 'tool_status', tool_call_id: 'tc-1', status: 'running' })
  })

  it('parses done, error and ping', () => {
    expect(parseAssistantEvent('done', '{"message_id":"m1","finish_reason":"stop"}')).toEqual({
      type: 'done',
      message_id: 'm1',
      finish_reason: 'stop',
    })
    expect(parseAssistantEvent('error', '{"code":"x","message":"boom"}')).toEqual({
      type: 'error',
      code: 'x',
      message: 'boom',
    })
    expect(parseAssistantEvent('ping', '')).toEqual({ type: 'ping' })
  })

  it('ignores unknown event names and malformed payloads instead of throwing', () => {
    expect(parseAssistantEvent('future_event', '{"a":1}')).toBeNull()
    expect(parseAssistantEvent('text_delta', 'not json')).toBeNull()
    expect(parseAssistantEvent('tool_status', '{"status":"nope"}')).toBeNull()
    expect(parseAssistantEvent('tool_call', '{"tool_name":"query_execute"}')).toBeNull()
  })
})

async function framesFrom(chunks: string[]) {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder()
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  const frames = []
  for await (const frame of readSseFrames(stream)) frames.push(frame)
  return frames
}

describe('readSseFrames', () => {
  it('splits frames and preserves the event name', async () => {
    const frames = await framesFrom([
      'event: text_delta\ndata: {"text":"a"}\n\nevent: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
    ])
    expect(frames).toEqual([
      { event: 'text_delta', data: '{"text":"a"}' },
      { event: 'done', data: '{"message_id":"m","finish_reason":"stop"}' },
    ])
  })

  it('reassembles a frame split across chunks', async () => {
    const frames = await framesFrom(['event: text_delta\nda', 'ta: {"text":"hi"}\n', '\n'])
    expect(frames).toEqual([{ event: 'text_delta', data: '{"text":"hi"}' }])
  })

  it('flushes a trailing frame with no terminating blank line (cancelled stream)', async () => {
    const frames = await framesFrom(['event: ping\ndata: {}'])
    expect(frames).toEqual([{ event: 'ping', data: '{}' }])
  })
})
