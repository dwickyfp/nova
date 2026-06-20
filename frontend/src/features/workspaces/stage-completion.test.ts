import { describe, expect, it } from 'vitest'
import {
  buildStageCompletionPath,
  extractStageCompletionContext,
  formatStageCompletionDetail,
  getStageFileExtension,
  getStageCompletionInsertText,
  getStageCompletionKind,
  getStageCompletionReplacementRange,
  getStageCompletionSortText,
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

describe('buildStageCompletionPath', () => {
  it('requests stage names using the active database only', () => {
    expect(
      buildStageCompletionPath('NOVA_ANALYTICS', {
        kind: 'stage',
        prefix: '',
      })
    ).toBe('/query/completions?kind=stage&database=NOVA_ANALYTICS&prefix=')
  })

  it('requests realtime contents for the selected stage folder', () => {
    expect(
      buildStageCompletionPath('NOVA_ANALYTICS', {
        kind: 'stage_path',
        stage: 'raw_data',
        folder: 'incoming.daily',
        prefix: 'ord',
      })
    ).toBe(
      '/query/completions?kind=stage_file&database=NOVA_ANALYTICS&prefix=ord&stage=raw_data&folder=incoming.daily'
    )
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
    expect(getStageCompletionKind({ label: 'sales', type: 'stage' })).toBe(
      'stage'
    )
    expect(getStageCompletionKind(folder)).toBe('folder')
    expect(
      getStageCompletionKind({ label: 'data.csv', type: 'stage_file' })
    ).toBe('file')
    expect(getStageCompletionSortText(folder)).toBe('0-raw')
    expect(
      getStageCompletionSortText({
        label: 'data.csv',
        type: 'stage_file',
      })
    ).toBe('2-data.csv')
  })

  it('replaces only the current stage path prefix', () => {
    expect(
      getStageCompletionReplacementRange(3, 28, {
        kind: 'stage_path',
        stage: 'sales',
        folder: 'raw',
        prefix: 'rep',
      })
    ).toEqual({
      startLineNumber: 3,
      startColumn: 25,
      endLineNumber: 3,
      endColumn: 28,
    })
  })

  it('formats file metadata for Monaco details', () => {
    expect(
      getStageFileExtension({
        label: 'data.parquet',
        type: 'stage_file',
      })
    ).toBe('PARQUET')
    expect(
      formatStageCompletionDetail({
        label: 'data.parquet',
        type: 'stage_file',
        detail: 'Stage file',
        size: 1536,
      })
    ).toBe('PARQUET file · 1.5 KB')
    expect(
      formatStageCompletionDetail({
        label: 'README',
        type: 'stage_file',
      })
    ).toBe('Stage file')
  })
})
