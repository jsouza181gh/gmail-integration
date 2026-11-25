import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { finishOnboarding } from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { qk } from '../../api/hooks'
import { Icon } from '../../ui/Icon'
import { Switch } from '../../ui/Switch'
import type { StepProps } from './steps'

// The last onboarding screen: what happens now, and the optional history import.
export function FinishStep({ onAdvance }: StepProps) {
  const qc = useQueryClient()
  const [backfill, setBackfill] = useState(true)
  const finish = useMutation({
    mutationFn: () => finishOnboarding(backfill),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: qk.me })
      onAdvance()
    },
  })

  return (
    <div className="ia-card login__card">
      <div className="stack" style={{ gap: 8 }}>
        <span className="ia-ico-accent">
          <Icon name="check" size={28} />
        </span>
        <h2 className="h-heading" style={{ margin: 0 }}>
          Tudo pronto
        </h2>
        <p className="t-body ia-muted" style={{ margin: 0 }}>
          Seu Gmail está conectado e a conta está protegida.
        </p>
      </div>
      <div className="stack" style={{ gap: 8 }}>
        <h3 className="h-heading">O que acontece agora</h3>
        <ul className="t-small ia-muted" style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
          <li>Cada e-mail novo que chegar é lido, classificado e organizado automaticamente.</li>
          <li>Nada é arquivado, apagado ou movido sem você aprovar.</li>
          <li>Você pode conversar com o assistente sobre seus e-mails na aba Chat.</li>
        </ul>
      </div>
      <Switch
        checked={backfill}
        onChange={setBackfill}
        label="Organizar também meus e-mails antigos"
        description="Eles vão aparecendo aos poucos, ao longo de alguns dias, sem atrapalhar os novos. Você pode parar quando quiser."
      />
      {finish.isError ? (
        <p className="warn-line" role="alert">
          {finish.error instanceof ApiError ? finish.error.detail : 'Não foi possível terminar agora. Tente de novo.'}
        </p>
      ) : null}
      <button type="button" className="ia-btn ia-btn--primary" disabled={finish.isPending} onClick={() => finish.mutate()}>
        {backfill ? 'Começar e organizar o histórico' : 'Começar sem o histórico'}
      </button>
    </div>
  )
}
