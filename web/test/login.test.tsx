import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test } from 'vitest'
import { server } from './server'
import { json, noContent, renderApp, unauthorized } from './render'

function loggedOut() {
  server.use(
    http.get('/api/auth/me', unauthorized),
    http.post('/api/auth/refresh', unauthorized),
  )
}

async function fillPassword(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Usuário'), 'joao')
  await user.type(screen.getByLabelText('Senha'), 'segredo')
  await user.click(screen.getByRole('button', { name: 'Continuar' }))
}

test('password_then_code_signs_in_and_goes_home', async () => {
  loggedOut()
  const sent: unknown[] = []
  server.use(
    http.post('/api/auth/login', async ({ request }) => {
      sent.push(await request.json())
      return noContent()
    }),
    http.post('/api/auth/2fa', async ({ request }) => {
      sent.push(await request.json())
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/login')
  await fillPassword(user)

  const code = await screen.findByLabelText('Código de 6 dígitos')
  expect(code).toHaveAttribute('inputmode', 'numeric')
  expect(code).toHaveAttribute('autocomplete', 'one-time-code')

  server.use(
    http.get('/api/auth/me', () => json({ username: 'joao' })),
    http.get('/api/actions', () => json([])),
  )
  await user.type(code, '123456')
  await user.click(screen.getByRole('button', { name: 'Entrar' }))

  await waitFor(() => expect(screen.getAllByRole('navigation', { name: 'Principal' }).length).toBeGreaterThan(0))
  expect(sent).toEqual([{ username: 'joao', password: 'segredo' }, { code: '123456' }])
})

test('wrong_code_stays_on_code_step_with_message', async () => {
  loggedOut()
  server.use(
    http.post('/api/auth/login', noContent),
    http.post('/api/auth/2fa', () => json({ detail: 'código inválido' }, 401)),
  )
  const user = userEvent.setup()
  renderApp('/login')
  await fillPassword(user)
  await user.type(await screen.findByLabelText('Código de 6 dígitos'), '000000')
  await user.click(screen.getByRole('button', { name: 'Entrar' }))
  expect(await screen.findByText('Código errado.')).toBeInTheDocument()
  expect(screen.getByLabelText('Código de 6 dígitos')).toBeInTheDocument()
})

test('locked_ip_shows_blocked_card', async () => {
  loggedOut()
  server.use(http.post('/api/auth/login', () => json({ detail: 'login bloqueado' }, 423)))
  const user = userEvent.setup()
  renderApp('/login')
  await fillPassword(user)
  expect(await screen.findByText('Login bloqueado')).toBeInTheDocument()
  expect(screen.getByText(/O desbloqueio é feito no servidor/)).toBeInTheDocument()
})

test('recovery_code_uses_the_same_endpoint', async () => {
  loggedOut()
  const sent: unknown[] = []
  server.use(
    http.post('/api/auth/login', noContent),
    http.post('/api/auth/2fa', async ({ request }) => {
      sent.push(await request.json())
      return json({ detail: 'código inválido' }, 401)
    }),
  )
  const user = userEvent.setup()
  renderApp('/login')
  await fillPassword(user)
  await user.click(await screen.findByRole('button', { name: /Usar um recovery code/ }))
  expect(screen.getByRole('heading', { name: 'Recovery code' })).toBeInTheDocument()
  await user.type(screen.getByLabelText('Recovery code'), 'abcd-efgh')
  await user.click(screen.getByRole('button', { name: 'Entrar' }))
  await waitFor(() => expect(sent).toEqual([{ code: 'abcd-efgh' }]))
})

test('bad_credentials_show_inline_error_and_keep_username', async () => {
  loggedOut()
  server.use(http.post('/api/auth/login', () => json({ detail: 'credenciais inválidas' }, 401)))
  const user = userEvent.setup()
  renderApp('/login')
  await fillPassword(user)
  expect(await screen.findByText('Usuário ou senha incorretos.')).toBeInTheDocument()
  expect(screen.getByLabelText('Usuário')).toHaveValue('joao')
})

test('expired_mfa_cookie_returns_to_password_step', async () => {
  loggedOut()
  server.use(
    http.post('/api/auth/login', noContent),
    http.post('/api/auth/2fa', () => json({ detail: 'faça o login de novo' }, 401)),
  )
  const user = userEvent.setup()
  renderApp('/login')
  await fillPassword(user)
  await user.type(await screen.findByLabelText('Código de 6 dígitos'), '123456')
  await user.click(screen.getByRole('button', { name: 'Entrar' }))
  expect(await screen.findByText('Faça o login de novo.')).toBeInTheDocument()
  expect(screen.getByLabelText('Usuário')).toBeInTheDocument()
})
