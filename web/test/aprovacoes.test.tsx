import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test, vi } from 'vitest'
import type { Action } from '../src/api/schemas'
import { action, rule } from './fixtures'
import { server } from './server'
import { json, renderApp, signedIn } from './render'

vi.mock('../src/lib/deferred', async (orig) => ({
  ...(await orig<typeof import('../src/lib/deferred')>()),
  DECISION_DELAY_MS: 400,
}))

const a = (over: Partial<Action>): Action => ({ ...action, ...over })

const trash = [1, 2, 3].map((id) =>
  a({ id, rule_id: 5, kind: 'trash', subject: `Promoção ${id}`, sender: 'Loja <x@loja.com>' }),
)
const archive = [4, 5].map((id) =>
  a({ id, rule_id: null, kind: 'archive', subject: `Vaga ${id}`, sender: 'Catho <v@catho.com>' }),
)
const block = a({ id: 6, rule_id: null, kind: 'block_sender', subject: 'Prêmio!!!', sender: 'premios@exemplo.net' })
const all = [...trash, ...archive, block]

function load(pending: Action[] = all) {
  signedIn(pending)
  server.use(http.get('/api/agent/rules', () => json([rule])))
}

function captureDecisions(verb: 'approve' | 'reject', reply: (ids: number[]) => Response) {
  const sent: Array<{ ids: number[]; comment?: string }> = []
  server.use(
    http.post(`/api/actions/${verb}`, async ({ request }) => {
      const body = (await request.json()) as { ids: number[]; comment?: string }
      sent.push(body)
      return reply(body.ids)
    }),
  )
  return sent
}

const ok = (ids: number[]) => json({ decided: ids.length })

const bar = () => within(screen.getByRole('region', { name: 'Decisão em lote' }))

async function selectGroup(user: ReturnType<typeof userEvent.setup>, title: string) {
  const group = await screen.findByRole('region', { name: title })
  await user.click(within(group).getAllByRole('checkbox')[0]!)
}

test('groups_by_rule_then_by_sender', async () => {
  load()
  const user = userEvent.setup()
  renderApp('/aprovacoes')
  expect(await screen.findByRole('heading', { name: '6 sugestões esperando você' })).toBeInTheDocument()
  const lojas = screen.getByRole('region', { name: 'lojas → lixeira' })
  expect(lojas).toHaveTextContent('regra, 3 sugestões, 23 aprovadas, 1 recusadas até hoje')
  expect(screen.getByRole('region', { name: 'Agente: Arquivar' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Agente: Bloquear' })).toHaveTextContent('nunca roda sozinha')

  await user.click(screen.getByRole('button', { name: 'Por remetente' }))
  expect(await screen.findByRole('region', { name: 'Loja' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Catho' })).toBeInTheDocument()
})

test('approve_group_sends_ids_and_omits_empty_comment_after_the_undo_window', async () => {
  load()
  const sent = captureDecisions('approve', ok)
  const user = userEvent.setup()
  renderApp('/aprovacoes')
  await selectGroup(user, 'lojas → lixeira')
  expect(screen.getByText('3 selecionadas')).toBeInTheDocument()
  await user.click(bar().getByRole('button', { name: /^Aprovar 3/ }))
  expect(sent).toEqual([])
  await waitFor(() => expect(sent).toEqual([{ ids: [1, 2, 3] }]))
})

test('comment_is_sent_with_the_decision', async () => {
  load()
  const sent = captureDecisions('reject', ok)
  const user = userEvent.setup()
  renderApp('/aprovacoes')
  await selectGroup(user, 'Agente: Arquivar')
  await user.type(screen.getByLabelText('Comentário'), 'nunca arquivar vagas de backend')
  await user.click(bar().getByRole('button', { name: /^Recusar/ }))
  await waitFor(() => expect(sent).toEqual([{ ids: [4, 5], comment: 'nunca arquivar vagas de backend' }]))
})

test('comment_input_is_limited_to_500_characters', async () => {
  load()
  renderApp('/aprovacoes')
  expect(await screen.findByLabelText('Comentário')).toHaveAttribute('maxlength', '500')
})

test('undo_before_the_window_ends_sends_nothing', async () => {
  load()
  const sent = captureDecisions('approve', ok)
  const user = userEvent.setup()
  renderApp('/aprovacoes')
  await selectGroup(user, 'lojas → lixeira')
  await user.click(bar().getByRole('button', { name: /^Aprovar 3/ }))
  await user.click(await screen.findByRole('button', { name: /Desfazer/ }))
  await new Promise((r) => setTimeout(r, 700))
  expect(sent).toEqual([])
  expect(screen.getByText('3 selecionadas')).toBeInTheDocument()
})

test('leaving_the_page_sends_the_pending_decision_at_once', async () => {
  load()
  const sent = captureDecisions('approve', ok)
  const user = userEvent.setup()
  renderApp('/aprovacoes')
  await selectGroup(user, 'lojas → lixeira')
  await user.click(bar().getByRole('button', { name: /^Aprovar 3/ }))
  await user.click(within(screen.getAllByRole('navigation', { name: 'Principal' })[0]!).getByRole('link', { name: /Chat/ }))
  await waitFor(() => expect(sent).toEqual([{ ids: [1, 2, 3] }]), { timeout: 250 })
})

test('fewer_decided_than_sent_reports_the_real_number_and_reloads', async () => {
  load()
  let lists = 0
  signedIn(all)
  server.use(
    http.get('/api/actions', () => {
      lists += 1
      return json(all)
    }),
    http.get('/api/agent/rules', () => json([rule])),
  )
  captureDecisions('approve', () => json({ decided: 2 }))
  const user = userEvent.setup()
  renderApp('/aprovacoes')
  await selectGroup(user, 'lojas → lixeira')
  await user.click(bar().getByRole('button', { name: /^Aprovar 3/ }))
  expect(
    await screen.findByText('2 de 3 sugestões aprovadas. As outras já tinham sido decididas.', {}, { timeout: 2000 }),
  ).toBeInTheDocument()
  expect(lists).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('0 selecionadas')).toBeInTheDocument()
})

test('rate_limit_and_csrf_errors_show_messages_and_keep_the_selection', async () => {
  load()
  let n = 0
  captureDecisions('approve', () => (++n === 1 ? json({ detail: 'muitas requisições' }, 429) : json({ detail: 'requisição recusada' }, 403)))
  const user = userEvent.setup()
  renderApp('/aprovacoes')
  await selectGroup(user, 'lojas → lixeira')
  await user.click(bar().getByRole('button', { name: /^Aprovar 3/ }))
  expect(await screen.findByText('Muitas requisições, tente de novo em instantes', {}, { timeout: 2000 })).toBeInTheDocument()
  expect(screen.getByText('3 selecionadas')).toBeInTheDocument()

  await user.click(bar().getByRole('button', { name: /^Aprovar 3/ }))
  expect(await screen.findByText('Requisição recusada. Recarregue a página.', {}, { timeout: 2000 })).toBeInTheDocument()
  expect(screen.getByText('3 selecionadas')).toBeInTheDocument()
})

test('empty_queue_has_message_and_link_to_the_mailbox', async () => {
  load([])
  renderApp('/aprovacoes')
  expect(await screen.findByRole('heading', { name: 'Nenhuma sugestão esperando você' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Ir para a Caixa' })).toHaveAttribute('href', '/caixa')
})

test('approve_is_disabled_until_something_is_selected', async () => {
  load()
  renderApp('/aprovacoes')
  expect(await within(await screen.findByRole('region', { name: 'Decisão em lote' })).findByRole('button', { name: /^Aprovar 0/ })).toBeDisabled()
})

test('group_with_important_attachment_shows_a_warning', async () => {
  load([a({ id: 1, rule_id: 5, kind: 'trash', has_important_attachment: true }), a({ id: 2, rule_id: 5, kind: 'trash' })])
  renderApp('/aprovacoes')
  expect(
    await screen.findByText('1 destes e-mails tem um documento importante indexado. Confira antes de aprovar.'),
  ).toBeInTheDocument()
})
