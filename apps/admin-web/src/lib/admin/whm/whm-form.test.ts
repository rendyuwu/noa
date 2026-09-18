import { describe, expect, it } from 'vitest'

import type { WhmServer } from './types'
import {
  EMPTY_WHM_FORM,
  buildWhmCreatePayload,
  buildWhmUpdatePayload,
  validateWhmServerForm,
  whmFormStateFromServer,
  type WhmServerFormState,
} from './whm-form'

const server: WhmServer = {
  id: 'server-1',
  name: 'web1',
  base_url: 'https://whm.example.com:2087',
  api_username: 'root',
  is_reseller_credential: false,
  ssh_username: 'ubuntu',
  ssh_port: 2222,
  ssh_host_key_fingerprint: 'SHA256:abc',
  has_ssh_password: true,
  has_ssh_private_key: false,
  verify_ssl: true,
}

const form = (over: Partial<WhmServerFormState> = {}): WhmServerFormState => ({
  ...EMPTY_WHM_FORM,
  ...over,
})

// A stored row already marked reseller — name == api_username, so it already satisfies the
// rule that a reseller row must be named its api_username. Exists to prove the flag round-trips through the edit dialog untouched — the
// failure mode a fixture stuck at `false` cannot expose.
const resellerServer: WhmServer = {
  ...server,
  id: 'server-2',
  name: 'reseller-user',
  api_username: 'reseller-user',
  is_reseller_credential: true,
}

describe('whmFormStateFromServer', () => {
  it('seeds non-secret fields and leaves every secret blank', () => {
    const seeded = whmFormStateFromServer(server)
    expect(seeded.name).toBe('web1')
    expect(seeded.apiUsername).toBe('root')
    expect(seeded.sshUsername).toBe('ubuntu')
    expect(seeded.sshPort).toBe('2222')
    expect(seeded.enableSsh).toBe(true)
    expect(seeded.isResellerCredential).toBe(false)
    // Password-backed server opens in password auth mode.
    expect(seeded.sshAuthMode).toBe('password')
    // No secret is ever seeded from a server (the safe view carries none).
    expect(seeded.apiToken).toBe('')
    expect(seeded.sshPassword).toBe('')
    expect(seeded.sshPrivateKey).toBe('')
    expect(seeded.sshPrivateKeyPassphrase).toBe('')
  })

  // A `false` fixture cannot separate "seeded correctly" from "the field was never wired at
  // all" — both read `false`. This is the case that can.
  it('seeds isResellerCredential true from a row already marked reseller', () => {
    expect(whmFormStateFromServer(resellerServer).isResellerCredential).toBe(true)
  })
})

describe('validateWhmServerForm', () => {
  it('requires name, base URL, and API username', () => {
    expect(validateWhmServerForm(form(), 'create')?.field).toBe('name')
    expect(validateWhmServerForm(form({ name: 'a' }), 'create')?.field).toBe('baseUrl')
    expect(validateWhmServerForm(form({ name: 'a', baseUrl: 'b' }), 'create')?.field).toBe(
      'apiUsername',
    )
  })

  it('requires an API token on create only', () => {
    const base = form({ name: 'a', baseUrl: 'b', apiUsername: 'c' })
    expect(validateWhmServerForm(base, 'create')?.field).toBe('apiToken')
    // On update a blank token means "keep stored", so it is not required.
    expect(validateWhmServerForm(base, 'update', server)).toBeNull()
  })

  it('rejects an out-of-range SSH port', () => {
    const f = form({ name: 'a', baseUrl: 'b', apiUsername: 'c', apiToken: 't', enableSsh: true, sshPort: '70000', sshAuthMode: 'password', sshPassword: 'pw' })
    expect(validateWhmServerForm(f, 'create')?.field).toBe('sshPort')
  })

  it('requires a new private key on create but not when one is already stored', () => {
    const create = form({ name: 'a', baseUrl: 'b', apiUsername: 'c', apiToken: 't', enableSsh: true, sshAuthMode: 'private_key' })
    expect(validateWhmServerForm(create, 'create')?.field).toBe('sshPrivateKey')

    const stored: WhmServer = { ...server, has_ssh_password: false, has_ssh_private_key: true }
    const update = form({ name: 'a', baseUrl: 'b', apiUsername: 'c', enableSsh: true, sshAuthMode: 'private_key' })
    expect(validateWhmServerForm(update, 'update', stored)).toBeNull()
  })
})

// A reseller-credential row must resolve back to itself through
// resolve_whm_server_ref's name match. The server enforces this regardless of
// what the client sends; this is the legible early refusal, mirrored with the
// same trim+lowercase normalization as the backend's compare.
describe('validateWhmServerForm — reseller credential name/api-username constraint', () => {
  it('refuses a reseller credential whose name does not match its api username', () => {
    const mismatched = form({
      name: 'web1',
      baseUrl: 'b',
      apiUsername: 'reseller-user',
      apiToken: 't',
      isResellerCredential: true,
    })
    expect(validateWhmServerForm(mismatched, 'create')).toEqual({
      field: 'name',
      message: 'A reseller credential requires Name to match API username (case-insensitive).',
    })
  })

  it('accepts a reseller credential whose name matches api username case-insensitively', () => {
    const matched = form({
      name: 'Reseller-User',
      baseUrl: 'b',
      apiUsername: 'reseller-user',
      apiToken: 't',
      isResellerCredential: true,
    })
    expect(validateWhmServerForm(matched, 'create')).toBeNull()
  })

  it('does not apply the constraint to a non-reseller row', () => {
    const rootRow = form({
      name: 'web1',
      baseUrl: 'b',
      apiUsername: 'root',
      apiToken: 't',
      isResellerCredential: false,
    })
    expect(validateWhmServerForm(rootRow, 'create')).toBeNull()
  })
})

describe('buildWhmCreatePayload', () => {
  it('sends the token and only-configured SSH fields', () => {
    const payload = buildWhmCreatePayload(
      form({
        name: ' web1 ',
        baseUrl: ' https://whm:2087 ',
        apiUsername: ' root ',
        apiToken: ' TOKEN ',
        verifySsl: false,
        enableSsh: true,
        sshUsername: ' ubuntu ',
        sshPort: '2222',
        sshAuthMode: 'private_key',
        sshPrivateKey: ' KEY ',
        sshPrivateKeyPassphrase: ' PASS ',
      }),
    )
    expect(payload).toEqual({
      name: 'web1',
      base_url: 'https://whm:2087',
      api_username: 'root',
      api_token: 'TOKEN',
      verify_ssl: false,
      is_reseller_credential: false,
      ssh_username: 'ubuntu',
      ssh_port: 2222,
      ssh_private_key: 'KEY',
      ssh_private_key_passphrase: 'PASS',
    })
  })

  it('omits SSH entirely when SSH is disabled', () => {
    const payload = buildWhmCreatePayload(
      form({ name: 'web1', baseUrl: 'b', apiUsername: 'root', apiToken: 'T', enableSsh: false }),
    )
    expect(payload).toEqual({
      name: 'web1',
      base_url: 'b',
      api_username: 'root',
      api_token: 'T',
      verify_ssl: true,
      is_reseller_credential: false,
    })
  })

  it('carries is_reseller_credential through to the payload', () => {
    const payload = buildWhmCreatePayload(
      form({
        name: 'reseller-user',
        baseUrl: 'b',
        apiUsername: 'reseller-user',
        apiToken: 'T',
        isResellerCredential: true,
      }),
    )
    expect(payload.is_reseller_credential).toBe(true)
  })
})

describe('buildWhmUpdatePayload', () => {
  it('omits the token when left blank (keep stored) and clears SSH config when disabled', () => {
    const payload = buildWhmUpdatePayload(
      form({ name: 'web1', baseUrl: server.base_url, apiUsername: 'root', enableSsh: false }),
      server,
    )
    expect(payload).not.toHaveProperty('api_token')
    expect(payload).toEqual({
      name: 'web1',
      base_url: server.base_url,
      api_username: 'root',
      verify_ssl: true,
      is_reseller_credential: false,
      clear_ssh_configuration: true,
    })
  })

  it('carries is_reseller_credential through the update payload', () => {
    const payload = buildWhmUpdatePayload(
      form({
        name: 'root',
        baseUrl: server.base_url,
        apiUsername: 'root',
        enableSsh: false,
        isResellerCredential: true,
      }),
      server,
    )
    expect(payload.is_reseller_credential).toBe(true)
  })

  // The edit dialog's real path: seed from a stored reseller row, touch nothing, submit. A
  // regression that dropped or defaulted the flag anywhere in that round trip would pass every
  // other test here (they all build the form state by hand) but fail this one.
  it('keeps is_reseller_credential true end to end when the checkbox is never touched', () => {
    const payload = buildWhmUpdatePayload(
      whmFormStateFromServer(resellerServer),
      resellerServer,
    )
    expect(payload.is_reseller_credential).toBe(true)
  })

  it('sends a replacement token only when a new value is entered', () => {
    const payload = buildWhmUpdatePayload(
      form({ name: 'web1', baseUrl: server.base_url, apiUsername: 'root', apiToken: 'NEW', enableSsh: true, sshAuthMode: 'password', sshUsername: 'ubuntu', sshPort: '2222' }),
      server,
    )
    expect(payload.api_token).toBe('NEW')
  })

  it('switching from password to key clears the stored password', () => {
    const payload = buildWhmUpdatePayload(
      form({ name: 'web1', baseUrl: server.base_url, apiUsername: 'root', enableSsh: true, sshAuthMode: 'private_key', sshPrivateKey: 'KEY', sshUsername: 'ubuntu', sshPort: '2222' }),
      server,
    )
    expect(payload.clear_ssh_password).toBe(true)
    expect(payload.ssh_private_key).toBe('KEY')
    // No passphrase entered → the stored one is explicitly cleared.
    expect(payload.clear_ssh_private_key_passphrase).toBe(true)
  })
})
