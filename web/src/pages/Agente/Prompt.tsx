import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import * as api from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import type { AgentConfig, ConfigsOut } from '../../api/schemas'
import { Empty } from '../../ui/Empty'
import { Icon } from '../../ui/Icon'
import { useToast } from '../../ui/Toast'

const MIN_TURNS = 1
const MAX_TURNS = 8

export function Prompt() {
  const configs = useQuery({ queryKey: ['agent-config'], queryFn: api.agentConfig })
  if (configs.isError) return <Empty text="Não foi possível carregar o prompt." />
  if (!configs.data) return <p className="ia-muted">Carregando…</p>
  // Keyed by the active version: switching version resets the editor to it.
  return <Editor key={configs.data.active?.id ?? 'none'} data={configs.data} />
}

function Editor({ data }: { data: ConfigsOut }) {
  const qc = useQueryClient()
  const toast = useToast()
  const active: AgentConfig | null = data.active
  const others = data.versions.filter((v) => !v.active)
  const [prompt, setPrompt] = useState(active?.decide_prompt ?? '')
  const [turns, setTurns] = useState(String(active?.max_turns ?? 4))
  const [older, setOlder] = useState(others[0] ? String(others[0].id) : '')

  const nextVersion = Math.max(0, ...data.versions.map((v) => v.version)) + 1
  const turnsNumber = Number(turns)
  const validTurns = Number.isInteger(turnsNumber) && turnsNumber >= MIN_TURNS && turnsNumber <= MAX_TURNS
  const changed = prompt !== (active?.decide_prompt ?? '') || turnsNumber !== (active?.max_turns ?? 4)
  const chosen = others.find((v) => String(v.id) === older)

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ['agent-config'] })
    await qc.invalidateQueries({ queryKey: ['audit'] })
  }
  const save = useMutation({
    mutationFn: () => api.newConfig(prompt, turnsNumber),
    onSuccess: async (row) => {
      toast.show(`Prompt salvo como v${row.version}.`)
      await refresh()
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível salvar o prompt.'),
  })
  const rollback = useMutation({
    mutationFn: (id: number) => api.activateConfig(id),
    onSuccess: async () => {
      toast.show('Versão anterior ativada.')
      await refresh()
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível voltar a versão.'),
  })

  return (
    <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Prompt">
      <div className="spread" style={{ flexWrap: 'wrap' }}>
        <h2 className="h-heading">{active ? `Prompt de decisão, v${active.version} ativa` : 'Prompt de decisão'}</h2>
        <span className="toolbar">
          {others.length > 0 ? (
            <>
              <label className="ia-input ia-input--dark" style={{ height: 32 }}>
                <select aria-label="Versão anterior" value={older} onChange={(e) => setOlder(e.target.value)}>
                  {others.map((v) => (
                    <option key={v.id} value={String(v.id)}>{`v${v.version}`}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="ia-btn ia-btn--sm"
                disabled={!chosen || rollback.isPending}
                onClick={() => chosen && rollback.mutate(chosen.id)}
              >
                <Icon name="history" />
                {chosen ? `Voltar para v${chosen.version}` : 'Voltar'}
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="ia-btn ia-btn--primary ia-btn--sm"
            disabled={!changed || !validTurns || !prompt.trim() || save.isPending}
            onClick={() => save.mutate()}
          >
            {`Salvar como v${nextVersion}`}
          </button>
        </span>
      </div>
      <label className="ia-field">
        <span className="ia-label">Instruções</span>
        <textarea className="prompt-box" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </label>
      <label className="ia-field" style={{ maxWidth: 200 }}>
        <span className="ia-label">Máx. de turnos</span>
        <span className="ia-input">
          <input
            type="number"
            min={MIN_TURNS}
            max={MAX_TURNS}
            value={turns}
            onChange={(e) => setTurns(e.target.value)}
          />
        </span>
      </label>
    </section>
  )
}
