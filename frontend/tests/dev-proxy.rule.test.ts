import { createServer as createHttpServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterAll, beforeAll, expect, it, vi } from 'vitest'
import { createServer, type ViteDevServer } from 'vite'
import novaConfig from '../vite.config'

const upstreams: Server[] = []
let proxy: ViteDevServer
let address: string

async function upstream(owner: string) {
  const server = createHttpServer((request, response) => {
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({ owner, path: request.url, auth: request.headers.authorization }))
  })
  upstreams.push(server)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`
}

beforeAll(async () => {
  vi.stubEnv('NOVA_WEB_API_URL', await upstream('web'))
  vi.stubEnv('NOVA_QUERY_API_URL', await upstream('query'))
  const config = typeof novaConfig === 'function'
    ? await novaConfig({ command: 'serve', mode: 'test' })
    : await novaConfig
  proxy = await createServer({
    configFile: false,
    logLevel: 'silent',
    publicDir: false,
    optimizeDeps: { noDiscovery: true, entries: [] },
    server: {
      host: '127.0.0.1', port: 0, hmr: false, watch: null,
      preTransformRequests: false, proxy: config.server?.proxy,
    },
  })
  await proxy.listen()
  address = `http://127.0.0.1:${(proxy.httpServer?.address() as AddressInfo).port}`
})

afterAll(async () => {
  await proxy?.close()
  await Promise.all(upstreams.map((server) => new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve())
  })))
  vi.unstubAllEnvs()
})

it.each([
  ['/api/v1/query/execute', 'query'],
  ['/api/v1/ml/jobs', 'query'],
  ['/api/v1/agents', 'web'],
  ['/api/v1/agents/fixture/knowledge/consolidate', 'web'],
  ['/api/v1/assistant/chat', 'web'],
  ['/api/v1/intelligence/actions/fixture', 'web'],
  ['/api/v1/query-autopilot/policies', 'web'],
  ['/api/v1/queryish', 'web'],
  ['/api/v1/catalog/catalogs', 'web'],
])('routes %s to its metadata owner', async (path, owner) => {
  const auth = 'Bearer synthetic-routing-fixture'
  const response = await fetch(address + path, { headers: { Authorization: auth } })
  expect(response.status).toBe(200)
  expect(await response.json()).toEqual({ owner, path, auth })
})
