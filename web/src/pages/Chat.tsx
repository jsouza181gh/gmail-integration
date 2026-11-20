import { Empty } from '../ui/Empty'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'

export default function Chat() {
  return (
    <Page>
      <PageHead title="Chat" subtitle="Pergunte sobre seus e-mails e documentos ou dite uma regra." />
      <Empty text="O chat chega no próximo subprojeto." />
      <div className="chat-composer">
        <label style={{ flex: '1 1 0', display: 'flex', alignItems: 'center', minHeight: 40 }}>
          <input
            disabled
            placeholder="Pergunte ou dite uma regra…"
            aria-label="Mensagem"
            className="chat-composer__input"
          />
        </label>
        <button type="button" className="ia-btn ia-btn--primary ia-btn--icon" disabled aria-label="Enviar">
          <Icon name="chevronright" />
        </button>
      </div>
      <p className="t-small ia-subtle" style={{ margin: 0, textAlign: 'center' }}>
        Mudanças de regra e de configuração sempre pedem confirmação.
      </p>
    </Page>
  )
}
