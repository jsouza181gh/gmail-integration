import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test } from 'vitest'
import type { AgentConfig, ConfigsOut, KbEntry, Policy, Rule } from '../src/api/schemas'
import { audit, configs, kbEntry, policy, rule, taxonomy } from './fixtures'
import { server } from './server'
import { json, noContent, renderApp, signedIn } from './render'

interface State {
  rules: Rule[]
  policies: Policy[]
  kb: KbEntry[]
  configs: ConfigsOut
  mode: 'free' | 'fast' | null
  audit: unknown[]
}

function load(over: Partial<State> = {}) {
  const state: State = {
    rules: [rule],
    policies: [policy],
    kb: [kbEntry],
    configs,
    mode: null,
    audit: [audit],
    ...over,
  }
  signedIn([])
  server.use(
    http.get('/api/agent/rules', () => json(state.rules)),
    http.get('/api/agent/policies', () => json(state.policies)),
    http.get('/api/agent/kb', () => json(state.kb)),
    http.get('/api/agent/config', () => json(state.configs)),
    http.get('/api/agent/settings', () => json({ llm_mode: state.mode })),
    http.get('/api/agent/audit', () => json(state.audit as never)),
    http.get('/api/categories', () => json(taxonomy)),
  )
  return state
}

const blockRule: Rule = { ...rule, id: 6, name: 'bloquear prêmios', action: 'block_sender', mode: 'suggest', sender_domain: 'exemplo.net' }

test('rules_tab_lists_conditions_and_promotes_a_suggest_rule', async () => {
  load()
  let promoted = 0
  server.use(
    http.post('/api/agent/rules/5/promote', () => {
      promoted += 1
      return json({ ...rule, mode: 'auto' })
    }),
  )
  const user = userEvent.setup()
  renderApp('/agente')
  const row = (await screen.findByRole('cell', { name: 'lojas → lixeira' })).closest('tr')!
  expect(row).toHaveTextContent('domínio lojavertice.com.br')
  expect(row).toHaveTextContent('23 / 1')
  await user.click(within(row).getByRole('button', { name: /Tornar automática/ }))
  await waitFor(() => expect(promoted).toBe(1))
})

test('block_sender_rule_never_offers_auto', async () => {
  load({ rules: [blockRule] })
  renderApp('/agente')
  const row = (await screen.findByRole('cell', { name: 'bloquear prêmios' })).closest('tr')!
  expect(within(row).queryByRole('button', { name: /Tornar automática/ })).not.toBeInTheDocument()
  expect(screen.getByText(/Bloquear remetente, enviar e filtros nunca ficam automáticos/)).toBeInTheDocument()
})

test('auto_rule_can_be_disabled', async () => {
  load({ rules: [{ ...rule, mode: 'auto' }] })
  let disabled = 0
  server.use(
    http.post('/api/agent/rules/5/disable', () => {
      disabled += 1
      return json({ ...rule, active: false })
    }),
  )
  const user = userEvent.setup()
  renderApp('/agente')
  await user.click(await screen.findByRole('button', { name: 'Desativar' }))
  await waitFor(() => expect(disabled).toBe(1))
})

test('new_rule_form_needs_name_and_action_and_sends_only_filled_fields', async () => {
  load()
  let body: unknown
  server.use(
    http.post('/api/agent/rules', async ({ request }) => {
      body = await request.json()
      return json({ ...rule, id: 7, name: 'vagas java' })
    }),
  )
  const user = userEvent.setup()
  renderApp('/agente')
  await user.click(await screen.findByRole('button', { name: 'Nova regra' }))
  const save = screen.getByRole('button', { name: 'Criar regra' })
  expect(save).toBeDisabled()
  await user.type(screen.getByLabelText('Nome'), 'vagas java')
  expect(save).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('Ação'), 'archive')
  await user.type(screen.getByLabelText('Assunto contém'), 'Java Sênior')
  expect(save).toBeEnabled()
  await user.click(save)
  await waitFor(() => expect(body).toEqual({ name: 'vagas java', action: 'archive', subject_contains: 'Java Sênior' }))
})

test('server_rejection_of_a_rule_shows_its_detail', async () => {
  load()
  server.use(http.post('/api/agent/rules', () => json({ detail: 'a regra precisa de ao menos uma condição' }, 422)))
  const user = userEvent.setup()
  renderApp('/agente')
  await user.click(await screen.findByRole('button', { name: 'Nova regra' }))
  await user.type(screen.getByLabelText('Nome'), 'x')
  await user.selectOptions(screen.getByLabelText('Ação'), 'trash')
  await user.click(screen.getByRole('button', { name: 'Criar regra' }))
  expect(await screen.findByText('a regra precisa de ao menos uma condição')).toBeInTheDocument()
})

test('policies_tab_changes_a_policy_and_locks_block_sender', async () => {
  load({ policies: [policy, { target: 'block_sender', trigger: 'webhook', policy: 'suggest' }] })
  let body: unknown
  server.use(
    http.put('/api/agent/policies', async ({ request }) => {
      body = await request.json()
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/agente?aba=politicas')
  const select = await screen.findByLabelText('Arquivar, E-mail novo')
  expect(select).toHaveValue('suggest')
  await user.selectOptions(select, 'auto')
  await waitFor(() => expect(body).toEqual({ target: 'archive', trigger: 'webhook', policy: 'auto' }))
  expect(screen.queryByLabelText('Bloquear, E-mail novo')).not.toBeInTheDocument()
  expect(screen.getAllByText('Sempre sugerir').length).toBe(2)
})

test('mode_switch_puts_llm_mode', async () => {
  load()
  let body: unknown
  server.use(
    http.put('/api/agent/settings', async ({ request }) => {
      body = await request.json()
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/agente')
  const sw = await screen.findByRole('switch', { name: /Modo free/ })
  expect(sw).toHaveAttribute('aria-checked', 'false')
  await user.click(sw)
  await waitFor(() => expect(body).toEqual({ llm_mode: 'fast' }))
})

test('knowledge_tab_adds_and_removes_entries', async () => {
  load()
  const added: unknown[] = []
  let removed = 0
  server.use(
    http.post('/api/agent/kb', async ({ request }) => {
      added.push(await request.json())
      return json({ ...kbEntry, id: 2, text: 'Nunca arquivar e-mails da 42' })
    }),
    http.delete('/api/agent/kb/1', () => {
      removed += 1
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/agente?aba=conhecimento')
  expect(await screen.findByText(kbEntry.text)).toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Escopo'), 'decision')
  await user.type(screen.getByLabelText('O que o agente deve saber'), 'Nunca arquivar e-mails da 42')
  await user.click(screen.getByRole('button', { name: 'Adicionar' }))
  await waitFor(() => expect(added).toEqual([{ scope: 'decision', text: 'Nunca arquivar e-mails da 42' }]))
  await user.click(screen.getByRole('button', { name: `Remover: ${kbEntry.text}` }))
  await waitFor(() => expect(removed).toBe(1))
})

test('prompt_tab_saves_a_new_version_and_rolls_back', async () => {
  const state = load()
  const created: unknown[] = []
  let activated = 0
  const v4: AgentConfig = { id: 4, version: 4, active: true, decide_prompt: 'Prompt novo', chat_prompt: 'Chat', max_turns: 5, created_at: '2026-11-19T22:00:00Z' }
  server.use(
    http.post('/api/agent/config', async ({ request }) => {
      created.push(await request.json())
      state.configs = {
        active: v4,
        versions: [v4, ...configs.versions.map((c) => ({ ...c, active: false }))],
      }
      return json(v4)
    }),
    http.post('/api/agent/config/2/activate', () => {
      activated += 1
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/agente?aba=prompt')
  expect(await screen.findByRole('heading', { name: 'Prompt de decisão, v3 ativa' })).toBeInTheDocument()
  const save = screen.getByRole('button', { name: 'Salvar como v4' })
  expect(save).toBeDisabled()

  const box = screen.getByLabelText('Instruções')
  await user.clear(box)
  await user.type(box, 'Prompt novo')
  const turns = screen.getByLabelText('Máx. de turnos')
  await user.clear(turns)
  await user.type(turns, '5')
  await user.click(save)
  await waitFor(() => expect(created).toEqual([{ decide_prompt: 'Prompt novo', max_turns: 5 }]))
  expect(await screen.findByRole('heading', { name: 'Prompt de decisão, v4 ativa' })).toBeInTheDocument()

  await user.selectOptions(screen.getByLabelText('Versão anterior'), '2')
  await user.click(screen.getByRole('button', { name: 'Voltar para v2' }))
  await waitFor(() => expect(activated).toBe(1))
})

test('categories_tab_is_read_only_taxonomy', async () => {
  load()
  renderApp('/agente?aba=categorias')
  expect(await screen.findByText('banco, fatura, cobrança, imposto')).toBeInTheDocument()
  expect(screen.getByText('Somente leitura por enquanto.')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Adicionar|Nova/ })).not.toBeInTheDocument()
})

test('history_shows_recent_changes', async () => {
  load()
  renderApp('/agente')
  const history = await screen.findByRole('region', { name: 'Histórico de mudanças' })
  expect(await within(history).findByText(/regra 5/)).toBeInTheDocument()
})

test('empty_lists_have_messages', async () => {
  load({ rules: [], kb: [], audit: [] })
  const user = userEvent.setup()
  renderApp('/agente')
  expect(await screen.findByText('Nenhuma regra ainda. As aprovações repetidas viram regras.')).toBeInTheDocument()
  expect(screen.getByText('Nenhuma mudança registrada.')).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Conhecimento' }))
  expect(await screen.findByText('O agente ainda não aprendeu nada com você.')).toBeInTheDocument()
})
