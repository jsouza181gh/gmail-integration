import { Link } from 'react-router'
import type { StepProps } from './steps'

// Signup connects the Gmail in the same click as the Google sign-in, so a user only
// lands here when that connection is missing: send them back to the invite link.
export function GmailStep(_props: StepProps) {
  return (
    <div className="ia-card login__card">
      <h2 className="h-heading" style={{ margin: 0 }}>
        Falta conectar o seu Gmail
      </h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Abra de novo o link do convite que você recebeu e entre com o Google. Se o link não funcionar mais,
        peça um novo a quem convidou você.
      </p>
      <Link className="ia-btn" to="/entrar">
        Abrir a página do convite
      </Link>
    </div>
  )
}
