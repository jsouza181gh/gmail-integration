# Chat utilizável — design

Data: 2026-11-20. Complementa o spec guarda-chuva (`2026-11-19-gmail-assistant-design.md`, subprojeto 5).

## Problema (conversa real de 2026-11-19, runs 25–33)

O usuário pediu para ver e apagar e-mails do spam. O modelo chamou as tools certas
(`show_emails`, `propose_email_action(trash)`), mas todas falharam com
`nenhum desses e-mails está guardado` / `e-mail não encontrado`: só a INBOX é
ingerida, então ids de spam/lixeira/antigos vindos de `list_emails` nunca existem
na tabela `emails`. Sem saída, o modelo improvisou: perguntou de novo em vez de
mostrar, ofereceu uma regra (que não age em e-mail existente e saiu com o endereço
inventado `google-alerts-noreply@google.com`), mandou apagar pelo painel e inventou
"a busca por ID não localiza no spam". Além disso, confirmar um cartão não retoma a
conversa, o histórico só leva o texto final (sem ids), não há ação em lote e o chat
usa o flash-lite.

## Decisões

1. **E-mail sob demanda.** Quando uma tool do chat recebe um id que não está no
   banco, busca os metadados no Gmail e grava uma linha mínima em `emails` com
   `source = 'chat'` (sem classificar, sem indexar, sem embedding). Linhas `chat`
   não aparecem nas listas do painel (Caixa, Relevantes). Se a sync ou o backfill
   encontrarem o mesmo id na INBOX depois, a linha vira `source = 'inbox'`.
   Id que o Gmail não conhece (404) → erro "esse e-mail não existe mais no Gmail".
2. **Ação em lote.** `propose_email_actions` substitui `propose_email_action`:
   recebe até 50 ids **ou** uma busca do Gmail, propõe a mesma ação para todos e
   gera **um** cartão. Duplicadas entram no cartão; negadas pela política são
   contadas na resposta da tool.
3. **Continuação após cartão.** Confirmar um cartão cria na conversa uma mensagem
   de evento (`role='user'`, `input='event'`) descrevendo o resultado e enfileira
   um `chat_turn`. O agente termina o pedido pendente ou responde uma frase.
   Cancelar não continua. Se a conversa estiver ocupada, não continua.
4. **Histórico com referências.** Cada resposta guarda os e-mails que citou
   (`chat_message_refs`: até 20 por mensagem, id + assunto + remetente). O
   histórico leva `[e-mails desta resposta: …]` (até 10), então "esses e-mails"
   aponta para o mesmo conjunto.
5. **Prompt do chat v2** (nova versão em `agent_configs`): pedido sobre e-mails
   específicos → `propose_email_actions`; regra só para padrão futuro e nunca como
   substituto; tool falhou → contar o erro real numa frase, sem inventar causa e sem
   mandar o usuário fazer no painel o que uma tool faz; "sim"/"mostre" executa o que
   foi oferecido; copiar endereços exatamente dos resultados; mensagens `[evento]`.
   Erros das tools passam a dizer o que fazer.
6. **`propose_rule` confere o remetente.** Endereço sem nenhum e-mail conhecido e
   sem remetente parecido → erro pedindo o endereço exato (sugere o parecido
   quando houver).
7. **Cadeia de modelos do chat.** `CHAT_MODELS` (padrão
   `gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite`)
   num gateway próprio; a triagem continua em `GEMINI_MODELS`. Em qualquer gateway:
   429 de cota diária marca o modelo como esgotado até a meia-noite do Pacífico
   (05h de Brasília) e a tentativa segue no próximo modelo; 503/429 curto troca de
   modelo sem esperar e só espera (backoff) ao voltar ao mesmo modelo. Todos
   esgotados → "A cota de hoje acabou em todos os modelos do chat; volta às 05h."
   Ao trocar de modelo no meio de um loop de tools, as assinaturas de raciocínio do
   modelo anterior são trocadas pela assinatura neutra do Gemini
   (`skip_thought_signature_validator`).
8. **Dados de 2026-11-19.** Regra #1 (endereço errado) e preferência #2 (escopo
   `decision`) desativadas, não apagadas.

## Fora do escopo

Streaming por token, desfazer lixeira pelo chat, ações em mais de 50 e-mails por
cartão, classificação de e-mails `source='chat'`.
