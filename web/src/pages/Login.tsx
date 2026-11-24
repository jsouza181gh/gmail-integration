import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { googleLogin, login, secondFactor } from '../api/endpoints'
import { ApiError } from '../api/errors'
import { qk } from '../api/hooks'
import { navigateTo } from '../lib/nav'
import { Icon } from '../ui/Icon'

type Step = 'password' | 'code' | 'blocked'

// What the server puts in ?erro= when a Google sign-in cannot go on.
const GOOGLE_PROBLEMS: Record<string, string> = {
  email_nao_verificado: 'O Google diz que esse e-mail ainda não foi confirmado.',
  ja_tem_conta: 'Esse e-mail já tem conta. Entre com o Google ou com a sua senha.',
}

function destination(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from
  return typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/'
}

export default function Login() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const erro = params.get('erro') ?? ''
  const [step, setStep] = useState<Step>(
    erro === 'bloqueado' ? 'blocked' : params.get('etapa') === 'codigo' ? 'code' : 'password',
  )
  const [recovery, setRecovery] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(GOOGLE_PROBLEMS[erro] ?? null)
  const [busy, setBusy] = useState(false)

  const submitGoogle = async () => {
    setBusy(true)
    setError(null)
    try {
      navigateTo((await googleLogin()).url)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Algo deu errado.')
      setBusy(false)
    }
  }

  const submitPassword = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
      setPassword('')
      setStep('code')
    } catch (err) {
      if (err instanceof ApiError && err.status === 423) setStep('blocked')
      else if (err instanceof ApiError && err.status === 401) setError('Usuário ou senha incorretos.')
      else setError(err instanceof ApiError ? err.detail : 'Algo deu errado.')
    } finally {
      setBusy(false)
    }
  }

  const submitCode = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await secondFactor(code.trim())
      qc.removeQueries({ queryKey: qk.me })
      navigate(destination(location.state), { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 423) setStep('blocked')
      else if (err instanceof ApiError && err.status === 401 && err.detail.includes('login de novo')) {
        setStep('password')
        setCode('')
        setError('Faça o login de novo.')
      } else if (err instanceof ApiError && err.status === 401) setError('Código errado.')
      else setError(err instanceof ApiError ? err.detail : 'Algo deu errado.')
    } finally {
      setBusy(false)
    }
  }

  const heading =
    step === 'password'
      ? 'Entrar no Inbox Agent'
      : step === 'code'
        ? recovery
          ? 'Recovery code'
          : 'Confirme que é você'
        : 'Acesso bloqueado'
  const sub =
    step === 'password'
      ? 'Entre com o Google ou com a sua senha. O código do app autenticador é pedido sempre.'
      : step === 'code'
        ? recovery
          ? 'Digite um dos 10 códigos guardados. Cada um vale uma vez.'
          : 'Abra o app autenticador e digite o código atual.'
        : 'Use a VPS para desbloquear.'

  return (
    <main className="ia-root login">
      <div className="login__box">
        <div className="login__head">
          <span className="login__mark">
            <Icon name="mail" size={24} />
          </span>
          <h1 className="h-title">{heading}</h1>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            {sub}
          </p>
        </div>

        {step === 'password' ? (
          <form className="ia-card login__card" onSubmit={(e) => void submitPassword(e)}>
            <button
              type="button"
              className="ia-btn ia-btn--primary"
              disabled={busy}
              onClick={() => void submitGoogle()}
            >
              Entrar com o Google
            </button>
            <p className="t-small ia-muted login__or">ou use a senha</p>
            <label className="ia-field">
              <span className="ia-label">Usuário</span>
              <span className="ia-input">
                <input
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
                  placeholder="usuário ou e-mail"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                />
              </span>
            </label>
            <label className="ia-field">
              <span className="ia-label">Senha</span>
              <span className="ia-input">
                <Icon name="lock" />
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </span>
            </label>
            {error ? <ErrorLine text={error} /> : null}
            <button className="ia-btn" type="submit" disabled={busy} style={{ width: '100%' }}>
              Continuar
            </button>
          </form>
        ) : null}

        {step === 'code' ? (
          <form className="ia-card login__card" onSubmit={(e) => void submitCode(e)}>
            <label className="ia-field">
              <span className="ia-label">{recovery ? 'Recovery code' : 'Código de 6 dígitos'}</span>
              <span className="ia-input" style={{ height: 56 }}>
                <input
                  className="login__code"
                  inputMode={recovery ? 'text' : 'numeric'}
                  autoComplete="one-time-code"
                  placeholder={recovery ? 'xxxx-xxxx' : '000000'}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  required
                />
              </span>
            </label>
            {error ? <ErrorLine text={error} /> : null}
            <button className="ia-btn ia-btn--primary" type="submit" disabled={busy} style={{ width: '100%' }}>
              Entrar
            </button>
            <button
              className="ia-btn ia-btn--ghost ia-btn--sm"
              type="button"
              onClick={() => {
                setRecovery((r) => !r)
                setCode('')
                setError(null)
              }}
            >
              <Icon name="keyround" size={14} />
              {recovery ? 'Usar o código do app' : 'Usar um recovery code'}
            </button>
          </form>
        ) : null}

        {step === 'blocked' ? (
          <div className="ia-card login__card" style={{ textAlign: 'center' }}>
            <span className="ia-ico-danger" style={{ justifySelf: 'center' }}>
              <Icon name="ban" size={28} />
            </span>
            <p className="h-heading" style={{ margin: 0 }}>
              Login bloqueado
            </p>
            <p className="t-small ia-muted" style={{ margin: 0 }}>
              Foram várias tentativas erradas. O desbloqueio é feito no servidor, por quem administra.
            </p>
          </div>
        ) : null}
      </div>
    </main>
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
