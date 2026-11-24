import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { afterEach, expect, test, vi } from 'vitest'
import * as nav from '../src/lib/nav'
import { server } from './server'
import { json, renderApp, unauthorized } from './render'

afterEach(() => vi.restoreAllMocks())

function loggedOut() {
  server.use(http.get('/api/auth/me', unauthorized), http.post('/api/auth/refresh', unauthorized))
}

test('invite_page_explains_the_google_warning_before_the_redirect', async () => {
  loggedOut()
  server.use(http.post('/api/entry/invite', () => json({ email: 'ana@example.com' })))
  renderApp('/entrar?convite=tok123')
  expect(await screen.findByRole('heading', { name: /Você foi convidado/ })).toBeInTheDocument()
  expect(await screen.findByText('Convite para ana@example.com')).toBeInTheDocument()
  // the unverified-app screens are named exactly as Google words them
  expect(screen.getByRole('heading', { name: /o Google vai mostrar um aviso/ })).toBeInTheDocument()
  expect(screen.getAllByText(/O Google não verificou este app/).length).toBeGreaterThan(0)
  expect(screen.getAllByText(/Avançado/).length).toBeGreaterThan(0)
  expect(screen.getAllByText(/Acessar Inbox Agent \(não seguro\)/).length).toBeGreaterThan(0)
  expect(screen.getByText(/marque todas as caixinhas/)).toBeInTheDocument()
})

test('continue_sends_the_token_and_goes_to_googles_url', async () => {
  loggedOut()
  const go = vi.spyOn(nav, 'navigateTo').mockImplementation(() => {})
  const sent: unknown[] = []
  server.use(
    http.post('/api/entry/invite', () => json({ email: 'ana@example.com' })),
    http.post('/api/entry/signup', async ({ request }) => {
      sent.push(await request.json())
      return json({ url: 'https://accounts.google.com/auth?x=1' })
    }),
  )
  const user = userEvent.setup()
  renderApp('/entrar?convite=tok123')
  const button = await screen.findByRole('button', { name: 'Continuar com o Google' })
  await waitFor(() => expect(button).toBeEnabled())
  await user.click(button)
  await waitFor(() => expect(go).toHaveBeenCalledWith('https://accounts.google.com/auth?x=1'))
  expect(sent).toEqual([{ token: 'tok123' }])
})

test('a_dead_invite_says_so_and_offers_no_google_button', async () => {
  loggedOut()
  server.use(
    http.post('/api/entry/invite', () => json({ detail: 'Esse convite não vale mais. Peça um novo link.' }, 404)),
  )
  renderApp('/entrar?convite=velho')
  expect(await screen.findByText(/Esse convite não vale mais/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Continuar com o Google' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Já tenho conta/ })).toBeInTheDocument()
})

test('without_a_token_the_page_asks_for_the_link', async () => {
  loggedOut()
  renderApp('/entrar')
  expect(await screen.findByText(/Abra o link do convite que você recebeu/)).toBeInTheDocument()
})

test.each([
  ['permissao', /marque todas as caixinhas antes de continuar/],
  ['outro_email', /conta do Google diferente da que foi convidada/],
  ['falha', /nada foi guardado e o convite continua valendo/],
])('google_problem_%s_is_explained_in_plain_words', async (code, text) => {
  loggedOut()
  server.use(http.post('/api/entry/invite', () => json({ email: 'ana@example.com' })))
  renderApp(`/entrar?convite=tok123&erro=${code}`)
  expect(await screen.findByRole('alert')).toHaveTextContent(text)
  expect(await screen.findByRole('button', { name: 'Continuar com o Google' })).toBeInTheDocument()
})

test('not_invited_page_is_friendly', async () => {
  loggedOut()
  renderApp('/nao-convidado')
  expect(await screen.findByRole('heading', { name: 'Você ainda não foi convidado' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Entrar' })).toHaveAttribute('href', '/login')
})

test('login_offers_google_and_goes_to_the_returned_url', async () => {
  loggedOut()
  const go = vi.spyOn(nav, 'navigateTo').mockImplementation(() => {})
  server.use(http.post('/api/entry/login', () => json({ url: 'https://accounts.google.com/auth?login=1' })))
  const user = userEvent.setup()
  renderApp('/login')
  await user.click(await screen.findByRole('button', { name: 'Entrar com o Google' }))
  await waitFor(() => expect(go).toHaveBeenCalledWith('https://accounts.google.com/auth?login=1'))
})

test('returning_from_google_lands_on_the_code_step', async () => {
  loggedOut()
  renderApp('/login?etapa=codigo')
  expect(await screen.findByLabelText('Código de 6 dígitos')).toBeInTheDocument()
})

test('google_errors_show_on_the_login_page', async () => {
  loggedOut()
  renderApp('/login?erro=ja_tem_conta')
  expect(await screen.findByRole('alert')).toHaveTextContent(/já tem conta/)
  renderApp('/login?erro=bloqueado')
  expect(await screen.findByText('Login bloqueado')).toBeInTheDocument()
})
