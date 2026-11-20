import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { beforeEach, expect, test, vi } from 'vitest'
import { navigateTo } from '../src/lib/nav'
import type { DocumentOut } from '../src/api/schemas'
import { document as doc, documentNulls } from './fixtures'
import { server } from './server'
import { json, renderApp, signedIn } from './render'

vi.mock('../src/lib/nav', () => ({ navigateTo: vi.fn() }))

const d = (over: Partial<DocumentOut>): DocumentOut => ({ ...doc, ...over })

const docs = [
  d({ id: 1, doc_class: 'fiscal', title: 'Conta de luz de novembro', issuer: 'Light S.A.' }),
  d({ id: 2, doc_class: 'fiscal', title: 'Fatura do cartão', issuer: 'Nu Pagamentos', barcode: null, due_on: '2999-01-01' }),
  d({ id: 3, doc_class: 'pessoal', title: 'CNH digital', issuer: 'DETRAN', amount_cents: null, due_on: null, barcode: null }),
  d({ id: 4, doc_class: 'trabalho', title: 'Holerite de outubro', issuer: 'Empregador', barcode: null }),
  d({ id: 5, doc_class: 'outro', title: 'Panfleto', issuer: 'Loja', barcode: null }),
]

const skipped = (reason: string, id: number) => d({ id, status: 'skipped', skip_reason: reason, doc_class: 'outro' })

beforeEach(() => vi.mocked(navigateTo).mockClear())

function load(done: DocumentOut[] = docs, skippedDocs: DocumentOut[] = []) {
  signedIn([])
  server.use(
    http.get('/api/documents', ({ request }) =>
      json(new URL(request.url).searchParams.get('status') === 'skipped' ? skippedDocs : done),
    ),
  )
}

const rows = () => screen.getAllByRole('listitem')

test('class_chips_show_counts_and_filter_the_list', async () => {
  load()
  const user = userEvent.setup()
  renderApp('/documentos')
  expect(await screen.findByRole('button', { name: 'Todos 5' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Fiscal 2' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Pessoal 1' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Trabalho 1' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Fiscal 2' }))
  expect(rows()).toHaveLength(2)
  await user.type(screen.getByRole('searchbox', { name: 'Buscar documentos' }), 'light')
  expect(rows()).toHaveLength(1)
  expect(rows()[0]).toHaveTextContent('Conta de luz de novembro')
})

test('slash_focuses_the_search_box', async () => {
  load()
  const user = userEvent.setup()
  renderApp('/documentos')
  await screen.findByRole('button', { name: 'Todos 5' })
  await user.keyboard('/')
  expect(screen.getByRole('searchbox', { name: 'Buscar documentos' })).toHaveFocus()
})

test('null_metadata_falls_back_to_filename_and_dashes', async () => {
  load([documentNulls])
  renderApp('/documentos')
  const row = (await screen.findAllByRole('listitem'))[0]!
  expect(row).toHaveTextContent('boleto.pdf')
  expect(row).toHaveTextContent('—')
  expect(document.body).not.toHaveTextContent('Invalid Date')
  expect(document.body).not.toHaveTextContent('NaN')
})

test('copy_code_only_with_barcode_and_copies_it', async () => {
  load()
  const user = userEvent.setup()
  renderApp('/documentos')
  await screen.findByRole('button', { name: 'Todos 5' })
  const buttons = screen.getAllByRole('button', { name: 'Copiar código' })
  expect(buttons).toHaveLength(1)
  await user.click(buttons[0]!)
  expect(await navigator.clipboard.readText()).toBe(doc.barcode)
  expect(await screen.findByText('Linha digitável copiada')).toBeInTheDocument()
})

test('download_gets_link_and_navigates', async () => {
  load()
  server.use(
    http.post('/api/documents/1/link', () => json({ url: '/api/documents/1/download?t=zzz', expires_in: 60 })),
  )
  const user = userEvent.setup()
  renderApp('/documentos')
  await user.click(await screen.findByRole('button', { name: 'Baixar Conta de luz de novembro' }))
  await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('/api/documents/1/download?t=zzz'))
})

test('soon_due_documents_get_a_calendar_marker', async () => {
  const soon = new Date()
  soon.setDate(soon.getDate() + 1)
  const iso = `${soon.getFullYear()}-${String(soon.getMonth() + 1).padStart(2, '0')}-${String(soon.getDate()).padStart(2, '0')}`
  load([d({ id: 9, title: 'Boleto amanhã', due_on: iso })])
  renderApp('/documentos')
  const row = (await screen.findAllByRole('listitem'))[0]!
  expect(within(row).getByLabelText('vence em breve')).toBeInTheDocument()
})

test('empty_list_has_message', async () => {
  load([])
  renderApp('/documentos')
  expect(await screen.findByText('Nenhum documento indexado ainda.')).toBeInTheDocument()
})

test('password_protected_footer_counts_only_encrypted', async () => {
  load(docs, [skipped('encrypted', 20), skipped('encrypted', 21), skipped('too_large', 22)])
  renderApp('/documentos')
  expect(
    await screen.findByText('2 documentos estão protegidos por senha e não puderam ser lidos.'),
  ).toBeInTheDocument()
})

test('no_footer_without_encrypted_documents', async () => {
  load(docs, [skipped('too_large', 22)])
  renderApp('/documentos')
  await screen.findByRole('button', { name: 'Todos 5' })
  expect(screen.queryByText(/protegidos por senha/)).not.toBeInTheDocument()
})
