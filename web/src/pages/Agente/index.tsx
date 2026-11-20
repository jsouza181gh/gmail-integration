import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import * as api from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { formatDateTime } from '../../lib/format'
import { Badge } from '../../ui/Badge'
import { Page, PageHead } from '../../ui/Page'
import { Switch } from '../../ui/Switch'
import { useToast } from '../../ui/Toast'
import { Categorias } from './Categorias'
import { Conhecimento } from './Conhecimento'
import { Politicas } from './Politicas'
import { Prompt } from './Prompt'
import { Regras } from './Regras'

const TABS = [
  ['regras', 'Regras'],
  ['politicas', 'Políticas'],
  ['conhecimento', 'Conhecimento'],
  ['prompt', 'Prompt'],
  ['categorias', 'Categorias'],
] as const

type Tab = (typeof TABS)[number][0]

const isTab = (v: string | null): v is Tab => TABS.some(([id]) => id === v)

const ENTITY: Record<string, string> = {
  rule: 'regra',
  agent_config: 'prompt',
  kb_entry: 'conhecimento',
  tool_policy: 'política',
  settings: 'configuração',
}
const CHANNEL: Record<string, string> = { ui: 'painel', chat: 'chat', cli: 'servidor' }

export default function Agente() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('aba')
  const tab: Tab = isTab(raw) ? raw : 'regras'

  return (
    <Page>
      <PageHead
        title="Como o agente se comporta"
        subtitle="Toda mudança vira uma versão nova e fica no histórico."
        actions={<ModeSwitch />}
      />

      <div className="chips" role="tablist" aria-label="Seções">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            className="chip-btn"
            aria-selected={tab === id}
            aria-pressed={tab === id}
            onClick={() => setParams({ aba: id }, { replace: true })}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'regras' ? <Regras /> : null}
      {tab === 'politicas' ? <Politicas /> : null}
      {tab === 'conhecimento' ? <Conhecimento /> : null}
      {tab === 'prompt' ? <Prompt /> : null}
      {tab === 'categorias' ? <Categorias /> : null}

      <History />
    </Page>
  )
}

function ModeSwitch() {
  const qc = useQueryClient()
  const toast = useToast()
  const settings = useQuery({ queryKey: ['agent-settings'], queryFn: api.agentSettings })
  const mode = settings.data?.llm_mode ?? 'free'
  const change = useMutation({
    mutationFn: (next: 'free' | 'fast') => api.setAgentSettings(next),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['agent-settings'] })
      await qc.invalidateQueries({ queryKey: ['audit'] })
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível trocar o modo.'),
  })
  return (
    <Switch
      checked={mode === 'fast'}
      onChange={(on) => change.mutate(on ? 'fast' : 'free')}
      label={`Modo ${mode}`}
      description={mode === 'fast' ? 'o mais rápido possível' : 'respeita a cota gratuita do Gemini'}
    />
  )
}

function History() {
  const [limit] = useState(20)
  const audit = useQuery({ queryKey: ['audit', limit], queryFn: () => api.audit(limit) })
  return (
    <section className="ia-card" style={{ display: 'grid', gap: 10 }} aria-label="Histórico de mudanças">
      <h2 className="h-heading">Histórico de mudanças</h2>
      {audit.isSuccess && audit.data.length === 0 ? (
        <p className="t-small ia-muted" style={{ margin: 0 }}>Nenhuma mudança registrada.</p>
      ) : null}
      {audit.data?.map((a) => (
        <div key={a.id} className="row t-small">
          <span className="ia-num ia-subtle history-when">{formatDateTime(a.created_at)}</span>
          <span>{`${ENTITY[a.entity] ?? a.entity} ${a.entity_id}: ${a.before ?? '—'} → ${a.after ?? '—'}`}</span>
          <span style={{ marginLeft: 'auto' }}>
            <Badge>{CHANNEL[a.channel] ?? a.channel}</Badge>
          </span>
        </div>
      ))}
    </section>
  )
}
