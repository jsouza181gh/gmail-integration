# Feedback de lotes no chat — design

Data: 2026-11-21. Complementa `2026-11-20-chat-usable-design.md`.

## Problema (teste em produção, 2026-11-21)

"Apagar todo o spam (~201)": o agente disse "Criei um cartão para apagar todos"
sem avisar que eram lotes de 50; ao confirmar, a continuação respondeu na hora
("quer o próximo lote?") enquanto as 50 exclusões ainda estavam na fila, sem
progresso nem resultado; o cartão ficou em `approved` até recarregar a página.
Pareceu um loop que não executa.

## Fluxo

1. **Ao propor**: `propose_email_actions` com `gmail_query` grava no cartão a
   busca, o total estimado (`resultSizeEstimate`) e o número do lote (cartões
   anteriores da mesma conversa com a mesma busca e ação + 1). O cartão mostra
   "Lote 1 de 5 · 50 de ~201 e-mails"; a tool devolve isso e o prompt v3 manda
   o agente avisar a divisão em lotes antes da confirmação.
2. **Ao confirmar com aprovar**: a continuação nasce como bolha em andamento
   (assistente `queued`, `wait_card_id` = cartão) com progresso "apagando…
   12/50"; o `chat_turn` **não** é enfileirado ainda.
3. **Ao assentar**: cada `execute_action` que termina (executada, falhou ou
   morreu) atualiza o progresso; quando nenhum item do cartão está mais
   `approved`, a mensagem `[evento]` recebe o resultado real ("50 executadas,
   0 falharam; lote 1 de 5; restam ~151 na busca 'in:spam'") e o `chat_turn`
   é enfileirado. O agente responde com o resultado e oferece o próximo lote.

## Regras

- Recusar, e cartões de regra/preferência/política: continuação imediata, como hoje.
- Job morto (dead-letter) conta como falha → a bolha nunca fica presa.
- Corrida: a rota de confirmação verifica o cartão logo depois de criar a
  espera; se tudo já assentou, enfileira na hora.
- Verbo do progresso por ação: trash "apagando", archive "arquivando",
  mark_read "marcando como lidos", star "destacando", block_sender "bloqueando".
- Lote sem busca (ids explícitos): sem cabeçalho de lote; o resto igual.
