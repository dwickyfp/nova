import { describe, expect, it } from 'vitest'
import { transcriptReducer, type TranscriptMessage } from './use-assistant-transcript'
import type { AssistantEvent } from './types'

function reduce(state: TranscriptMessage[], ...events: AssistantEvent[]) {
  return events.reduce((acc, event) => transcriptReducer(acc, { type: 'event', event }), state)
}

describe('transcriptReducer', () => {
  it('appends text deltas into a single streaming assistant message', () => {
    const state = reduce(
      [],
      { type: 'text_delta', text: 'Hello ' },
      { type: 'text_delta', text: 'world' }
    )
    expect(state).toHaveLength(1)
    expect(state[0].content).toBe('Hello world')
    expect(state[0].turn_state).toBe('streaming')
  })

  it('adds a tool card and updates it by tool_call_id', () => {
    const state = reduce(
      [],
      {
        type: 'tool_call',
        payload: {
          tool_call_id: 'tc-1',
          tool_name: 'query_execute',
          sql_preview: 'SELECT 1',
          classification: 'read_only',
          status: 'pending',
        },
      },
      { type: 'tool_status', tool_call_id: 'tc-1', status: 'running' },
      { type: 'tool_status', tool_call_id: 'tc-1', status: 'done' }
    )
    expect(state).toHaveLength(1)
    expect(state[0].tool_call?.status).toBe('done')
    expect(state[0].tool_call?.sql_preview).toBe('SELECT 1')
  })

  it('marks a streaming message done on the done event', () => {
    const state = reduce(
      [],
      { type: 'text_delta', text: 'answer' },
      { type: 'done', message_id: 'm-9', finish_reason: 'stop' }
    )
    expect(state[0].turn_state).toBe('done')
    expect(state[0].message_id).toBe('m-9')
  })

  it('marks a partial answer cancelled without deleting it', () => {
    const streaming = reduce([], { type: 'text_delta', text: 'partial' })
    const state = transcriptReducer(streaming, { type: 'cancelled' })
    expect(state).toHaveLength(1)
    expect(state[0].content).toBe('partial')
    expect(state[0].turn_state).toBe('cancelled')
  })

  it('keeps an error as a distinct message', () => {
    const state = reduce([], { type: 'error', code: 'provider', message: 'upstream failed' })
    expect(state[0].turn_state).toBe('error')
    expect(state[0].content).toBe('upstream failed')
  })

  it('ignores ping frames', () => {
    expect(reduce([], { type: 'ping' })).toEqual([])
  })
})
