import test from 'node:test'
import assert from 'node:assert/strict'

import { ApiError, isAbort, request } from '../src/api.js'

function stubFetch(status: number, body: unknown, ok = status < 400): void {
  ;(globalThis as { fetch: unknown }).fetch = (async () => ({
    ok,
    status,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
    json: async () => body,
  })) as unknown as typeof fetch
}

test('错误信封解析为稳定 code/message', async () => {
  stubFetch(409, { detail: { code: 'job_running', message: '运行中的作业不能删除' } })
  await assert.rejects(
    request('/jobs/delete', { method: 'POST' }),
    (error: unknown) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 409)
      assert.equal(error.code, 'job_running')
      assert.equal(error.message, '运行中的作业不能删除')
      return true
    },
  )
})

test('非信封错误退化为 http_<status> 与默认消息', async () => {
  stubFetch(500, { unexpected: true })
  await assert.rejects(
    request('/x'),
    (error: unknown) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.code, 'http_500')
      assert.equal(error.message, '本地请求失败')
      return true
    },
  )
})

test('reimport 冲突携带 conflicts 与 reason', async () => {
  stubFetch(409, { detail: { code: 'reimport_conflict', message: '导入逻辑记录冲突', conflicts: ['a', 3, 'b'], reason: 'hash' } })
  await assert.rejects(
    request('/reimports', { method: 'POST' }),
    (error: unknown) => {
      assert.ok(error instanceof ApiError)
      assert.deepEqual(error.conflicts, ['a', 'b'])
      assert.equal(error.reason, 'hash')
      return true
    },
  )
})

test('成功响应解析 JSON，空响应返回 undefined', async () => {
  stubFetch(200, { items: [1, 2] })
  assert.deepEqual(await request<{ items: number[] }>('/search'), { items: [1, 2] })
  stubFetch(204, undefined)
  assert.equal(await request('/jobs/x/cancel', { method: 'POST' }), undefined)
})

test('isAbort 只识别 AbortError', () => {
  const abort = new DOMException('aborted', 'AbortError')
  assert.equal(isAbort(abort), true)
  assert.equal(isAbort(new Error('x')), false)
  assert.equal(isAbort(null), false)
})
