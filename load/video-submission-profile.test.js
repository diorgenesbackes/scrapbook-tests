import assert from 'node:assert/strict'
import { test } from 'node:test'

import { videoLoadProfile } from './video-submission-profile.js'

const inputs = {
  api: 'http://127.0.0.1:11001',
  storage: 'http://127.0.0.1:11002',
  codes: ['a'.repeat(43), 'b'.repeat(43)],
  noticeCode: 'fixture-f08-v1',
  profile: 'resume',
}

test('define os tetos fixos dos três perfis', () => {
  assert.deepEqual(videoLoadProfile({ ...inputs, profile: 'warmup' }), { vus: 1, iterations: 1 })
  assert.deepEqual(videoLoadProfile(inputs), { vus: 4, iterations: 2 })
  assert.deepEqual(videoLoadProfile({ ...inputs, profile: 'admission' }), { vus: 40, iterations: 5 })
})

for (const [name, changes] of [
  ['API ausente', { api: undefined }],
  ['API remota', { api: 'https://example.invalid:11001' }],
  ['armazenamento ausente', { storage: undefined }],
  ['armazenamento remoto', { storage: 'http://example.invalid:11002' }],
  ['origens iguais', { storage: inputs.api }],
  ['evento único', { codes: inputs.codes.slice(0, 1) }],
  ['evento repetido', { codes: [inputs.codes[0], inputs.codes[0]] }],
  ['código ausente', { codes: [inputs.codes[0], undefined] }],
  ['código inválido', { codes: [inputs.codes[0], 'x'] }],
  ['aviso divergente', { noticeCode: 'fixture-outro' }],
  ['perfil desconhecido', { profile: 'toString' }],
  ['perfil ausente', { profile: undefined }],
]) {
  test('recusa ' + name, () => {
    assert.throws(() => videoLoadProfile({ ...inputs, ...changes }), /stack descartável/)
  })
}
