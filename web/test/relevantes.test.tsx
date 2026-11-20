import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { action, backfillStatus, backfillStatusNulls, emailItem, emailItemNulls } from './fixtures'
import { server } from './server'
import { json, renderApp, signedIn } from './render'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 19, 12, 0, 0))
})
afterEach(() => vi.useRealTimers())

const withDoc = { ...emailItem, id: 'a', subject: 'Conta da Light', deadline: '2026-11-21', has_document: true }
const withoutDoc = { ...emailItem, id: 'b', subject: 'Entrevista técnica', deadline: '2026-11-25', has_document: false, due_soon: false }
const pending = Array.from({ length: 12 }, (_, i) => ({ ...action, id: i + 1 }))

function load(items: unknown[], backfill: unknown = backfillStatus) {
  signedIn(pending)
  server.use(
    http.get('/api/emails/relevant', () => json(items as never)),
    http.get('/api/backfill', () => json(backfill as never)),
  )
}

test('renders_items_in_order_with_title_and_links', async () => {
  load([withDoc, withoutDoc, { ...withoutDoc, id: 'c' }, { ...withoutDoc, id: 'd' }])
  renderApp('/')
  expect(await screen.findByRole('heading', { name: '4 coisas pedem atenção' })).toBeInTheDocument()
  const rows = within(screen.getByRole('region', { name: 'E-mails relevantes' })).getAllByRole('link')
  expect(rows[0]).toHaveAttribute('href', '/caixa/a')
  expect(rows[0]).toHaveTextContent('Conta da Light')
  expect(rows[0]).toHaveTextContent('Vence em 2 dias')
  expect(rows[1]).toHaveTextContent('Vence 25/11')
  expect(await screen.findByText(/12 sugestões do agente esperando você/)).toBeInTheDocument()
})

test('document_filter_hides_others_and_empty_filter_has_message', async () => {
  load([withoutDoc, { ...withoutDoc, id: 'c' }])
  const user = userEvent.setup()
  renderApp('/')
  await screen.findByRole('heading', { name: '2 coisas pedem atenção' })
  await user.click(screen.getByRole('button', { name: 'Com documento' }))
  expect(screen.getByText('Nada aqui com esse filtro.')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /^Todos/ }))
  expect(screen.queryByText('Nada aqui com esse filtro.')).not.toBeInTheDocument()
})

test('zero_relevant_shows_calm_empty_state', async () => {
  load([])
  renderApp('/')
  expect(await screen.findByRole('heading', { name: 'Nada pede atenção agora' })).toBeInTheDocument()
  expect(screen.getByText(/Nenhum e-mail relevante/)).toBeInTheDocument()
})

test('null_fields_render_placeholders_without_crashing', async () => {
  load([emailItemNulls])
  renderApp('/')
  const row = (await screen.findAllByRole('link', { name: /sem assunto/ }))[0]!
  expect(row).toHaveTextContent('(sem assunto)')
  expect(row).toHaveTextContent('—')
  expect(document.body).not.toHaveTextContent('Invalid Date')
})

test('backfill_without_percent_has_no_nan', async () => {
  load([withDoc], backfillStatusNulls)
  renderApp('/')
  await screen.findByRole('heading', { name: '1 coisa pede atenção' })
  expect(await screen.findByText(/Histórico/)).toBeInTheDocument()
  expect(document.body).not.toHaveTextContent('NaN')
})

test('summary_failure_does_not_hide_the_list', async () => {
  signedIn([])
  server.use(
    http.get('/api/emails/relevant', () => json([withDoc])),
    http.get('/api/backfill', () => json({ detail: 'erro' }, 500)),
  )
  renderApp('/')
  expect(await screen.findByText('Conta da Light')).toBeInTheDocument()
})
