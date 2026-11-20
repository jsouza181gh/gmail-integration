import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import * as api from '../api/endpoints'
import { ApiError } from '../api/errors'
import { useBackfill } from '../api/hooks'
import type { Run, UsageDay } from '../api/schemas'
import {
  formatDuration,
  formatEta,
  formatInt,
  formatPercent,
  formatTime,
  subjectOf,
} from '../lib/format'
import { Badge } from '../ui/Badge'
import { Empty } from '../ui/Empty'
import { Icon, type IconName } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { Progress } from '../ui/Progress'
import { useToast } from '../ui/Toast'

const plural = (n: number, one: string, many: string): string => `${formatInt(n)} ${n === 1 ? one : many}`
const failure = (err: unknown, fallback: string): string => (err instanceof ApiError ? err.detail : fallback)

interface Day {
  day: string
  calls: number
  errors: number
  tokens: number
}

function perDay(rows: UsageDay[]): Day[] {
  const byDay = new Map<string, Day>()
  for (const r of rows) {
    const d = byDay.get(r.day) ?? { day: r.day, calls: 0, errors: 0, tokens: 0 }
    d.calls += r.calls
    d.errors += r.errors
    d.tokens += r.input_tokens + r.output_tokens
    byDay.set(r.day, d)
  }
  return [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day))
}

const shortDay = (iso: string): string => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`
const kTokens = (n: number): string => (n < 1000 ? formatInt(n) : `${Math.round(n / 1000)} k`)

const RUN_ICON: Record<string, IconName> = { done: 'check', failed: 'alert' }

export default function Execucoes() {
  const qc = useQueryClient()
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const runId = Number(params.get('run')) || null
  const [today] = useState(() => new Date().toISOString().slice(0, 10))

  const backfill = useBackfill({ poll: true })
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.jobs() })
  const usage = useQuery({ queryKey: ['usage', 7], queryFn: () => api.usage(7) })
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => api.runs({ limit: 30 }) })
  const detail = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.runDetail(runId ?? 0),
    enabled: runId !== null,
  })

  const start = useMutation({
    mutationFn: api.backfillStart,
    onSuccess: async () => {
      toast.show('Histórico retomado.')
      await qc.invalidateQueries({ queryKey: ['backfill'] })
    },
    onError: (err) => toast.show(failure(err, 'Não foi possível retomar o histórico.')),
  })
  const toggle = useMutation({
    mutationFn: (pause: boolean) => (pause ? api.backfillPause() : api.backfillResume()),
    onSuccess: (status, pause) => {
      qc.setQueryData(['backfill'], status)
      toast.show(pause ? 'Histórico pausado.' : 'Histórico retomado.')
    },
    onError: (err) => toast.show(failure(err, 'Não foi possível mudar o histórico.')),
  })
  const requeue = useMutation({
    mutationFn: api.requeueJob,
    onSuccess: async () => {
      toast.show('Job reenfileirado.')
      await qc.invalidateQueries({ queryKey: ['jobs'] })
      await qc.invalidateQueries({ queryKey: ['backfill'] })
    },
    onError: (err) => toast.show(failure(err, 'Não foi possível reenfileirar.')),
  })

  const days = perDay(usage.data ?? [])
  const maxCalls = Math.max(1, ...days.map((d) => d.calls))
  const last = days.at(-1)

  return (
    <Page>
      <PageHead title="O que o sistema está fazendo" />

      {backfill.data ? <BackfillCard
          status={backfill.data}
          onResume={() => start.mutate()}
          resuming={start.isPending}
          onToggle={(pause) => toggle.mutate(pause)}
          toggling={toggle.isPending}
        /> : null}
      {backfill.isError ? <Empty text="Não foi possível carregar o progresso do histórico." /> : null}

      <div className="split">
        <section className="split__list ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Fila">
          <h2 className="h-heading">Fila</h2>
          <div className="tablebox">
            <table className="table">
              <thead>
                <tr>
                  <th>Tipo</th>
                  <th>Na fila</th>
                  <th>Rodando</th>
                  <th>Feitos</th>
                  <th>Mortos</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(jobs.data?.counts ?? {})
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([kind, c]) => (
                    <tr key={kind}>
                      <td>{kind}</td>
                      <td className="ia-num">{c.queued}</td>
                      <td className="ia-num">{c.running}</td>
                      <td className="ia-num">{c.done}</td>
                      <td className="ia-num">{c.dead}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {jobs.isSuccess && jobs.data.dead.length === 0 ? (
            <p className="t-small ia-muted" style={{ margin: 0 }}>Nenhum job morto.</p>
          ) : null}
          {jobs.data?.dead.map((j) => (
            <div key={j.id} className="spread dead-job">
              <span className="dead-job__text">
                <span className="t-small">{`#${j.id} ${j.kind}, ${plural(j.attempts, 'tentativa', 'tentativas')}`}</span>
                {j.last_error ? <span className="t-small ia-muted">{j.last_error}</span> : null}
              </span>
              <button
                type="button"
                className="ia-btn ia-btn--sm"
                disabled={requeue.isPending}
                onClick={() => requeue.mutate(j.id)}
              >
                <Icon name="refresh" />
                Reenfileirar
              </button>
            </div>
          ))}

          <h2 className="h-heading" style={{ marginTop: 8 }}>Uso do LLM, 7 dias</h2>
          {usage.isSuccess && days.length === 0 ? (
            <p className="t-small ia-muted" style={{ margin: 0 }}>Sem chamadas nos últimos 7 dias.</p>
          ) : null}
          {days.length > 0 ? (
            <>
              <div
                className="usage-chart"
                role="img"
                aria-label={`Chamadas por dia: ${days.map((d) => d.calls).join(', ')}`}
              >
                {days.map((d, i) => (
                  <span
                    key={d.day}
                    className={i === days.length - 1 ? 'usage-chart__bar usage-chart__bar--now' : 'usage-chart__bar'}
                    style={{ height: `${Math.max(4, Math.round((100 * d.calls) / maxCalls))}%` }}
                  />
                ))}
              </div>
              {last ? (
                <div className="spread t-small ia-subtle">
                  <span>{shortDay(days[0]?.day ?? last.day)}</span>
                  <span>
                    {`${last.day === today ? 'hoje' : shortDay(last.day)}, ${plural(last.calls, 'chamada', 'chamadas')}, ${plural(last.errors, 'erro', 'erros')}, ${kTokens(last.tokens)} tokens`}
                  </span>
                </div>
              ) : null}
            </>
          ) : null}
        </section>

        <section className="split__detail ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Execuções do agente">
          <div className="spread">
            <h2 className="h-heading">Execuções do agente</h2>
            <span className="t-small ia-subtle">por que ele sugeriu isso?</span>
          </div>
          {runs.isSuccess && runs.data.length === 0 ? <Empty text="Nenhuma execução ainda." /> : null}
          {runs.isError ? <Empty text="Não foi possível carregar as execuções." /> : null}
          {runs.data?.map((r) => (
            <RunRow key={r.id} run={r} selected={r.id === runId} onOpen={() => setParams({ run: String(r.id) })} />
          ))}
          {runId !== null ? (
            <section className="ia-card" style={{ background: 'var(--surface)', display: 'grid', gap: 10 }} aria-label={`Passos do run #${runId}`}>
              <span className="ia-label">{`Run #${runId}, passos`}</span>
              {detail.isPending ? <span className="t-small ia-muted">Carregando…</span> : null}
              {detail.isError ? <span className="t-small ia-muted">Execução não encontrada.</span> : null}
              {detail.data?.steps.map((s) => {
                const ms =
                  s.finished_at === null ? null : Date.parse(s.finished_at) - Date.parse(s.started_at)
                return (
                  <div key={s.id} className="row t-small">
                    <span className="ia-num ia-subtle">{s.position}</span>
                    <span style={{ color: s.status === 'ok' ? 'var(--accent)' : 'var(--warning)' }}>
                      <Icon name={s.status === 'ok' ? 'check' : 'alert'} />
                    </span>
                    <span>{s.detail ? `${s.name} → ${s.detail}` : s.name}</span>
                    <span className="ia-num ia-subtle" style={{ marginLeft: 'auto' }}>
                      {ms === null || Number.isNaN(ms) ? '—' : formatDuration(ms)}
                    </span>
                  </div>
                )
              })}
              {detail.data ? (
                <span className="t-small ia-muted">
                  {`${plural(detail.data.llm_calls.length, 'chamada', 'chamadas')}, ${formatInt(
                    detail.data.llm_calls.reduce((n, c) => n + c.total_tokens, 0),
                  )} tokens`}
                </span>
              ) : null}
            </section>
          ) : null}
        </section>
      </div>
    </Page>
  )
}

function RunRow({ run, selected, onOpen }: { run: Run; selected: boolean; onOpen: () => void }) {
  const icon = RUN_ICON[run.status] ?? 'activity'
  const title = run.summary ?? `Run #${run.id}`
  const trigger = run.trigger === 'webhook' ? 'e-mail novo' : run.trigger
  return (
    <button type="button" className="ia-row run-row" aria-selected={selected} onClick={onOpen}>
      <span style={{ color: run.status === 'failed' ? 'var(--warning)' : 'var(--accent)' }}>
        <Icon name={icon} />
      </span>
      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <span className="ia-row__subject" style={{ fontSize: 14 }}>{subjectOf({ subject: title })}</span>
        <span className="ia-row__from">{run.error ?? `${trigger}, ${run.status}`}</span>
      </span>
      <span className="ia-num ia-subtle">#{run.id}</span>
    </button>
  )
}

function BackfillCard({
  status: s,
  onResume,
  resuming,
  onToggle,
  toggling,
}: {
  status: NonNullable<ReturnType<typeof useBackfill>['data']>
  onResume: () => void
  resuming: boolean
  onToggle: (pause: boolean) => void
  toggling: boolean
}) {
  const p = s.progress
  const paused = s.paused || s.deferred_until !== null
  const chain = p.jobs['backfill_page']
  const chainAlive = (chain?.queued ?? 0) + (chain?.running ?? 0) > 0
  const attachments = p.attachments

  return (
    <section className="ia-card" style={{ display: 'grid', gap: 20, padding: 24 }} aria-label="Progresso do backfill">
      <div className="spread" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span className="ia-label">Indexação do histórico</span>
          <span className="row" style={{ gap: 12, alignItems: 'baseline' }}>
            <span className="stat" style={{ fontSize: 40, lineHeight: '44px' }}>{formatPercent(s.percent)}</span>
            <span className="t-body ia-muted ia-num">
              {s.mailbox_total === null
                ? `${formatInt(p.emails)} e-mails`
                : `${formatInt(p.emails)} de ${formatInt(s.mailbox_total)} e-mails`}
            </span>
          </span>
        </div>
        <span className="toolbar">
        {p.fetch_done ? (
          <Badge tone="accent" icon="check">Concluído</Badge>
        ) : s.paused ? (
          <Badge tone="warning" icon="pause">Pausado por você</Badge>
        ) : paused ? (
          <Badge tone="warning" icon="pause">
            {`Pausado: cota diária do Gemini, volta às ${formatTime(s.deferred_until)}`}
          </Badge>
        ) : (
          <Badge tone="accent" icon="activity">{`Rodando, página ${p.pages}`}</Badge>
        )}
        {p.fetch_done ? null : (
          <button
            type="button"
            className="ia-btn ia-btn--sm"
            disabled={toggling}
            onClick={() => onToggle(!s.paused)}
          >
            <Icon name={s.paused ? 'refresh' : 'pause'} />
            {s.paused ? 'Retomar' : 'Pausar'}
          </button>
        )}
        </span>
      </div>
      <Progress value={s.percent} paused={paused} height={10} />
      <div className="backfill-grid">
        <Metric label="Classificados" value={formatInt(p.classified)} />
        <Metric label="Com vetor" value={formatInt(p.embedded)} />
        <Metric
          label="Anexos lidos"
          value={formatInt(attachments['done'] ?? 0)}
          hint={attachments['skipped'] ? `${formatInt(attachments['skipped'])} ignorados` : undefined}
        />
        <Metric label="Ritmo" value={`${formatInt(s.pages_last_hour)} pág/h`} />
        <Metric label="Previsão" value={formatEta(s.eta_hours)} />
      </div>
      {!p.fetch_done && !chainAlive && !s.paused ? (
        <div>
          <button type="button" className="ia-btn" disabled={resuming} onClick={onResume}>
            <Icon name="refresh" />
            Retomar histórico
          </button>
        </div>
      ) : null}
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        E-mails novos sempre passam na frente. Um deploy no meio não perde nada: a página em curso recomeça de onde parou.
      </p>
    </section>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string | undefined }) {
  return (
    <div className="stack" style={{ gap: 4 }}>
      <span className="ia-label">{label}</span>
      <span className="ia-num" style={{ fontSize: 18 }}>
        {value}
        {hint ? <span className="ia-subtle" style={{ fontSize: 13 }}>{` (${hint})`}</span> : null}
      </span>
    </div>
  )
}
