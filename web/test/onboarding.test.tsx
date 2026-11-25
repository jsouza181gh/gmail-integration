import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { afterEach, expect, test, vi } from 'vitest'
import { server } from './server'
import { json, noContent, renderApp, signedIn } from './render'

afterEach(() => vi.restoreAllMocks())

const STEPS = ['gmail', 'totp', 'password_optional', 'llm_key', 'finish', 'done']
const state = (step: string | null, extra: Record<string, unknown> = {}) => ({
  step,
  steps: STEPS,
  done: step === 'done' || step === null,
  email: 'ana@example.com',
  has_password: false,
  totp_confirmed: false,
  ...extra,
})

test('totp_step_walks_install_scan_confirm_and_codes_then_advances', async () => {
  let current = state('totp')
  const calls: string[] = []
  server.use(
    http.get('/api/onboarding/state', () => json(current)),
    http.post('/api/onboarding/totp/start', () => {
      calls.push('start')
      return json({ secret: 'ABCD EFGH', uri: 'otpauth://totp/x' })
    }),
    http.post('/api/onboarding/totp/confirm', async ({ request }) => {
      const body = (await request.json()) as { code: string }
      calls.push(`confirm:${body.code}`)
      return json({ codes: ['aaaa-bbbb-cccc', 'dddd-eeee-ffff'] })
    }),
    http.post('/api/onboarding/recovery/ack', () => {
      calls.push('ack')
      current = state('password_optional')
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/onboarding')

  expect(await screen.findByText('Etapa 2 de 5: Proteção da conta')).toBeInTheDocument()
  expect(screen.getByText(/Google Authenticator/)).toBeInTheDocument()
  expect(screen.getByText(/Microsoft Authenticator/)).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: 'Android' })).toHaveLength(2)
  await user.click(screen.getByRole('button', { name: 'Já instalei o app' }))

  const qr = await screen.findByAltText('QR code para o app autenticador')
  expect(qr).toHaveAttribute('src', expect.stringContaining('/api/onboarding/totp/qr.svg'))
  expect(screen.getByText('ABCD EFGH')).toBeInTheDocument()
  await user.type(screen.getByLabelText(/Digite o código de 6 dígitos/), '123456')
  await user.click(screen.getByRole('button', { name: 'Confirmar código' }))

  expect(await screen.findByText('aaaa-bbbb-cccc')).toBeInTheDocument()
  const next = screen.getByRole('button', { name: 'Continuar' })
  expect(next).toBeDisabled() // not before "guardei"
  await user.click(screen.getByRole('checkbox', { name: /Guardei meus códigos/ }))
  expect(next).toBeEnabled()
  await user.click(next)

  expect(await screen.findByRole('heading', { name: /Quer também uma senha/ })).toBeInTheDocument()
  expect(calls).toEqual(['start', 'confirm:123456', 'ack'])
})

test('wrong_totp_code_shows_the_servers_plain_message', async () => {
  server.use(
    http.get('/api/onboarding/state', () => json(state('totp'))),
    http.post('/api/onboarding/totp/start', () => json({ secret: 'S', uri: 'otpauth://x' })),
    http.post('/api/onboarding/totp/confirm', () =>
      json({ detail: 'Esse código não confere. Confira o app e tente de novo.' }, 400),
    ),
  )
  const user = userEvent.setup()
  renderApp('/onboarding')
  await user.click(await screen.findByRole('button', { name: 'Já instalei o app' }))
  await user.type(await screen.findByLabelText(/Digite o código de 6 dígitos/), '000000')
  await user.click(screen.getByRole('button', { name: 'Confirmar código' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Esse código não confere')
  expect(screen.getByLabelText(/Digite o código de 6 dígitos/)).toBeInTheDocument()
})

test('codes_can_be_copied', async () => {
  server.use(
    http.get('/api/onboarding/state', () => json(state('totp'))),
    http.post('/api/onboarding/totp/start', () => json({ secret: 'S', uri: 'otpauth://x' })),
    http.post('/api/onboarding/totp/confirm', () => json({ codes: ['aaaa-bbbb-cccc', 'dddd-eeee-ffff'] })),
  )
  const user = userEvent.setup()
  renderApp('/onboarding')
  await user.click(await screen.findByRole('button', { name: 'Já instalei o app' }))
  await user.type(await screen.findByLabelText(/Digite o código de 6 dígitos/), '123456')
  await user.click(screen.getByRole('button', { name: 'Confirmar código' }))
  await user.click(await screen.findByRole('button', { name: 'Copiar códigos' }))
  await waitFor(async () =>
    expect(await navigator.clipboard.readText()).toBe('aaaa-bbbb-cccc\ndddd-eeee-ffff'),
  )
  expect(await screen.findByRole('button', { name: 'Copiados' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Baixar arquivo' })).toBeInTheDocument()
})

test('resuming_after_confirming_offers_to_redo_the_step', async () => {
  server.use(http.get('/api/onboarding/state', () => json(state('totp', { totp_confirmed: true }))))
  renderApp('/onboarding')
  expect(await screen.findByText(/só aparecem uma vez/)).toBeInTheDocument()
})

test('password_step_saves_then_the_gemini_key_comes_next', async () => {
  let current = state('password_optional')
  const sent: unknown[] = []
  server.use(
    http.get('/api/onboarding/state', () => json(current)),
    http.post('/api/onboarding/password', async ({ request }) => {
      sent.push(await request.json())
      current = state('llm_key')
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/onboarding')
  expect(await screen.findByText('Etapa 3 de 5: Senha (opcional)')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Salvar senha' })).toBeDisabled()
  await user.type(screen.getByLabelText(/Senha \(mínimo 12 caracteres\)/), 'uma-senha-bem-longa')
  await user.click(screen.getByRole('button', { name: 'Salvar senha' }))
  expect(await screen.findByText('Etapa 4 de 5: Chave do Gemini')).toBeInTheDocument()
  expect(sent).toEqual([{ password: 'uma-senha-bem-longa' }])
})

test('skipping_the_password_goes_to_the_gemini_key', async () => {
  let current = state('password_optional')
  server.use(
    http.get('/api/onboarding/state', () => json(current)),
    http.post('/api/onboarding/password/skip', () => {
      current = state('llm_key')
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/onboarding')
  await user.click(await screen.findByRole('button', { name: 'Não quero senha agora' }))
  expect(await screen.findByRole('heading', { name: 'Chave do Gemini' })).toBeInTheDocument()
})

test('a_working_gemini_key_leads_to_the_finish_screen', async () => {
  let current = state('llm_key')
  server.use(
    http.get('/api/onboarding/state', () => json(current)),
    http.post('/api/onboarding/llm-key', () => {
      current = state('finish')
      return json({ configured: true, has_backfill_key: false, validated_at: null, last_error: null, next_step: 'finish' })
    }),
  )
  const user = userEvent.setup()
  renderApp('/onboarding')
  expect(await screen.findByText('Etapa 4 de 5: Chave do Gemini')).toBeInTheDocument()
  await user.type(screen.getByLabelText('Sua chave'), 'AIza-abc')
  await user.click(screen.getByRole('button', { name: 'Testar e salvar' }))
  await user.click(await screen.findByRole('button', { name: 'Continuar' }))
  expect(await screen.findByRole('heading', { name: 'Tudo pronto' })).toBeInTheDocument()
})

test('a_user_with_unfinished_onboarding_is_sent_there_from_the_panel', async () => {
  server.use(
    http.get('/api/auth/me', () => json({ username: 'ana', onboarding_step: 'totp', onboarding_done: false })),
    http.get('/api/onboarding/state', () => json(state('totp'))),
  )
  renderApp('/caixa')
  expect(await screen.findByText(/Etapa 2 de 5/)).toBeInTheDocument()
})

test('an_unknown_future_step_does_not_crash', async () => {
  server.use(http.get('/api/onboarding/state', () => json({ ...state('x'), steps: ['x', ...STEPS] })))
  renderApp('/onboarding')
  expect(await screen.findByText(/ainda não está disponível/)).toBeInTheDocument()
})

test('expired_onboarding_cookie_goes_to_login', async () => {
  server.use(
    http.get('/api/onboarding/state', () => json({ detail: 'sessão expirada' }, 401)),
    http.post('/api/auth/refresh', () => json({ detail: 'x' }, 401)),
  )
  renderApp('/onboarding')
  expect(await screen.findByRole('heading', { name: 'Entrar no Inbox Agent' })).toBeInTheDocument()
})

test('existing_users_without_the_new_fields_still_open_the_panel', async () => {
  signedIn([])
  renderApp('/')
  expect((await screen.findAllByRole('navigation', { name: 'Principal' })).length).toBeGreaterThan(0)
})
