import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Navigate } from 'react-router'
import { onboardingState } from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { qk } from '../../api/hooks'
import { Icon } from '../../ui/Icon'
import { Progress } from '../../ui/Progress'
import { STEP_PAGES } from './steps'

const stateKey = ['onboarding'] as const

export default function Onboarding() {
  const qc = useQueryClient()
  const state = useQuery({ queryKey: stateKey, queryFn: onboardingState, retry: false })

  if (state.isPending) {
    return (
      <div className="ia-root boot" role="status">
        Carregando…
      </div>
    )
  }
  if (state.error instanceof ApiError && state.error.status === 401) {
    return <Navigate to="/login" replace />
  }
  if (state.isError) {
    return (
      <main className="ia-root login">
        <div className="login__box">
          <p className="warn-line" role="alert">
            Não foi possível falar com o servidor.
          </p>
          <button type="button" className="ia-btn" onClick={() => void state.refetch()}>
            Tentar de novo
          </button>
        </div>
      </main>
    )
  }

  const s = state.data
  if (s.done) return <Navigate to="/" replace />

  // The last entry of the list is the finish line, not a screen.
  const screens = s.steps.slice(0, -1)
  const index = Math.max(0, screens.indexOf(s.step ?? ''))
  const info = STEP_PAGES[s.step ?? '']
  const advance = () => {
    qc.removeQueries({ queryKey: qk.me }) // the cached "me" knows the old step
    void qc.invalidateQueries({ queryKey: stateKey })
  }

  return (
    <main className="ia-root login">
      <div className="login__box login__box--wide">
        <div className="login__head">
          <span className="login__mark">
            <Icon name="shield" size={24} />
          </span>
          <h1 className="h-title">Vamos terminar de preparar sua conta</h1>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            Etapa {index + 1} de {screens.length}
            {info ? `: ${info.label}` : ''}
          </p>
          <Progress value={((index + 1) / screens.length) * 100} />
        </div>
        {info ? (
          <info.Page state={s} onAdvance={advance} />
        ) : (
          <div className="ia-card login__card">
            <p className="t-small ia-muted" style={{ margin: 0 }}>
              Esta etapa ainda não está disponível nesta versão do painel. Tente de novo mais tarde.
            </p>
          </div>
        )}
      </div>
    </main>
  )
}
