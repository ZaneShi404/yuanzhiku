import test from 'node:test'
import assert from 'node:assert/strict'

import { LatestRequestGate } from '../src/asyncGate.js'

test('begin 序号递增且中止上一个请求', () => {
  const gate = new LatestRequestGate()
  const first = gate.begin('key')
  assert.equal(first.epoch, 1)
  assert.equal(first.signal.aborted, false)

  const second = gate.begin('key')
  assert.equal(second.epoch, 2)
  assert.equal(first.signal.aborted, true, '旧请求必须被 abort')
  assert.equal(second.signal.aborted, false)
})

test('isCurrent 只认最新序号', () => {
  const gate = new LatestRequestGate()
  const first = gate.begin('k')
  const second = gate.begin('k')
  assert.equal(gate.isCurrent('k', first.epoch), false, '陈旧响应不得覆盖状态')
  assert.equal(gate.isCurrent('k', second.epoch), true)
})

test('不同 key 互不影响', () => {
  const gate = new LatestRequestGate()
  const a = gate.begin('a')
  gate.begin('b')
  assert.equal(gate.isCurrent('a', a.epoch), true)
  assert.equal(a.signal.aborted, false)
})

test('cancel 与 cancelAll 中止并清理', () => {
  const gate = new LatestRequestGate()
  const a = gate.begin('a')
  const b = gate.begin('b')
  gate.cancel('a')
  assert.equal(a.signal.aborted, true)
  assert.equal(gate.isCurrent('a', a.epoch), false)
  gate.cancelAll()
  assert.equal(b.signal.aborted, true)
})
