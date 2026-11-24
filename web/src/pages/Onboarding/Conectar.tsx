import { useMutation } from '@tanstack/react-query'
import { Navigate } from 'react-router'
import { connectGmail } from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { useMe } from '../../api/hooks'
import { navigateTo } from '../../lib/nav'
import { Icon } from '../../ui/Icon'

// Where a logged-in user with no Gmail account lands (any API call answers 409
// `no_account`). Phase 3's onboarding replaces this screen with the full flow.
export default function Conectar() {
  const me = useMe()
  const connect = useMutation({ mutationFn: connectGmail, onSuccess: (out) => navigateTo(out.url) })

  if (me.isPending) return <div className="ia-root boot" role="status">Carregando…</div>
  if (me.error instanceof ApiError && me.error.status === 401) return <Navigate to="/login" replace />

  return (
    <main className="ia-root finish">
      <div className="ia-card finish__card">
        <span className="ia-ico-accent">
          <Icon name="mail" size={28} />
        </span>
        <h1 className="h-display">Conecte seu Gmail</h1>
        <p className="t-body ia-muted" style={{ margin: 0 }}>
          Para o assistente organizar seus e-mails, ele precisa de acesso à sua caixa de entrada. O Google vai
          pedir sua permissão. Nada é apagado nem movido sem você aprovar.
        </p>
        {connect.isError ? (
          <p className="warn-line" role="alert">
            Não deu para abrir o Google agora. Tente de novo em instantes.
          </p>
        ) : null}
        <button type="button" className="ia-btn ia-btn--primary" disabled={connect.isPending} onClick={() => connect.mutate()}>
          Conectar com o Google
        </button>
      </div>
    </main>
  )
}
