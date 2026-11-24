import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { afterEach, expect, test, vi } from 'vitest'
import type { KeysStatus, Settings } from '../src/api/schemas'
import * as nav from '../src/lib/nav'
import { json, noContent, renderApp, signedIn } from './render'
import { server } from './server'

const keys: KeysStatus = { configured: true, has_backfill_key: false, validated_at: '2026-11-20T10:00:00Z', last_error: null }
const settings: Settings = {
  username: 'joao',
  email: null,
  role: 'member',
  has_password: true,
  has_totp: true,
  gmail_email: 'joao@gmail.com',
  llm_keys: keys,
}
const gmail = { email_address: 'joao@gmail.com', status: 'connected', last_sync_at: '2026-11-24T08:00:00Z', last_error: null }

afterEach(() => vi.restoreAllMocks())

function settingsApi(over: Partial<typeof settings> = {}) {
  server.use(
    http.get('/api/settings', () => json({ ...settings, ...over })),
    http.get('/api/gmail/status', () => json(gmail)),
  )
}

// --- warnings banner -----------------------------------------------------------

test('revoked_gmail_shows_reconnect_banner_that_opens_google', async () => {
  signedIn([], 'member')
  server.use(
    http.get('/api/warnings', () => json([{ kind: 'gmail_revoked' }])),
    http.post('/api/oauth/google/connect', () => json({ url: 'https://accounts.google.com/x' })),
  )
  const go = vi.spyOn(nav, 'navigateTo').mockImplementation(() => {})
  const user = userEvent.setup()
  renderApp('/')
  const banner = await screen.findByRole('region', { name: 'Avisos' })
  expect(within(banner).getByText(/Reconecte o Gmail/)).toBeInTheDocument()
  await user.click(within(banner).getByRole('button', { name: 'Reconectar Gmail' }))
  await waitFor(() => expect(go).toHaveBeenCalledWith('https://accounts.google.com/x'))
})

test('invalid_key_banner_points_to_settings', async () => {
  signedIn([], 'member')
  server.use(http.get('/api/warnings', () => json([{ kind: 'key_invalid' }])))
  renderApp('/')
  const banner = await screen.findByRole('region', { name: 'Avisos' })
  expect(within(banner).getByText(/Sua chave do Gemini parou de funcionar/)).toBeInTheDocument()
  expect(within(banner).getByRole('link', { name: 'Trocar a chave' })).toHaveAttribute('href', '/configuracoes')
})

test('no_warnings_no_banner', async () => {
  signedIn([], 'member')
  renderApp('/')
  await screen.findAllByRole('navigation', { name: 'Principal' })
  expect(screen.queryByRole('region', { name: 'Avisos' })).toBeNull()
})

test('api_no_account_sends_user_to_the_connect_screen', async () => {
  signedIn([], 'member')
  server.use(
    http.get('/api/emails/relevant', () => json({ detail: 'Conecte seu Gmail para continuar.', code: 'no_account' }, 409)),
  )
  renderApp('/')
  expect(await screen.findByRole('heading', { name: 'Conecte seu Gmail' })).toBeInTheDocument()
})

// --- nav ------------------------------------------------------------------------

test('admin_link_only_for_admins', async () => {
  signedIn([], 'member')
  const first = renderApp('/')
  const side = (await screen.findAllByRole('navigation', { name: 'Principal' }))[0]!
  expect(within(side).getByRole('link', { name: /Configurações/ })).toBeInTheDocument()
  expect(within(side).queryByRole('link', { name: /Administração/ })).toBeNull()
  first.unmount()
  signedIn([], 'admin')
  renderApp('/')
  const side2 = (await screen.findAllByRole('navigation', { name: 'Principal' }))[0]!
  expect(await within(side2).findByRole('link', { name: /Administração/ })).toBeInTheDocument()
})

test('member_opening_admin_is_sent_home', async () => {
  signedIn([], 'member')
  server.use(http.get('/api/emails/relevant', () => json([])))
  renderApp('/admin')
  await waitFor(() => expect(screen.queryByRole('heading', { name: 'Administração' })).toBeNull())
  expect(await screen.findByRole('heading', { name: /Relevantes|Nada/i })).toBeInTheDocument()
})

// --- settings -------------------------------------------------------------------

test('settings_shows_account_gmail_and_key_state', async () => {
  signedIn([], 'member')
  settingsApi()
  renderApp('/configuracoes')
  const gm = await screen.findByRole('region', { name: 'Gmail' })
  expect(await within(gm).findByText('Conectado')).toBeInTheDocument()
  const keysCard = await screen.findByRole('region', { name: 'Chaves do Gemini' })
  expect(await within(keysCard).findByText('Funcionando')).toBeInTheDocument()
  expect(within(await screen.findByRole('region', { name: 'Conta' })).getByText('joao@gmail.com')).toBeInTheDocument()
})

test('broken_key_can_be_replaced_and_the_server_message_shown', async () => {
  signedIn([], 'member')
  settingsApi({ llm_keys: { ...keys, last_error: 'API key not valid' } })
  let body: unknown
  server.use(
    http.put('/api/settings/llm-keys', async ({ request }) => {
      const sent = (await request.json()) as { app_key?: string }
      body = sent
      if (sent.app_key === 'ruim') return json({ detail: 'Essa chave não parece ser do Gemini.' }, 422)
      return json(keys)
    }),
  )
  const user = userEvent.setup()
  renderApp('/configuracoes')
  const card = await screen.findByRole('region', { name: 'Chaves do Gemini' })
  expect(await within(card).findByText('Parou de funcionar')).toBeInTheDocument()
  const field = within(card).getByLabelText('Trocar a chave')
  await user.type(field, 'ruim')
  await user.click(within(card).getByRole('button', { name: 'Conferir e salvar' }))
  expect(await within(card).findByText('Essa chave não parece ser do Gemini.')).toBeInTheDocument()
  await user.clear(field)
  await user.type(field, 'AIzaBOA')
  await user.click(within(card).getByRole('button', { name: 'Conferir e salvar' }))
  await waitFor(() => expect(body).toEqual({ app_key: 'AIzaBOA' }))
})

test('revoked_gmail_card_offers_reconnect', async () => {
  signedIn([], 'member')
  settingsApi()
  server.use(
    http.get('/api/gmail/status', () => json({ ...gmail, status: 'revoked' })),
    http.post('/api/oauth/google/connect', () => json({ url: '/google' })),
  )
  const go = vi.spyOn(nav, 'navigateTo').mockImplementation(() => {})
  const user = userEvent.setup()
  renderApp('/configuracoes')
  const gm = await screen.findByRole('region', { name: 'Gmail' })
  expect(await within(gm).findByText('Precisa reconectar')).toBeInTheDocument()
  await user.click(within(gm).getByRole('button', { name: 'Reconectar Gmail' }))
  await waitFor(() => expect(go).toHaveBeenCalledWith('/google'))
})

test('change_password_sends_both_and_reports_server_errors', async () => {
  signedIn([], 'member')
  settingsApi()
  const sent: unknown[] = []
  server.use(
    http.post('/api/auth/password', async ({ request }) => {
      sent.push(await request.json())
      return sent.length === 1 ? json({ detail: 'A senha atual não confere.' }, 403) : noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/configuracoes')
  const card = await screen.findByRole('region', { name: 'Senha' })
  await user.type(within(card).getByLabelText('Senha atual'), 'velha')
  await user.type(within(card).getByLabelText('Senha nova'), 'uma-senha-nova-123')
  await user.click(within(card).getByRole('button', { name: 'Trocar a senha' }))
  expect(await within(card).findByText('A senha atual não confere.')).toBeInTheDocument()
  await user.click(within(card).getByRole('button', { name: 'Trocar a senha' }))
  await waitFor(() => expect(sent).toHaveLength(2))
  expect(sent[1]).toEqual({ current_password: 'velha', new_password: 'uma-senha-nova-123' })
})

test('new_authenticator_shows_secret_and_recovery_codes_once', async () => {
  signedIn([], 'member')
  settingsApi()
  server.use(
    http.post('/api/auth/totp', () =>
      json({ otpauth_uri: 'otpauth://totp/x?secret=ABC', secret: 'ABCDEF', recovery_codes: ['aaaa-bbbb-cccc', 'dddd-eeee-ffff'] }),
    ),
  )
  const user = userEvent.setup()
  renderApp('/configuracoes')
  const card = await screen.findByRole('region', { name: 'Autenticador' })
  await user.type(within(card).getByLabelText('Senha'), 'minha-senha')
  await user.type(within(card).getByLabelText(/Código atual/), '123456')
  await user.click(within(card).getByRole('button', { name: 'Gerar autenticador novo' }))
  expect(await within(card).findByText('aaaa-bbbb-cccc')).toBeInTheDocument()
  expect(within(card).getByDisplayValue('ABCDEF')).toBeInTheDocument()
  await user.click(within(card).getByRole('button', { name: 'Guardei os códigos' }))
  expect(within(card).queryByText('aaaa-bbbb-cccc')).toBeNull()
})

test('sign_out_other_devices_reports_how_many', async () => {
  signedIn([], 'member')
  settingsApi()
  server.use(http.delete('/api/auth/sessions', () => json({ ended: 2 })))
  const user = userEvent.setup()
  renderApp('/configuracoes')
  await user.click(await screen.findByRole('button', { name: 'Sair dos outros aparelhos' }))
  expect(await screen.findByText('2 aparelhos desconectados.')).toBeInTheDocument()
})

// --- security page, per user -----------------------------------------------------

test('member_security_page_has_no_ip_block_section_and_never_asks_for_it', async () => {
  signedIn([], 'member')
  let asked = false
  server.use(
    http.get('/api/auth/sessions', () => json([])),
    http.get('/api/security/blocks', () => {
      asked = true
      return json({ global_lock: false, ips: [] })
    }),
    http.get('/api/security/events', () => json([])),
    http.get('/api/notifications', () => json([])),
  )
  renderApp('/seguranca')
  expect(await screen.findByText('Nenhum evento registrado.')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'Bloqueios' })).toBeNull()
  expect(asked).toBe(false)
})

// --- admin ------------------------------------------------------------------------

const account = {
  user_id: 2,
  email: 'maria@gmail.com',
  role: 'member',
  status: 'revoked',
  last_sync_at: '2026-11-24T08:00:00Z',
  watch_expires_at: null,
  error_kind: 'revoked',
  queue_depth: 7,
  dead_jobs: 1,
  key_ok: false,
}

function adminApi(invites: unknown[] = []) {
  signedIn([], 'admin')
  server.use(
    http.get('/api/admin/accounts', () => json([account])),
    http.get('/api/admin/invites', () => json(invites)),
    http.get('/api/admin/security/events', () => json([])),
  )
}

test('admin_sees_account_health_and_nothing_about_content', async () => {
  adminApi()
  renderApp('/admin')
  const card = await screen.findByRole('region', { name: 'Contas' })
  const row = (await within(card).findByText('maria@gmail.com')).closest('tr')!
  expect(within(row).getByText('revogado')).toBeInTheDocument()
  expect(within(row).getByText('acesso revogado')).toBeInTheDocument()
  expect(within(row).getByText('inválida')).toBeInTheDocument()
  expect(within(row).getByText(/1 com falha/)).toBeInTheDocument()
  expect(within(row).getByText('7', { exact: false })).toBeInTheDocument()
})

test('admin_creates_invite_and_gets_copyable_link_then_revokes_another', async () => {
  adminApi([{ id: 9, email: 'amiga@gmail.com', state: 'pending', created_at: '2026-11-24T08:00:00Z' }])
  let created = ''
  let revoked = ''
  server.use(
    http.post('/api/admin/invites', async ({ request }) => {
      created = ((await request.json()) as { email: string }).email
      return json({ link: '/entrar?convite=abc' }, 201)
    }),
    http.delete('/api/admin/invites/:id', ({ params }) => {
      revoked = String(params.id)
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/admin')
  const card = await screen.findByRole('region', { name: 'Convites' })
  await user.type(within(card).getByLabelText('E-mail da pessoa'), 'novo@gmail.com')
  await user.click(within(card).getByRole('button', { name: 'Convidar' }))
  const link = await within(card).findByLabelText('Link do convite')
  expect(link).toHaveValue(`${window.location.origin}/entrar?convite=abc`)
  expect(created).toBe('novo@gmail.com')
  await user.click(within(card).getByRole('button', { name: 'Revogar convite de amiga@gmail.com' }))
  await waitFor(() => expect(revoked).toBe('9'))
})

test('reset_2fa_asks_for_confirmation_first', async () => {
  adminApi()
  let called = 0
  server.use(
    http.post('/api/admin/users/2/reset-2fa', () => {
      called += 1
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/admin')
  await user.click(await screen.findByRole('button', { name: 'Resetar 2FA de maria@gmail.com' }))
  expect(called).toBe(0)
  await user.click(screen.getByRole('button', { name: 'Confirmar' }))
  await waitFor(() => expect(called).toBe(1))
})

// --- onboarding step 6 ---------------------------------------------------------------

test('finish_screen_sends_the_backfill_choice_and_goes_home', async () => {
  signedIn([], 'member')
  let body: unknown
  server.use(
    http.post('/api/onboarding/finish', async ({ request }) => {
      body = await request.json()
      return json({ backfill_started: false })
    }),
    http.get('/api/emails/relevant', () => json([])),
  )
  const user = userEvent.setup()
  renderApp('/onboarding/pronto')
  expect(await screen.findByRole('heading', { name: 'Tudo pronto' })).toBeInTheDocument()
  expect(screen.getByText('Passo 6 de 6')).toBeInTheDocument()
  await user.click(screen.getByRole('switch', { name: /e-mails antigos/ }))
  await user.click(screen.getByRole('button', { name: 'Começar sem o histórico' }))
  await waitFor(() => expect(body).toEqual({ backfill: false }))
  expect(await screen.findAllByRole('navigation', { name: 'Principal' })).not.toHaveLength(0)
})

test('finish_screen_defaults_to_organizing_old_mail', async () => {
  signedIn([], 'member')
  renderApp('/onboarding/pronto')
  expect(await screen.findByRole('switch', { name: /e-mails antigos/ })).toHaveAttribute('aria-checked', 'true')
})
