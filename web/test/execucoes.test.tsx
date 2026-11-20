import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test } from 'vitest'
import type { BackfillStatus, Run, UsageDay } from '../src/api/schemas'
import { backfillStatus, backfillStatusNulls, jobsOut, runDetail, usageDay } from './fixtures'
import { server } from './server'
import { json, noContent, renderApp, signedIn } from './render'

const run = (over: Partial<Run>): Run => ({ ...runDetail.run, ...over })
const runs = [
  run({ id: 812, summary: 'Sua conta de novembro chegou', status: 'done' }),
  run({ id: 805, summary: 'Seu código de acesso', status: 'failed', error: '503 do modelo' }),
]
const days: UsageDay[] = [
  { ...usageDay, day: '2026-11-17', calls: 40, errors: 0 },
  { ...usageDay, day: '2026-11-18', calls: 310, errors: 1 },
  { ...usageDay, day: '2026-11-19', calls: 96, errors: 2, input_tokens: 150000, output_tokens: 34000 },
]

interface Data {
  backfill?: BackfillStatus
  jobs?: typeof jobsOut
  usage?: UsageDay[]
  runs?: Run[]
}

function load(d: Data = {}) {
  signedIn([])
  server.use(
    http.get('/api/backfill', () => json(d.backfill ?? backfillStatus)),
    http.get('/api/jobs', () => json(d.jobs ?? jobsOut)),
    http.get('/api/usage', () => json(d.usage ?? days)),
    http.get('/api/runs', () => json(d.runs ?? runs)),
    http.get('/api/runs/:id', () => json(runDetail)),
  )
}

const withProgress = (over: Partial<BackfillStatus['progress']>, top: Partial<BackfillStatus> = {}): BackfillStatus => ({
  ...backfillStatus,
  ...top,
  progress: { ...backfillStatus.progress, ...over },
})

test('running_state_shows_percent_counts_rate_and_eta', async () => {
  load()
  renderApp('/execucoes')
  const card = await screen.findByRole('region', { name: 'Progresso do backfill' })
  expect(within(card).getByText('1,6%')).toBeInTheDocument()
  expect(within(card).getByText('Rodando, página 29')).toBeInTheDocument()
  expect(within(card).getByText('≈ 19 h')).toBeInTheDocument()
  expect(within(card).getByText('96 pág/h')).toBeInTheDocument()
  expect(card).toHaveTextContent('725 de 45.038 e-mails')
  expect(within(card).queryByRole('button', { name: 'Retomar histórico' })).not.toBeInTheDocument()
})

test('quota_pause_shows_local_time_and_striped_bar', async () => {
  load({ backfill: { ...backfillStatus, deferred_until: '2999-01-01T21:30:00Z' } })
  renderApp('/execucoes')
  const card = await screen.findByRole('region', { name: 'Progresso do backfill' })
  expect(await within(card).findByText(/Pausado: cota diária do Gemini, volta às \d{2}:\d{2}/)).toBeInTheDocument()
  expect(within(card).getByRole('progressbar')).toHaveClass('ia-progress--paused')
})

test('unknown_total_percent_and_eta_render_dashes_without_nan', async () => {
  load({ backfill: backfillStatusNulls })
  renderApp('/execucoes')
  const card = await screen.findByRole('region', { name: 'Progresso do backfill' })
  expect(card).toHaveTextContent('725 e-mails')
  expect(card).not.toHaveTextContent('de 45')
  expect(card).not.toHaveTextContent('NaN')
  expect(within(card).getAllByText('—').length).toBeGreaterThanOrEqual(2)
})

test('finished_backfill_says_concluded_and_hides_resume', async () => {
  load({ backfill: withProgress({ fetch_done: true, jobs: {} }, { percent: 100 }) })
  renderApp('/execucoes')
  const card = await screen.findByRole('region', { name: 'Progresso do backfill' })
  expect(await within(card).findByText('Concluído')).toBeInTheDocument()
  expect(within(card).queryByRole('button', { name: 'Retomar histórico' })).not.toBeInTheDocument()
})

test('resume_button_appears_when_the_chain_is_empty_and_starts_it', async () => {
  load({ backfill: withProgress({ jobs: { backfill_page: { queued: 0, running: 0, done: 28, dead: 0 } } }) })
  let started = 0
  server.use(
    http.post('/api/backfill/start', () => {
      started += 1
      return json(backfillStatus)
    }),
  )
  const user = userEvent.setup()
  renderApp('/execucoes')
  await user.click(await screen.findByRole('button', { name: 'Retomar histórico' }))
  await waitFor(() => expect(started).toBe(1))
})

test('dead_job_can_be_requeued', async () => {
  load()
  let requeued = 0
  let lists = 0
  server.use(
    http.post('/api/jobs/9/requeue', () => {
      requeued += 1
      return noContent()
    }),
    http.get('/api/jobs', () => {
      lists += 1
      return json(jobsOut)
    }),
  )
  const user = userEvent.setup()
  renderApp('/execucoes')
  expect(await screen.findByText(/index_attachment, 5 tentativas/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Reenfileirar' }))
  await waitFor(() => expect(requeued).toBe(1))
  await waitFor(() => expect(lists).toBeGreaterThanOrEqual(2))
})

test('queue_table_lists_counts_per_job_kind', async () => {
  load()
  renderApp('/execucoes')
  const row = (await screen.findByRole('cell', { name: 'backfill_page' })).closest('tr')!
  expect(within(row).getAllByRole('cell').map((c) => c.textContent)).toEqual(['backfill_page', '1', '0', '28', '0'])
})

test('usage_chart_describes_daily_calls', async () => {
  load()
  renderApp('/execucoes')
  const chart = await screen.findByRole('img', { name: 'Chamadas por dia: 40, 310, 96' })
  expect(chart).toBeInTheDocument()
  expect(screen.getByText(/96 chamadas, 2 erros, 184 k tokens/)).toBeInTheDocument()
})

test('empty_usage_and_runs_have_messages', async () => {
  load({ usage: [], runs: [] })
  renderApp('/execucoes')
  expect(await screen.findByText('Sem chamadas nos últimos 7 dias.')).toBeInTheDocument()
  expect(await screen.findByText('Nenhuma execução ainda.')).toBeInTheDocument()
})

test('run_query_param_opens_step_timeline', async () => {
  load()
  renderApp('/execucoes?run=812')
  const panel = await screen.findByRole('region', { name: 'Passos do run #812' })
  expect(await within(panel).findByText(/classify_email/)).toBeInTheDocument()
  expect(panel).toHaveTextContent('2,0 s')
  expect(panel).toHaveTextContent('1 chamada, 840 tokens')
})

test('clicking_a_run_opens_its_steps_and_failed_runs_show_the_error', async () => {
  load()
  const user = userEvent.setup()
  renderApp('/execucoes')
  expect(await screen.findByText('503 do modelo')).toBeInTheDocument()
  await user.click(await screen.findByRole('button', { name: /Sua conta de novembro chegou/ }))
  expect(await screen.findByRole('region', { name: 'Passos do run #812' })).toBeInTheDocument()
})
