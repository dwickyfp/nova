import { describe, expect, it } from 'vitest'
import {
  extractStageCompletionContext,
  formatStageCompletionDetail,
  getStageCompletionInsertText,
  shouldTriggerStageSuggestions,
} from './stage-completion'

describe('extractStageCompletionContext', () => {
  it('parses an empty and filtered stage prefix', () => {
    expect(extractStageCompletionContext('SELECT * FROM @')).toEqual({
      kind: 'stage',
      prefix: '',
    })
    expect(extractStageCompletionContext('SELECT * FROM @sal')).toEqual({
      kind: 'stage',
      prefix: 'sal',
    })
  })

  it('parses root and nested stage folders', () => {
    expect(extractStageCompletionContext('SELECT * FROM @sales.')).toEqual({
      kind: 'stage_path',
      stage: 'sales',
      folder: '',
      prefix: '',
    })
    expect(
      extractStageCompletionContext('SELECT * FROM @sales.raw.daily.rep')
    ).toEqual({
      kind: 'stage_path',
      stage: 'sales',
      folder: 'raw.daily',
      prefix: 'rep',
    })
  })

  it('normalizes slash input into dot-separated folder context', () => {
    expect(
      extractStageCompletionContext('SELECT * FROM @sales.raw/daily/rep')
    ).toEqual({
      kind: 'stage_path',
      stage: 'sales',
      folder: 'raw.daily',
      prefix: 'rep',
    })
  })

  it('supports stage and folder names with hyphens and underscores', () => {
    expect(
      extractStageCompletionContext('SELECT * FROM @sales-prod.raw_data-1.')
    ).toEqual({
      kind: 'stage_path',
      stage: 'sales-prod',
      folder: 'raw_data-1',
      prefix: '',
    })
  })
})

describe('stage completion item helpers', () => {
  it('uses structured insertion text and reopens folders', () => {
    const folder = {
      label: 'raw',
      type: 'stage_folder',
      insert_text: 'raw.',
    }
    expect(getStageCompletionInsertText(folder)).toBe('raw.')
    expect(shouldTriggerStageSuggestions(folder)).toBe(true)
    expect(
      shouldTriggerStageSuggestions({ label: 'data.csv', type: 'stage_file' })
    ).toBe(false)
  })

  it('formats file metadata for Monaco details', () => {
    expect(
      formatStageCompletionDetail({
        label: 'data.parquet',
        type: 'stage_file',
        detail: 'Stage file',
        size: 1536,
      })
    ).toBe('Stage file · 1.5 KB')
  })
})
