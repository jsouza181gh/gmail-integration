import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { TOTP_QR_URL, recoveryAck, totpConfirm, totpStart } from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import type { TotpStart } from '../../api/schemas'
import { saveText } from '../../lib/saveText'
import { Check } from '../../ui/Check'
import { Icon } from '../../ui/Icon'
import type { StepProps } from './steps'

const APPS = [
  {
    name: 'Google Authenticator',
    android: 'https://play.google.com/store/apps/details?id=com.google.android.apps.authenticator2',
    ios: 'https://apps.apple.com/app/google-authenticator/id388497605',
  },
  {
    name: 'Microsoft Authenticator',
    android: 'https://play.google.com/store/apps/details?id=com.azure.authenticator',
    ios: 'https://apps.apple.com/app/microsoft-authenticator/id983156458',
  },
]

type Phase = 'intro' | 'scan' | 'codes'

function message(err: unknown): string {
  return err instanceof ApiError ? err.detail : 'Algo deu errado. Tente de novo.'
}

export function TotpStep({ state, onAdvance }: StepProps) {
  const [phase, setPhase] = useState<Phase>('intro')
  const [started, setStarted] = useState<TotpStart | null>(null)
  const [nonce, setNonce] = useState(0)
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState<string[]>([])
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState(false)

  const start = useMutation({
    mutationFn: totpStart,
    onSuccess: (r) => {
      setStarted(r)
      setNonce((n) => n + 1)
      setCode('')
      setPhase('scan')
    },
  })
  const confirm = useMutation({
    mutationFn: () => totpConfirm(code.trim()),
    onSuccess: (r) => {
      setCodes(r.codes)
      setPhase('codes')
    },
  })
  const ack = useMutation({ mutationFn: recoveryAck, onSuccess: onAdvance })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    confirm.mutate()
  }
  const text = codes.join('\n')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  if (phase === 'intro') {
    return (
      <div className="ia-card login__card">
        <h2 className="h-heading" style={{ margin: 0 }}>
          Vamos proteger sua conta com um código
        </h2>
        <p className="t-small ia-muted" style={{ margin: 0 }}>
          Toda vez que você entrar, vai digitar um número de 6 dígitos que muda a cada 30 segundos. Ele
          aparece em um app no seu celular, o “autenticador”. Assim, mesmo que alguém descubra sua senha,
          não entra.
        </p>
        <p className="t-small" style={{ margin: 0 }}>
          <strong>1.</strong> Instale um destes apps no celular (é grátis):
        </p>
        <ul className="store-list">
          {APPS.map((a) => (
            <li key={a.name}>
              <strong>{a.name}</strong>
              {' · '}
              <a href={a.android} target="_blank" rel="noreferrer">
                Android
              </a>
              {' · '}
              <a href={a.ios} target="_blank" rel="noreferrer">
                iPhone
              </a>
            </li>
          ))}
        </ul>
        {state.totp_confirmed ? (
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            Você já confirmou o app, mas os códigos de recuperação só aparecem uma vez. Vamos refazer esta
            etapa para gerar novos.
          </p>
        ) : null}
        {start.error ? <ErrorLine text={message(start.error)} /> : null}
        <button
          type="button"
          className="ia-btn ia-btn--primary"
          disabled={start.isPending}
          onClick={() => start.mutate()}
        >
          Já instalei o app
        </button>
      </div>
    )
  }

  if (phase === 'scan' && started) {
    return (
      <form className="ia-card login__card" onSubmit={submit}>
        <p className="t-small" style={{ margin: 0 }}>
          <strong>2.</strong> Abra o app, toque em <strong>+</strong> (adicionar) e escolha{' '}
          <strong>Ler código QR</strong>. Aponte a câmera para a imagem:
        </p>
        <img
          className="totp-qr"
          src={`${TOTP_QR_URL}?v=${nonce}`}
          alt="QR code para o app autenticador"
          width={220}
          height={220}
        />
        <details>
          <summary className="t-small">Não consegue ler o QR? Digite a chave no app</summary>
          <p className="t-small totp-secret">{started.secret}</p>
        </details>
        <label className="ia-field">
          <span className="ia-label">
            <strong>3.</strong> Digite o código de 6 dígitos que o app mostra
          </span>
          <span className="ia-input" style={{ height: 56 }}>
            <input
              className="login__code"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="000000"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
            />
          </span>
        </label>
        {confirm.error ? <ErrorLine text={message(confirm.error)} /> : null}
        <button className="ia-btn ia-btn--primary" type="submit" disabled={confirm.isPending}>
          Confirmar código
        </button>
      </form>
    )
  }

  return (
    <div className="ia-card login__card">
      <h2 className="h-heading" style={{ margin: 0 }}>
        Guarde seus códigos de recuperação
      </h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Se você perder o celular, estes códigos são a única forma de entrar. Cada um vale uma vez e eles{' '}
        <strong>não aparecem de novo</strong>. Guarde fora do computador: foto na galeria protegida,
        gerenciador de senhas ou papel.
      </p>
      <ul className="recovery-grid" aria-label="Códigos de recuperação">
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <div className="recovery-actions">
        <button type="button" className="ia-btn ia-btn--sm" onClick={() => void copy()}>
          {copied ? 'Copiados' : 'Copiar códigos'}
        </button>
        <button
          type="button"
          className="ia-btn ia-btn--sm"
          onClick={() => saveText('inbox-agent-codigos-de-recuperacao.txt', `${text}\n`)}
        >
          <Icon name="download" size={14} />
          Baixar arquivo
        </button>
      </div>
      <Check checked={saved} onChange={() => setSaved((v) => !v)}>
        Guardei meus códigos em um lugar seguro
      </Check>
      {ack.error ? <ErrorLine text={message(ack.error)} /> : null}
      <button
        type="button"
        className="ia-btn ia-btn--primary"
        disabled={!saved || ack.isPending}
        onClick={() => ack.mutate()}
      >
        Continuar
      </button>
    </div>
  )
}

function ErrorLine({ text }: { text: string }) {
  return (
    <p className="warn-line" role="alert">
      <span className="ia-ico-warning">
        <Icon name="alert" />
      </span>
      {text}
    </p>
  )
}
