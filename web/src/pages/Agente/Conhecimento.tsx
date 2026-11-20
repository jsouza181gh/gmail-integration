import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import * as api from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { Badge } from '../../ui/Badge'
import { Empty } from '../../ui/Empty'
import { Icon } from '../../ui/Icon'
import { useToast } from '../../ui/Toast'

const SCOPE_LABEL: Record<string, string> = { classification: 'classificação', decision: 'decisão' }

export function Conhecimento() {
  const qc = useQueryClient()
  const toast = useToast()
  const [scope, setScope] = useState<api.KbScope>('classification')
  const [text, setText] = useState('')
  const entries = useQuery({ queryKey: ['kb'], queryFn: () => api.kb() })

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ['kb'] })
    await qc.invalidateQueries({ queryKey: ['audit'] })
  }
  const add = useMutation({
    mutationFn: () => api.addKb(scope, text.trim()),
    onSuccess: async () => {
      setText('')
      toast.show('Anotado. O agente vai considerar isso.')
      await refresh()
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível adicionar.'),
  })
  const remove = useMutation({
    mutationFn: api.deactivateKb,
    onSuccess: async () => {
      toast.show('Removido do conhecimento do agente.')
      await refresh()
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível remover.'),
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (text.trim()) add.mutate()
  }
  const active = (entries.data ?? []).filter((k) => k.active)

  return (
    <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Conhecimento">
      <h2 className="h-heading">O que o agente sabe sobre você</h2>

      <form className="row kb-form" onSubmit={submit}>
        <label className="ia-field">
          <span className="ia-label">Escopo</span>
          <span className="ia-input ia-input--dark">
            <select value={scope} onChange={(e) => setScope(e.target.value as api.KbScope)}>
              <option value="classification">Classificação</option>
              <option value="decision">Decisão</option>
            </select>
          </span>
        </label>
        <label className="ia-field" style={{ flex: '1 1 280px' }}>
          <span className="ia-label">O que o agente deve saber</span>
          <span className="ia-input ia-input--dark">
            <input value={text} onChange={(e) => setText(e.target.value)} maxLength={500} />
          </span>
        </label>
        <button type="submit" className="ia-btn ia-btn--sm" disabled={!text.trim() || add.isPending}>
          <Icon name="plus" />
          Adicionar
        </button>
      </form>

      {entries.isSuccess && active.length === 0 ? <Empty text="O agente ainda não aprendeu nada com você." /> : null}
      {entries.isError ? <Empty text="Não foi possível carregar o conhecimento." /> : null}
      {active.map((k) => (
        <div key={k.id} className="ia-row" style={{ gridTemplateColumns: '1fr auto auto', alignItems: 'center', cursor: 'default' }}>
          <span className="t-body">{k.text}</span>
          <Badge>{`${SCOPE_LABEL[k.scope] ?? k.scope}, ${k.source}`}</Badge>
          <button
            type="button"
            className="ia-btn ia-btn--ghost ia-btn--sm ia-btn--icon"
            aria-label={`Remover: ${k.text}`}
            disabled={remove.isPending}
            onClick={() => remove.mutate(k.id)}
          >
            <Icon name="x" />
          </button>
        </div>
      ))}
    </section>
  )
}
