import { Navigate, useLocation } from 'react-router'
import { useMe } from '../api/hooks'
import { ApiError } from '../api/errors'
import { Empty } from '../ui/Empty'
import { Shell } from './Shell'

export function RequireAuth() {
  const me = useMe()
  const location = useLocation()

  if (me.isPending) {
    return (
      <div className="ia-root boot" role="status">
        Carregando…
      </div>
    )
  }
  if (me.error instanceof ApiError && me.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  if (me.isError) {
    return (
      <div className="ia-root boot">
        <Empty
          text="Não foi possível falar com o servidor."
          action={
            <button type="button" className="ia-btn" onClick={() => void me.refetch()}>
              Tentar de novo
            </button>
          }
        />
      </div>
    )
  }
  return <Shell />
}
