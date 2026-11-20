import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { beforeEach, expect, test, vi } from 'vitest'
import { navigateTo } from '../src/lib/nav'
import { action, document as doc, emailDetail, emailItem, taxonomy } from './fixtures'
import { server } from './server'
import { json, noContent, renderApp, signedIn } from './render'

vi.mock('../src/lib/nav', () => ({ navigateTo: vi.fn() }))
vi.mock('../src/lib/deferred', async (orig) => ({
  ...(await orig<typeof import('../src/lib/deferred')>()),
  DECISION_DELAY_MS: 200,
}))

const second = {
  ...emailItem,
  id: 'e9',
  subject: 'Sua fatura fechou',
  sender: 'Nubank <no-reply@nubank.com.br>',
  deadline: null,
  due_soon: false,
}

beforeEach(() => vi.mocked(navigateTo).mockClear())

interface Loaders {
  detail?: unknown
  emails?: (url: URL) => unknown
}

function load(opts: Loaders = {}) {
  signedIn([])
  server.use(
    http.get('/api/categories', () => json(taxonomy)),
    http.get('/api/emails', ({ request }) =>
      json((opts.emails?.(new URL(request.url)) ?? { items: [emailItem, second], next_before: null }) as never),
    ),
    http.get('/api/emails/:id', ({ params }) => {
      const base = (opts.detail ?? emailDetail) as typeof emailDetail
      return json(params.id === 'e9' ? { ...base, item: second } : base)
    }),
  )
}

test('opens_first_email_by_default_and_switches_on_click', async () => {
  load()
  const user = userEvent.setup()
  renderApp('/caixa')
  const detail = await screen.findByRole('article', { name: 'Detalhe' })
  expect(await within(detail).findByRole('heading', { name: 'Sua conta de novembro chegou' })).toBeInTheDocument()
  await user.click(await screen.findByRole('link', { name: /Sua fatura fechou/ }))
  expect(await within(detail).findByRole('heading', { name: 'Sua fatura fechou' })).toBeInTheDocument()
})

test('category_chip_and_relevant_chip_refetch_with_params', async () => {
  const urls: URL[] = []
  load({ emails: (u) => (urls.push(u), { items: [emailItem], next_before: null }) })
  const user = userEvent.setup()
  renderApp('/caixa')
  await user.click(await screen.findByRole('button', { name: 'Finanças' }))
  await waitFor(() => expect(urls.some((u) => u.searchParams.get('category') === 'Finanças')).toBe(true))
  await user.click(screen.getByRole('button', { name: 'Só relevantes' }))
  await waitFor(() => expect(urls.some((u) => u.searchParams.get('relevant') === 'true')).toBe(true))
})

test('load_more_sends_cursor_and_appends', async () => {
  const urls: URL[] = []
  load({
    emails: (u) => {
      urls.push(u)
      return u.searchParams.get('before')
        ? { items: [{ ...second, id: 'e10', subject: 'Mais antigo' }], next_before: null }
        : { items: [emailItem], next_before: 'cursor-1' }
    },
  })
  const user = userEvent.setup()
  renderApp('/caixa')
  await user.click(await screen.findByRole('button', { name: 'Carregar mais' }))
  expect(await screen.findByRole('link', { name: /Mais antigo/ })).toBeInTheDocument()
  expect(urls.at(-1)?.searchParams.get('before')).toBe('cursor-1')
  expect(screen.queryByRole('button', { name: 'Carregar mais' })).not.toBeInTheDocument()
})

test('search_is_debounced_and_sent_as_q', async () => {
  const urls: URL[] = []
  load({ emails: (u) => (urls.push(u), { items: [emailItem], next_before: null }) })
  const user = userEvent.setup()
  renderApp('/caixa')
  await user.type(await screen.findByRole('searchbox', { name: 'Buscar na caixa' }), 'luz')
  await waitFor(() => expect(urls.some((u) => u.searchParams.get('q') === 'luz')).toBe(true))
  expect(urls.filter((u) => u.searchParams.get('q')).length).toBe(1)
})

test('reclassify_needs_a_category_then_posts_it', async () => {
  load()
  let body: unknown
  server.use(
    http.post('/api/emails/e1/reclassify', async ({ request }) => {
      body = await request.json()
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/caixa/e1')
  const button = await screen.findByRole('button', { name: 'Reclassificar' })
  expect(button).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('Nova categoria'), 'Compras')
  await user.click(button)
  await waitFor(() => expect(body).toEqual({ category: 'Compras' }))
  expect(await screen.findByText(/Reclassificado como Compras/)).toBeInTheDocument()
})

test('html_in_subject_and_body_is_rendered_as_text', async () => {
  const evil = '<img src=x onerror="window.__pwned=1">'
  load({
    detail: {
      ...emailDetail,
      item: { ...emailItem, subject: evil },
      body: `${evil}<script>window.__pwned=2</script>`,
    },
  })
  renderApp('/caixa/e1')
  const detail = await screen.findByRole('article', { name: 'Detalhe' })
  await within(detail).findByRole('heading', { name: evil })
  expect(detail).toHaveTextContent('<script>window.__pwned=2</script>')
  expect(document.querySelector('img[src="x"]')).toBeNull()
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
})

test('download_gets_a_link_then_navigates_and_failure_shows_error', async () => {
  load()
  let calls = 0
  server.use(
    http.post('/api/documents/:id/link', () => {
      calls += 1
      return calls === 1
        ? json({ url: '/api/documents/7/download?t=abc', expires_in: 60 })
        : json({ detail: 'documento não encontrado' }, 404)
    }),
  )
  const user = userEvent.setup()
  renderApp('/caixa/e1')
  await user.click(await screen.findByRole('button', { name: /Baixar/ }))
  await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('/api/documents/7/download?t=abc'))
  await user.click(screen.getByRole('button', { name: /Baixar/ }))
  expect(await screen.findByText(/Não foi possível baixar: documento não encontrado/)).toBeInTheDocument()
})

test('approve_waits_for_undo_window_z_cancels_otherwise_sends', async () => {
  load()
  const sent: unknown[] = []
  server.use(
    http.post('/api/actions/approve', async ({ request }) => {
      sent.push(await request.json())
      return json({ decided: 1 })
    }),
  )
  const user = userEvent.setup()
  renderApp('/caixa/e1')
  const approve = await screen.findByRole('button', { name: /Aprovar/ })

  await user.click(approve)
  await user.keyboard('z')
  await new Promise((r) => setTimeout(r, 450))
  expect(sent).toEqual([])

  await user.click(screen.getByRole('button', { name: /Aprovar/ }))
  expect(sent).toEqual([])
  await waitFor(() => expect(sent).toEqual([{ ids: [action.id] }]))
})

test('missing_body_has_placeholder', async () => {
  load({ detail: { ...emailDetail, body: null, attachments: [], actions: [] } })
  renderApp('/caixa/e1')
  expect(await screen.findByText('Sem texto indexado para este e-mail.')).toBeInTheDocument()
})

test('empty_mailbox_shows_empty_state', async () => {
  load({ emails: () => ({ items: [], next_before: null }) })
  renderApp('/caixa')
  expect(await screen.findByText('Nenhum e-mail encontrado.')).toBeInTheDocument()
})

test('attachment_card_shows_title_and_amount', async () => {
  load({ detail: { ...emailDetail, attachments: [{ ...doc, title: 'Boleto Light novembro/2026' }] } })
  renderApp('/caixa/e1')
  const detail = await screen.findByRole('article', { name: 'Detalhe' })
  expect(await within(detail).findByText('Boleto Light novembro/2026')).toBeInTheDocument()
  expect(within(detail).getByText(/R\$ 189,90/)).toBeInTheDocument()
})
