import { useMutation } from '@tanstack/react-query'
import { Link } from 'react-router'
import { connectGmail } from '../api/endpoints'
import { useWarnings } from '../api/hooks'
import type { WarningKind } from '../api/schemas'
import { navigateTo } from '../lib/nav'
import { Icon } from '../ui/Icon'

interface Copy {
  title: string
  text: string
  action: 'reconnect' | 'settings' | 'onboarding'
  cta: string
}

const COPY: Record<WarningKind, Copy> = {
  no_gmail: {
    title: 'Falta conectar seu Gmail',
    text: 'Sem ele o assistente não tem e-mails para organizar.',
    action: 'onboarding',
    cta: 'Conectar',
  },
  gmail_revoked: {
    title: 'Reconecte o Gmail',
    text: 'O Google desligou o acesso do assistente à sua caixa de entrada. Enquanto isso, e-mails novos não chegam aqui.',
    action: 'reconnect',
    cta: 'Reconectar Gmail',
  },
  gmail_disconnected: {
    title: 'Seu Gmail está desconectado',
    text: 'Os e-mails que já estão aqui continuam salvos, mas os novos não chegam.',
    action: 'reconnect',
    cta: 'Reconectar Gmail',
  },
  key_missing: {
    title: 'Falta a chave do Gemini',
    text: 'Sem ela o assistente não consegue ler nem responder seus e-mails.',
    action: 'settings',
    cta: 'Cadastrar a chave',
  },
  key_invalid: {
    title: 'Sua chave do Gemini parou de funcionar',
    text: 'Os e-mails novos ficam esperando até você trocar a chave.',
    action: 'settings',
    cta: 'Trocar a chave',
  },
}

export function WarningsBanner() {
  const warnings = useWarnings()
  const reconnect = useMutation({
    mutationFn: connectGmail,
    onSuccess: (out) => navigateTo(out.url),
  })
  if (!warnings.data || warnings.data.length === 0) return null

  return (
    <div className="warnings" role="region" aria-label="Avisos">
      {warnings.data.map(({ kind }) => {
        const c = COPY[kind]
        return (
          <div key={kind} className="warnings__item" role="alert">
            <span className="ia-ico-warning">
              <Icon name="alert" />
            </span>
            <span className="warnings__text">
              <strong>{c.title}.</strong> <span className="ia-muted">{c.text}</span>
            </span>
            {c.action === 'reconnect' ? (
              <button
                type="button"
                className="ia-btn ia-btn--sm"
                disabled={reconnect.isPending}
                onClick={() => reconnect.mutate()}
              >
                {c.cta}
              </button>
            ) : (
              <Link className="ia-btn ia-btn--sm" to={c.action === 'settings' ? '/configuracoes' : '/onboarding'}>
                {c.cta}
              </Link>
            )}
          </div>
        )
      })}
      {reconnect.isError ? (
        <p className="warn-line" role="alert">
          Não deu para abrir o Google agora. Tente de novo em instantes.
        </p>
      ) : null}
    </div>
  )
}
