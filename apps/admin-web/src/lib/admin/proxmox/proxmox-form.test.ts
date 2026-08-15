import { describe, expect, it } from 'vitest'

import type { ProxmoxServer } from './types'
import {
  EMPTY_PROXMOX_FORM,
  buildProxmoxCreatePayload,
  buildProxmoxUpdatePayload,
  clearProxmoxSecrets,
  proxmoxFormStateFromServer,
  validateProxmoxServerForm,
  type ProxmoxServerFormState,
} from './proxmox-form'

const server: ProxmoxServer = {
  id: 'server-1',
  name: 'pve1',
  base_url: 'https://pve.example.com:8006',
  api_token_id: 'root@pam!noa',
  has_api_token_secret: true,
  verify_ssl: true,
}

const form = (over: Partial<ProxmoxServerFormState> = {}): ProxmoxServerFormState => ({
  ...EMPTY_PROXMOX_FORM,
  ...over,
})

describe('proxmoxFormStateFromServer', () => {
  it('seeds non-secret fields and leaves the secret blank', () => {
    const seeded = proxmoxFormStateFromServer(server)
    expect(seeded.name).toBe('pve1')
    expect(seeded.baseUrl).toBe('https://pve.example.com:8006')
    expect(seeded.apiTokenId).toBe('root@pam!noa')
    expect(seeded.verifySsl).toBe(true)
    // No secret is ever seeded from a server (the safe view carries none).
    expect(seeded.apiTokenSecret).toBe('')
  })
})

describe('clearProxmoxSecrets', () => {
  it('blanks the secret field but keeps non-secret input', () => {
    const dirty = form({ name: 'pve1', apiTokenId: 'root@pam!noa', apiTokenSecret: 'SECRET' })
    const cleared = clearProxmoxSecrets(dirty)
    expect(cleared.name).toBe('pve1')
    expect(cleared.apiTokenId).toBe('root@pam!noa')
    expect(cleared.apiTokenSecret).toBe('')
  })
})

describe('validateProxmoxServerForm', () => {
  it('requires name, base URL, and API token ID in order', () => {
    expect(validateProxmoxServerForm(form(), 'create')?.field).toBe('name')
    expect(validateProxmoxServerForm(form({ name: 'a' }), 'create')?.field).toBe('baseUrl')
    expect(validateProxmoxServerForm(form({ name: 'a', baseUrl: 'b' }), 'create')?.field).toBe(
      'apiTokenId',
    )
  })

  it('requires an API token secret on create only', () => {
    const base = form({ name: 'a', baseUrl: 'b', apiTokenId: 'c' })
    expect(validateProxmoxServerForm(base, 'create')?.field).toBe('apiTokenSecret')
    // On update a blank secret means "keep stored", so it is not required.
    expect(validateProxmoxServerForm(base, 'update')).toBeNull()
  })
})

describe('buildProxmoxCreatePayload', () => {
  it('trims fields and always sends the secret', () => {
    const payload = buildProxmoxCreatePayload(
      form({
        name: ' pve1 ',
        baseUrl: ' https://pve:8006 ',
        apiTokenId: ' root@pam!noa ',
        apiTokenSecret: ' SECRET ',
        verifySsl: true,
      }),
    )
    expect(payload).toEqual({
      name: 'pve1',
      base_url: 'https://pve:8006',
      api_token_id: 'root@pam!noa',
      api_token_secret: 'SECRET',
      verify_ssl: true,
    })
  })
})

describe('buildProxmoxUpdatePayload', () => {
  it('omits the secret when left blank (keep stored)', () => {
    const payload = buildProxmoxUpdatePayload(
      form({ name: 'pve1', baseUrl: server.base_url, apiTokenId: 'root@pam!noa', verifySsl: false }),
    )
    expect(payload).not.toHaveProperty('api_token_secret')
    expect(payload).toEqual({
      name: 'pve1',
      base_url: server.base_url,
      api_token_id: 'root@pam!noa',
      verify_ssl: false,
    })
  })

  it('sends a replacement secret only when a new value is entered', () => {
    const payload = buildProxmoxUpdatePayload(
      form({
        name: 'pve1',
        baseUrl: server.base_url,
        apiTokenId: 'root@pam!noa',
        apiTokenSecret: 'NEW_SECRET',
        verifySsl: true,
      }),
    )
    expect(payload.api_token_secret).toBe('NEW_SECRET')
  })
})
