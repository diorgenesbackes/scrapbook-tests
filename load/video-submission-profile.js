const loopback = /^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/
const publicCode = /^[A-Za-z0-9_-]{43}$/
const profiles = {
  warmup: { vus: 1, iterations: 1 },
  resume: { vus: 4, iterations: 2 },
  admission: { vus: 40, iterations: 5 },
}

export function videoLoadProfile({ api, storage, codes, noticeCode, profile }) {
  if (!loopback.test(api ?? '') || !loopback.test(storage ?? '') || api === storage
      || codes.length !== 2 || codes.some(code => !publicCode.test(code ?? '')) || codes[0] === codes[1]
      || noticeCode !== 'fixture-f08-v1' || !Object.hasOwn(profiles, profile)) {
    throw new Error('Informe o perfil e dois eventos na stack descartável de vídeo em loopback.')
  }
  return profiles[profile]
}
