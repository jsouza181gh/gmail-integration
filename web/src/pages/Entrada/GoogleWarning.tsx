import type { ReactNode } from 'react'

// A faithful sketch of the two Google screens a guest must click through (the app is
// "not verified" by Google). Drawn, not screenshotted: Google changes the pixels,
// not the words, and the words are what a lay person has to recognise.
function Mock({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="gwarn" role="img" aria-label={label}>
      {children}
    </div>
  )
}

export function GoogleWarningMocks() {
  return (
    <div className="gwarn-list">
      <Mock label="Exemplo da primeira tela do Google: O Google não verificou este app, com o link Avançado destacado">
        <p className="gwarn__title">O Google não verificou este app</p>
        <p className="gwarn__text">
          O app está solicitando acesso a dados confidenciais da sua Conta do Google. Até que o
          desenvolvedor verifique este app, você não deveria usá-lo.
        </p>
        <p className="gwarn__row">
          <span className="gwarn__link gwarn__link--hot">Avançado</span>
          <span className="gwarn__btn">Voltar à segurança</span>
        </p>
      </Mock>
      <Mock label="Exemplo da segunda tela do Google: o link Acessar Inbox Agent (não seguro) destacado">
        <p className="gwarn__text">
          Ao continuar, você permite que o app acesse sua conta, como combinado nas próximas telas.
        </p>
        <p className="gwarn__row">
          <span className="gwarn__link gwarn__link--hot">Acessar Inbox Agent (não seguro)</span>
        </p>
      </Mock>
    </div>
  )
}
