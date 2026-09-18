import { describe, expect, it } from 'vitest'

import type { PmgServer } from './types'
import {
  EMPTY_PMG_FORM,
  buildPmgCreatePayload,
  buildPmgUpdatePayload,
  pmgFormStateFromServer,
  validatePmgServerForm,
  type PmgServerFormState,
} from './pmg-form'

const server: PmgServer = {
  id: 'server-1',
  name: 'pmg1',
  ssh_host: 'pmg.example.com',
  ssh_username: 'ubuntu',
  ssh_port: 2222,
  ssh_host_key_fingerprint: 'SHA256:abc',
  has_ssh_password: true,
  has_ssh_private_key: false,
}

const form = (over: Partial<PmgServerFormState> = {}): PmgServerFormState => ({
  ...EMPTY_PMG_FORM,
  ...over,
})

describe('pmgFormStateFromServer', () => {
  it('seeds non-secret fields and leaves every secret blank', () => {
    const seeded = pmgFormStateFromServer(server)
    expect(seeded.name).toBe('pmg1')
    expect(seeded.sshHost).toBe('pmg.example.com')
    expect(seeded.sshUsername).toBe('ubuntu')
    expect(seeded.sshPort).toBe('2222')
    expect(seeded.sshHostKeyFingerprint).toBe('SHA256:abc')
    // Password-backed server opens in password auth mode.
    expect(seeded.sshAuthMode).toBe('password')
    // No secret is ever seeded from a server (the safe view carries none).
    expect(seeded.sshPassword).toBe('')
    expect(seeded.sshPrivateKey).toBe('')
    expect(seeded.sshPrivateKeyPassphrase).toBe('')
  })

  it('opens a key-backed server in private_key auth mode', () => {
    const keyServer: PmgServer = { ...server, has_ssh_password: false, has_ssh_private_key: true }
    expect(pmgFormStateFromServer(keyServer).sshAuthMode).toBe('private_key')
  })
})

describe('validatePmgServerForm', () => {
  it('requires name and SSH host in order', () => {
    expect(validatePmgServerForm(form(), 'create')?.field).toBe('name')
    expect(validatePmgServerForm(form({ name: 'a' }), 'create')?.field).toBe('sshHost')
  })

  it('rejects an out-of-range SSH port', () => {
    const f = form({
      name: 'a',
      sshHost: 'h',
      sshPort: '70000',
      sshAuthMode: 'password',
      sshPassword: 'pw',
    })
    expect(validatePmgServerForm(f, 'create')?.field).toBe('sshPort')
  })

  it('requires a password on create when password auth is selected', () => {
    const f = form({ name: 'a', sshHost: 'h', sshAuthMode: 'password' })
    expect(validatePmgServerForm(f, 'create')?.field).toBe('sshPassword')
  })

  it('requires a new private key on create but not when one is already stored', () => {
    const create = form({ name: 'a', sshHost: 'h', sshAuthMode: 'private_key' })
    expect(validatePmgServerForm(create, 'create')?.field).toBe('sshPrivateKey')

    const stored: PmgServer = { ...server, has_ssh_password: false, has_ssh_private_key: true }
    const update = form({ name: 'a', sshHost: 'h', sshAuthMode: 'private_key' })
    expect(validatePmgServerForm(update, 'update', stored)).toBeNull()
  })

  it('requires a new password when switching a key-backed server to password auth', () => {
    const stored: PmgServer = { ...server, has_ssh_password: false, has_ssh_private_key: true }
    const update = form({ name: 'a', sshHost: 'h', sshAuthMode: 'password' })
    expect(validatePmgServerForm(update, 'update', stored)?.field).toBe('sshPassword')
  })
})

describe('buildPmgCreatePayload', () => {
  it('trims fields and sends only-configured SSH fields with the private key', () => {
    const payload = buildPmgCreatePayload(
      form({
        name: ' pmg1 ',
        sshHost: ' pmg.example.com ',
        sshUsername: ' ubuntu ',
        sshPort: '2222',
        sshHostKeyFingerprint: ' SHA256:host ',
        sshAuthMode: 'private_key',
        sshPrivateKey: ' KEY ',
        sshPrivateKeyPassphrase: ' PASS ',
      }),
    )
    expect(payload).toEqual({
      name: 'pmg1',
      ssh_host: 'pmg.example.com',
      ssh_username: 'ubuntu',
      ssh_port: 2222,
      ssh_host_key_fingerprint: 'SHA256:host',
      ssh_private_key: 'KEY',
      ssh_private_key_passphrase: 'PASS',
    })
  })

  it('omits optional SSH fields left blank and sends a password', () => {
    const payload = buildPmgCreatePayload(
      form({ name: 'pmg1', sshHost: 'h', sshAuthMode: 'password', sshPassword: 'pw' }),
    )
    expect(payload).toEqual({ name: 'pmg1', ssh_host: 'h', ssh_password: 'pw' })
  })
})

describe('buildPmgUpdatePayload', () => {
  it('sends a replacement fingerprint and keeps the stored password when left blank', () => {
    const payload = buildPmgUpdatePayload(
      form({
        name: 'pmg1',
        sshHost: server.ssh_host,
        sshUsername: 'ubuntu',
        sshPort: '2222',
        sshHostKeyFingerprint: 'SHA256:new',
        sshAuthMode: 'password',
      }),
      server,
    )
    expect(payload.ssh_host_key_fingerprint).toBe('SHA256:new')
    // Blank password keeps the stored one — nothing sent, nothing cleared.
    expect(payload).not.toHaveProperty('ssh_password')
    expect(payload).not.toHaveProperty('clear_ssh_password')
  })

  it('clears the fingerprint when blanked on a server that had one', () => {
    const payload = buildPmgUpdatePayload(
      form({ name: 'pmg1', sshHost: server.ssh_host, sshAuthMode: 'password' }),
      server,
    )
    expect(payload.clear_ssh_host_key_fingerprint).toBe(true)
    // Username/port were stored but are now blank → explicit clears.
    expect(payload.clear_ssh_username).toBe(true)
    expect(payload.clear_ssh_port).toBe(true)
  })

  it('switching from password to key clears the stored password', () => {
    const payload = buildPmgUpdatePayload(
      form({
        name: 'pmg1',
        sshHost: server.ssh_host,
        sshUsername: 'ubuntu',
        sshPort: '2222',
        sshAuthMode: 'private_key',
        sshPrivateKey: 'KEY',
      }),
      server,
    )
    expect(payload.clear_ssh_password).toBe(true)
    expect(payload.ssh_private_key).toBe('KEY')
    // No passphrase entered → the stored one is explicitly cleared.
    expect(payload.clear_ssh_private_key_passphrase).toBe(true)
  })
})
