import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { setPassword, skipPassword } from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { Icon } from '../../ui/Icon'
import type { StepProps } from './steps'

export function PasswordStep({ onAdvance }: StepProps) {
  const [password, setValue] = useState('')
  const save = useMutation({ mutationFn: () => setPassword(password), onSuccess: onAdvance })
  const skip = useMutation({ mutationFn: skipPassword, onSuccess: onAdvance })
  const error = save.error ?? skip.error

  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate()
  }

  return (
    <form className="ia-card login__card" onSubmit={submit}>
      <h2 className="h-heading" style={{ margin: 0 }}>
        Quer também uma senha? (opcional)
      </h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Você sempre pode entrar com o Google. Se preferir, crie uma senha para entrar também com e-mail e
        senha. Nos dois casos, o código do app autenticador continua sendo pedido.
      </p>
      <label className="ia-field">
        <span className="ia-label">Senha (mínimo 12 caracteres)</span>
        <span className="ia-input">
          <Icon name="lock" />
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setValue(e.target.value)}
          />
        </span>
      </label>
      {error ? (
        <p className="warn-line" role="alert">
          <span className="ia-ico-warning">
            <Icon name="alert" />
          </span>
          {error instanceof ApiError ? error.detail : 'Algo deu errado. Tente de novo.'}
        </p>
      ) : null}
      <button
        className="ia-btn ia-btn--primary"
        type="submit"
        disabled={save.isPending || password.length === 0}
      >
        Salvar senha
      </button>
      <button
        className="ia-btn ia-btn--ghost ia-btn--sm"
        type="button"
        disabled={skip.isPending}
        onClick={() => skip.mutate()}
      >
        Não quero senha agora
      </button>
    </form>
  )
}
