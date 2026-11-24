import { Link } from 'react-router'
import { Icon } from '../../ui/Icon'

export default function NaoConvidado() {
  return (
    <main className="ia-root login">
      <div className="login__box">
        <div className="login__head">
          <span className="login__mark">
            <Icon name="lock" size={24} />
          </span>
          <h1 className="h-title">Você ainda não foi convidado</h1>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            O Inbox Agent funciona só por convite, por enquanto. Peça um convite a quem apresentou o
            assistente a você: chega um link para abrir, e a entrada leva poucos minutos.
          </p>
        </div>
        <div className="ia-card login__card" style={{ textAlign: 'center' }}>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            Se você já tem conta, entre por aqui.
          </p>
          <Link className="ia-btn" to="/login">
            Entrar
          </Link>
        </div>
      </div>
    </main>
  )
}
