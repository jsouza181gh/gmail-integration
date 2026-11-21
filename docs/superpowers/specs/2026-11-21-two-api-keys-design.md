# Duas chaves de API (app × backfill) — design

Data: 2026-11-21. Complementa `2026-11-19-gmail-assistant-design.md`.

## Problema

Uma chave do Gemini atende tudo: o backfill do histórico gasta a cota diária
(1000 embeddings) e o chat e a triagem de e-mail novo ficam sem. Além disso o
worker tem uma faixa para o chat e outra para todo o resto: um `backfill_page`
(25 e-mails, classificação, vetores, esperas de retry) atrasa a triagem de um
e-mail que acabou de chegar.

## Regra

**App e mensagens novas primeiro; backfill em segundo plano.**

## Decisões

1. **Duas chaves, projetos diferentes** (cada uma com a própria cota):
   `GOOGLE_API_KEY` = app (chat, triagem, indexação e anexos de e-mail novo,
   busca do chat); `GOOGLE_API_KEY_BACKFILL` = backfill (páginas do histórico,
   classificação e vetores delas, anexos do histórico). Sem a segunda chave,
   tudo usa a primeira (comportamento atual).
2. **Um gateway por chave**, cada um com a própria memória de cota diária,
   pausa por modelo, modelo preferido e ritmo.
3. **Anexo do histórico é outro tipo de job**: `backfill_attachment` (mesmo
   payload de `index_attachment`). O indexador enfileira `backfill_attachment`
   quando indexa para o backfill (`run_id` presente) e `index_attachment` para
   e-mail novo.
4. **Três faixas no worker**: chat (`chat_turn`); app (`process_email`,
   `execute_action`, `index_email`, `index_attachment`); backfill
   (`backfill_page`, `backfill_attachment`). A faixa do backfill não segura a
   do app.
5. **Turbo** (`app_settings.backfill_turbo`, padrão desligado), botão no cartão
   de Execuções junto de Pausar/Retomar. Ligado, o backfill alterna as chamadas
   entre as duas chaves, mas **cede a chave do app**: não a usa enquanto o
   gateway do app tiver qualquer modelo pausado (503/timeout) ou sem cota do
   dia. O backfill usa o mesmo objeto gateway do app (memória compartilhada),
   então nunca esgota o que o chat precisa sem saber.
6. Turbo com uma chave só (sem `GOOGLE_API_KEY_BACKFILL`) não faz nada; o
   painel mostra o botão desabilitado com o motivo.

## Fora do escopo

Contar a cota restante de cada chave (o Gemini não expõe); mais de duas chaves.
