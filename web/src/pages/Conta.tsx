import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import * as api from '../api/endpoints'
import { ApiError } from '../api/errors'
import { Badge } from '../ui/Badge'
import { Empty } from '../ui/Empty'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { useToast } from '../ui/Toast'

type Confirm = 'password' | 'code'

export default function Conta() {
  const toast = useToast()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const account = useQuery({ queryKey: ['account'], queryFn: api.account })

  const [askDisconnect, setAskDisconnect] = useState(false)
  const [askDelete, setAskDelete] = useState(false)
  const [email, setEmail] = useState('')
  const [how, setHow] = useState<Confirm>('password')
  const [secret, setSecret] = useState('')

  const disconnect = useMutation({
    mutationFn: api.disconnectGmail,
    onSuccess: async () => {
      setAskDisconnect(false)
      toast.show('Gmail desconectado. Seus dados continuam guardados.')
      await qc.invalidateQueries({ queryKey: ['account'] })
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível desconectar.'),
  })

  const remove = useMutation({
    mutationFn: () =>
      api.deleteAccount({ email, ...(how === 'password' ? { password: secret } : { code: secret }) }),
    onSuccess: () => {
      qc.clear()
      navigate('/login', { replace: true })
    },
  })

  if (account.isError) return <Page><Empty text="Não foi possível carregar a conta." /></Page>
  const info = account.data
  const typedOk = info !== undefined && email.trim().toLowerCase() === info.email.toLowerCase()
  const deleteError = remove.error instanceof ApiError ? remove.error.detail : remove.isError ? 'Não foi possível excluir a conta.' : null

  return (
    <Page>
      <PageHead
        title="Sua conta"
        subtitle={info ? info.email : undefined}
        actions={
          info ? (
            <Badge tone={info.status === 'connected' ? 'accent' : 'warning'}>
              {info.status === 'connected' ? 'Gmail conectado' : 'Gmail desconectado'}
            </Badge>
          ) : undefined
        }
      />

      <div style={{ display: 'grid', gap: 'var(--space-4)', maxWidth: 640 }}>
        <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Desconectar o Gmail">
          <h2 className="h-heading">Desconectar o Gmail</h2>
          <p className="t-body ia-muted" style={{ margin: 0 }}>
            Para de acompanhar sua caixa de entrada e tira o acesso que você deu ao Google. Nada é apagado:
            seus e-mails já lidos, regras e preferências continuam aqui. Quando reconectar, o assistente
            continua de onde parou.
          </p>
          {askDisconnect ? (
            <div className="row" style={{ gap: 8 }}>
              <button type="button" className="ia-btn ia-btn--danger" disabled={disconnect.isPending}
                onClick={() => disconnect.mutate()}>
                Sim, desconectar
              </button>
              <button type="button" className="ia-btn ia-btn--ghost" onClick={() => setAskDisconnect(false)}>
                Cancelar
              </button>
            </div>
          ) : (
            <div>
              <button type="button" className="ia-btn" disabled={info?.status !== 'connected'}
                onClick={() => setAskDisconnect(true)}>
                <Icon name="logout" />
                Desconectar o Gmail
              </button>
            </div>
          )}
        </section>

        <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Excluir conta">
          <h2 className="h-heading">Excluir conta</h2>
          <p className="t-body ia-muted" style={{ margin: 0 }}>
            Apaga tudo agora e não tem volta: seus e-mails e anexos guardados, regras, preferências, conversas
            do chat e o seu acesso. Também desconectamos o Gmail. Para voltar depois, o seu convite continua
            valendo e é só entrar de novo, mas começando do zero.
          </p>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            {info
              ? `Cópias de segurança: o sistema guarda as ${info.backup_copies} cópias mais recentes do banco de dados e cria uma nova a cada atualização. Seus dados saem delas conforme as cópias antigas forem sendo substituídas, ou seja, depois de ${info.backup_copies} atualizações do sistema. Não dá para dizer em quantos dias, porque depende de quando o sistema for atualizado.`
              : null}
          </p>
          {info && !info.can_delete ? (
            <p className="warn-line" style={{ margin: 0 }}>
              <span className="ia-ico-warning"><Icon name="lock" /></span>
              Esta é a conta de administração e não pode ser excluída pelo painel.
            </p>
          ) : askDelete ? (
            <form
              style={{ display: 'grid', gap: 12 }}
              onSubmit={(e) => {
                e.preventDefault()
                if (typedOk && secret) remove.mutate()
              }}
            >
              <label className="ia-field">
                <span className="ia-label">{`Digite ${info?.email ?? 'o e-mail da conta'} para confirmar`}</span>
                <span className="ia-input">
                  <input type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
                </span>
              </label>
              <label className="ia-field">
                <span className="ia-label">{how === 'password' ? 'Sua senha' : 'Código de 6 dígitos do app autenticador'}</span>
                <span className="ia-input">
                  <input
                    type={how === 'password' ? 'password' : 'text'}
                    inputMode={how === 'password' ? undefined : 'numeric'}
                    autoComplete={how === 'password' ? 'current-password' : 'one-time-code'}
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                  />
                </span>
              </label>
              <button
                type="button"
                className="ia-btn ia-btn--ghost ia-btn--sm"
                style={{ justifySelf: 'start' }}
                onClick={() => {
                  setHow(how === 'password' ? 'code' : 'password')
                  setSecret('')
                }}
              >
                {how === 'password' ? 'Usar código do app autenticador' : 'Usar minha senha'}
              </button>
              {deleteError ? (
                <p className="warn-line" role="alert" style={{ margin: 0 }}>
                  <span className="ia-ico-warning"><Icon name="alert" /></span>
                  {deleteError}
                </p>
              ) : null}
              <div className="row" style={{ gap: 8 }}>
                <button type="submit" className="ia-btn ia-btn--danger" disabled={!typedOk || !secret || remove.isPending}>
                  <Icon name="trash" />
                  Excluir tudo para sempre
                </button>
                <button type="button" className="ia-btn ia-btn--ghost" onClick={() => setAskDelete(false)}>
                  Cancelar
                </button>
              </div>
            </form>
          ) : (
            <div>
              <button type="button" className="ia-btn ia-btn--danger" disabled={!info} onClick={() => setAskDelete(true)}>
                <Icon name="trash" />
                Excluir minha conta
              </button>
            </div>
          )}
        </section>
      </div>
    </Page>
  )
}
