import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { saveLlmKey } from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { qk } from '../../api/hooks'
import { Icon } from '../../ui/Icon'

const AI_STUDIO = 'https://aistudio.google.com/apikey'

/** The Gemini key form: the user pastes their own key; it is tested on the spot.
 * Used as an onboarding step and as the standalone /onboarding/chave page. */
export function LlmKeyForm({ onContinue }: { onContinue: () => void }) {
  const qc = useQueryClient()
  const [key, setKey] = useState('')
  const [second, setSecond] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await saveLlmKey(key.trim(), second.trim() || undefined)
      setKey('')
      setSecond('')
      setOk(true)
      void qc.invalidateQueries({ queryKey: qk.backfill })
    } catch (err) {
      setOk(false)
      setError(err instanceof ApiError ? err.detail : 'Algo deu errado. Tente de novo.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="ia-card login__card" onSubmit={(e) => void submit(e)}>
      <h2 className="h-heading" style={{ margin: 0 }}>
        Chave do Gemini
      </h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        O assistente usa a inteligência artificial do Google (o Gemini) para ler e organizar seus
        e-mails. Para isso ele precisa de uma chave: uma senha só sua, que o Google dá de graça. Leva
        uns 2 minutos.
      </p>
      <ol className="t-body" style={{ margin: 0, paddingLeft: 20, display: 'grid', gap: 8 }}>
        <li>
          Abra o{' '}
          <a href={AI_STUDIO} target="_blank" rel="noopener noreferrer">
            Google AI Studio (aistudio.google.com/apikey)
          </a>{' '}
          e entre com a sua conta do Google.
        </li>
        <li>
          Clique em <strong>Criar chave de API</strong>. Se o Google pedir um projeto, escolha
          criar um novo.
        </li>
        <li>
          Copie o texto grande que aparece (ele começa com <code>AIza</code>) e cole aqui
          embaixo.
        </li>
      </ol>

      <label className="ia-field">
        <span className="ia-label">Sua chave</span>
        <span className="ia-input">
          <Icon name="lock" />
          <input
            type="password"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="AIza..."
            value={key}
            onChange={(e) => setKey(e.target.value)}
            required
          />
        </span>
      </label>

      <p className="t-small ia-muted" style={{ margin: 0 }}>
        <strong>Quanto custa?</strong> O plano gratuito do Google costuma bastar para o uso do
        dia a dia, e não precisa de cartão. Se um dia você quiser mais velocidade, dá para ligar
        a cobrança direto no Google, e é você quem controla o gasto lá. A chave fica guardada
        cifrada e só o seu assistente a usa.
      </p>

      <details>
        <summary className="t-small">Avançado: uma segunda chave (opcional)</summary>
        <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            Para os e-mails antigos não gastarem a cota do dia a dia, você pode criar uma
            segunda chave (em um projeto novo no mesmo AI Studio) e colar aqui. Se não souber o
            que é isso, deixe em branco.
          </p>
          <label className="ia-field">
            <span className="ia-label">Segunda chave</span>
            <span className="ia-input">
              <input
                type="password"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                placeholder="AIza..."
                value={second}
                onChange={(e) => setSecond(e.target.value)}
              />
            </span>
          </label>
        </div>
      </details>

      {error ? (
        <p className="warn-line" role="alert">
          <span className="ia-ico-warning">
            <Icon name="alert" />
          </span>
          {error}
        </p>
      ) : null}
      {ok ? (
        <p className="warn-line" role="status">
          <span className="ia-ico-accent">
            <Icon name="check" />
          </span>
          Chave funcionando! O assistente já pode trabalhar nos seus e-mails.
        </p>
      ) : null}

      {ok ? (
        <button
          className="ia-btn ia-btn--primary"
          type="button"
          style={{ width: '100%' }}
          onClick={onContinue}
        >
          Continuar
        </button>
      ) : (
        <button className="ia-btn ia-btn--primary" type="submit" disabled={busy} style={{ width: '100%' }}>
          {busy ? 'Testando a chave…' : 'Testar e salvar'}
        </button>
      )}
    </form>
  )
}

/** Standalone page (replacing the key later, outside the onboarding). */
export default function LlmKey() {
  const navigate = useNavigate()
  return (
    <main className="ia-root login">
      <div className="login__box" style={{ maxWidth: 520 }}>
        <div className="login__head">
          <span className="login__mark">
            <Icon name="lock" size={24} />
          </span>
          <h1 className="h-title">Configurar a inteligência artificial</h1>
        </div>
        <LlmKeyForm onContinue={() => navigate('/')} />
      </div>
    </main>
  )
}
