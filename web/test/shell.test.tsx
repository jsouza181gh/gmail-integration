import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test } from 'vitest'
import { action } from './fixtures'
import { server } from './server'
import { json, noContent, renderApp, signedIn, unauthorized } from './render'

const pending = (n: number) => Array.from({ length: n }, (_, i) => ({ ...action, id: i + 1 }))

test('without_a_session_any_route_goes_to_login', async () => {
  server.use(
    http.get('/api/auth/me', unauthorized),
    http.post('/api/auth/refresh', unauthorized),
  )
  renderApp('/caixa')
  expect(await screen.findByRole('heading', { name: 'Entrar no Inbox Agent' })).toBeInTheDocument()
})

test('signed_in_shows_nav_with_current_tab_and_pending_pill', async () => {
  signedIn(pending(12))
  renderApp('/aprovacoes')
  const side = (await screen.findAllByRole('navigation', { name: 'Principal' }))[0]!
  const link = within(side).getByRole('link', { name: /Aprovações/ })
  expect(link).toHaveAttribute('aria-current', 'page')
  expect(await within(link).findByText('12')).toBeInTheDocument()
  expect(within(side).getByRole('link', { name: /Caixa/ })).not.toHaveAttribute('aria-current')
  expect(within(side).getByText('joao')).toBeInTheDocument()
})

test('zero_pending_hides_the_pill', async () => {
  signedIn([])
  renderApp('/')
  const side = (await screen.findAllByRole('navigation', { name: 'Principal' }))[0]!
  const link = within(side).getByRole('link', { name: /Aprovações/ })
  expect(link.querySelector('.ia-pill')).toBeNull()
})

test('sign_out_calls_logout_and_returns_to_login', async () => {
  signedIn([])
  let called = false
  server.use(
    http.post('/api/auth/logout', () => {
      called = true
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/')
  await user.click(await screen.findByRole('button', { name: 'Sair' }))
  expect(await screen.findByRole('heading', { name: 'Entrar no Inbox Agent' })).toBeInTheDocument()
  expect(called).toBe(true)
})

test('lost_session_while_using_the_app_redirects_to_login', async () => {
  server.use(
    http.get('/api/auth/me', () => json({ username: 'joao' })),
    http.get('/api/actions', unauthorized),
    http.post('/api/auth/refresh', unauthorized),
  )
  renderApp('/')
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Entrar no Inbox Agent' })).toBeInTheDocument())
})

test('more_sheet_lists_system_pages_on_mobile_nav', async () => {
  signedIn([])
  server.use(http.get('/api/backfill', () => json(backfillPaused())))
  const user = userEvent.setup()
  renderApp('/')
  const bottom = (await screen.findAllByRole('navigation', { name: 'Principal' }))[1]!
  await user.click(within(bottom).getByRole('button', { name: /Mais/ }))
  const sheet = await screen.findByRole('navigation', { name: 'Mais' })
  for (const name of ['Chat', 'Agente', 'Execuções', 'Segurança']) {
    expect(within(sheet).getByRole('link', { name: new RegExp(name) })).toBeInTheDocument()
  }
  expect(await within(sheet).findByText('pausado')).toBeInTheDocument()
})

function backfillPaused() {
  return {
    progress: {
      run_id: 1, pages: 3, fetch_done: false, emails: 10, classified: 10, indexed: 10, embedded: 10,
      attachments: {}, jobs: {},
    },
    mailbox_total: 100, percent: 10, pages_last_hour: 0, pages_last_day: 5, eta_hours: null,
    deferred_until: '2999-01-01T00:00:00Z',
    paused: false,
  }
}
