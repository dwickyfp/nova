import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchGraph, fetchGraphRun, fetchGraphRuns, fetchGraphs } from './api';

const fetchMock = vi.fn();

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
  };
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockResolvedValue(jsonResponse({}));
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function requestedUrl(index = 0) {
  return fetchMock.mock.calls[index][0] as string;
}

function requestedMethod(index = 0) {
  return fetchMock.mock.calls[index][1]?.method ?? 'GET';
}

// The orchestration read API lives at a prefix the native /tasks surface does
// not use. These assertions pin the URLs and the read-only method so a caller
// cannot drift onto /tasks or send a mutation.
describe('task-orchestration API calls', () => {
  it('lists graphs', async () => {
    await fetchGraphs();

    expect(requestedUrl()).toBe('/api/v1/task-orchestration/graphs');
    expect(requestedMethod()).toBe('GET');
  });

  it('reads one graph definition', async () => {
    await fetchGraph('g1');

    expect(requestedUrl()).toBe('/api/v1/task-orchestration/graphs/g1');
    expect(requestedMethod()).toBe('GET');
  });

  it('encodes a graph id with URL-reserved characters', async () => {
    await fetchGraph('graph/with space');

    expect(requestedUrl()).toBe(
      '/api/v1/task-orchestration/graphs/graph%2Fwith%20space',
    );
  });

  it('lists graph runs', async () => {
    await fetchGraphRuns('g1');

    expect(requestedUrl()).toBe('/api/v1/task-orchestration/graphs/g1/runs');
    expect(requestedMethod()).toBe('GET');
  });

  it('reads one graph run with its node runs', async () => {
    await fetchGraphRun('r1');

    expect(requestedUrl()).toBe('/api/v1/task-orchestration/runs/r1');
    expect(requestedMethod()).toBe('GET');
  });

  it.each([
    ['fetchGraphs', () => fetchGraphs()],
    ['fetchGraph', () => fetchGraph('g')],
    ['fetchGraphRuns', () => fetchGraphRuns('g')],
    ['fetchGraphRun', () => fetchGraphRun('r')],
  ])('%s sends a single /api/v1 prefix', async (_name, call) => {
    await call();

    expect(requestedUrl().startsWith('/api/v1/task-orchestration/')).toBe(true);
    expect(requestedUrl()).not.toContain('/api/v1/api/v1');
  });

  it.each([
    ['fetchGraphs', () => fetchGraphs()],
    ['fetchGraph', () => fetchGraph('g')],
    ['fetchGraphRuns', () => fetchGraphRuns('g')],
    ['fetchGraphRun', () => fetchGraphRun('r')],
  ])('%s never targets the native /tasks surface', async (_name, call) => {
    await call();

    expect(requestedUrl()).not.toMatch(/\/api\/v1\/tasks(\/|$)/);
  });
});
