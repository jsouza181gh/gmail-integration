import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { googleSignup, inviteInfo } from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { navigateTo } from '../../lib/nav'
import { Icon } from '../../ui/Icon'
import { GoogleWarningMocks } from './GoogleWarning'

// What the server puts in ?erro= after a Google callback that could not go on.
const PROBLEMS: Record<string, string> = {
  email_nao_verificado:
    'O Google diz que esse e-mail ainda não foi confirmado. Confirme o e-mail na sua conta do Google e tente de novo.',
  outro_email:
    'Você entrou com uma conta do Google diferente da que foi convidada. Tente de novo e escolha a conta do e-mail do convite.',
  permissao:
    'Faltou marcar alguma permissão. Tente de novo e, na tela do Google, marque todas as caixinhas antes de continuar.',
  falha:
    'Não deu certo desta vez, mas nada foi guardado e o convite continua valendo. Tente de novo; se repetir, avise quem convidou você.',
  use_o_convite:
    'Você tem um convite, mas a conta só é criada pelo link que você recebeu. Abra esse link de novo.',
}

export default function Entrar() {
  const [params] = useSearchParams()
  const token = params.get('convite')
  const problem = PROBLEMS[params.get('erro') ?? '']

  const invite = useQuery({
    queryKey: ['invite', token],
    queryFn: () => inviteInfo(token ?? ''),
    enabled: Boolean(token),
    retry: false,
  })
  const go = useMutation({
    mutationFn: () => googleSignup(token ?? ''),
    onSuccess: (r) => navigateTo(r.url),
  })

  const closed = !token || invite.error instanceof ApiError
  const closedText =
    invite.error instanceof ApiError
      ? invite.error.detail
      : 'Abra o link do convite que você recebeu para entrar.'

  return (
    <main className="ia-root login">
      <div className="login__box login__box--wide">
        <div className="login__head">
          <span className="login__mark">
            <Icon name="mail" size={24} />
          </span>
          <h1 className="h-title">Você foi convidado para o Inbox Agent</h1>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            O Inbox Agent lê os e-mails que chegam no seu Gmail, separa o que importa e sugere o que fazer
            com cada um. Nada é apagado, escondido ou enviado sem você aprovar.
          </p>
        </div>

        {problem ? (
          <p className="warn-line" role="alert">
            <span className="ia-ico-warning">
              <Icon name="alert" />
            </span>
            {problem}
          </p>
        ) : null}

        {closed ? (
          <div className="ia-card login__card" style={{ textAlign: 'center' }}>
            <p className="h-heading" style={{ margin: 0 }}>
              {closedText}
            </p>
            <Link className="ia-btn ia-btn--ghost ia-btn--sm" to="/login">
              Já tenho conta: entrar
            </Link>
          </div>
        ) : (
          <>
            <div className="ia-card login__card">
              <p className="h-heading" style={{ margin: 0 }}>
                {invite.data ? `Convite para ${invite.data.email}` : 'Conferindo o convite…'}
              </p>
              <p className="t-small ia-muted" style={{ margin: 0 }}>
                Você vai entrar com o Google, na conta desse e-mail. É um clique só: o mesmo passo conecta o
                seu Gmail.
              </p>
            </div>

            <section className="ia-card login__card" aria-labelledby="aviso">
              <h2 id="aviso" className="h-heading" style={{ margin: 0 }}>
                Antes de continuar: o Google vai mostrar um aviso
              </h2>
              <p className="t-small ia-muted" style={{ margin: 0 }}>
                O Inbox Agent é um app novo e pequeno, que o Google ainda não revisou. Por isso aparece um
                aviso de segurança. É normal e esperado, e não significa que algo está errado. Veja como
                passar:
              </p>
              <ol className="gwarn-steps">
                <li>
                  Escolha a conta do Google do e-mail do convite.
                </li>
                <li>
                  Quando aparecer <strong>“O Google não verificou este app”</strong>, clique em{' '}
                  <strong>Avançado</strong> (um link pequeno, abaixo do texto).
                </li>
                <li>
                  Depois clique em <strong>“Acessar Inbox Agent (não seguro)”</strong>.
                </li>
                <li>
                  Na tela de permissões, <strong>marque todas as caixinhas</strong> e clique em Continuar. Sem
                  elas o assistente não consegue ler os e-mails.
                </li>
              </ol>
              <GoogleWarningMocks />
            </section>

            <button
              type="button"
              className="ia-btn ia-btn--primary"
              style={{ width: '100%' }}
              disabled={go.isPending || !invite.data}
              onClick={() => go.mutate()}
            >
              Continuar com o Google
            </button>
            {go.error ? (
              <p className="warn-line" role="alert">
                <span className="ia-ico-warning">
                  <Icon name="alert" />
                </span>
                {go.error instanceof ApiError ? go.error.detail : 'Algo deu errado.'}
              </p>
            ) : null}
          </>
        )}
      </div>
    </main>
  )
}
