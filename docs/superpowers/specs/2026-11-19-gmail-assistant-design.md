# Assistente de Gmail (agente + fila + painel + chat) — design geral

Data: 2026-11-19. Status: aprovado pelo João em 2026-11-19.

Legenda: **[decidido]** = dito pelo João. **[minha escolha]** = decisão do redator, o João pode trocar. **[em aberto]** = depende de algo que ainda não temos.

Este é o documento-guarda-chuva. Ele fixa visão, fronteiras e contratos entre partes. Cada subprojeto (seção "Subprojetos") ganha depois seu próprio brainstorm curto + plano detalhado, um de cada vez, na ordem dada.

## Intenção

A inbox do João (45.014 mensagens, ~28/dia) está poluída de marketing e alertas automáticos; e-mails que importam somem. Caso real: "Até 29/11: marque um dia pra fazer o jogo de Lógica" (42 São Paulo, 17/11) — o classificador atual acertou (`Urgente`), mas o João só viu olhando um CSV. Outro caso: documentos pessoais/de trabalho e comprovantes/NF/boletos que ele reusa e leva muito tempo para achar.

Ele **não quer** triar manualmente todo dia (ler irrelevante, excluir, bloquear remetente). Quer: o importante visível num lugar só; o lixo tratado por um agente que aprende com o feedback dele; poder conversar (texto ou áudio) com um agente que conhece os e-mails e age no Gmail.

A classificação existente é só a base. O valor está no **agente** que decide a ação de cada e-mail, na **KB de feedback** que guia o agente, e no **painel** (Vite + React) para ver, aprovar e conversar.

Sucesso: abrir o painel e ver só o que importa, com prazo; a fila de aprovações encolhendo com o tempo porque regras viram automáticas; achar qualquer documento pedindo no chat.

## Metas

- Taxonomia nova de 11 categorias + flags ortogonais (abaixo) **[decidido]**, com definições guardadas em dado versionado, não hardcoded **[decidido para preferências; minha escolha para as definições]**.
- **Um único agente** manipula o Gmail; chamado pelo pipeline do webhook e pelo chat **[decidido]**.
- Tools do agente cobrindo a superfície da Gmail API (messages, threads, labels, drafts, history, attachments, settings/filters) **[decidido]**; o classificador vira uma tool **[decidido]**.
- Toda ação potencialmente destrutiva vira **ação sugerida** numa fila, aprovada/recusada no painel ou no chat **[decidido]**.
- Feedback (texto ou áudio) vira **knowledge base** que guia ações futuras **[decidido]**. Preferências pessoais (ex.: quais vagas interessam) vivem na KB, nunca no prompt **[decidido]**.
- Regras criadas por áudio/texto no chat, com dois modos `suggest` (padrão) | `auto`, promoção explícita **[decidido]**. Esta é a função principal do chat **[decidido]**.
- Worker que consome uma **fila de jobs**; trabalho de qualquer tamanho executa gradualmente **[decidido]**. Modo `free` (respeita limites do Gemini free) | `fast` (o mais rápido possível) **[decidido]**.
- **Ponto único de chamada de LLM** consumido por todo agente/tool **[decidido]**.
- **Backfill** do histórico + indexação de anexos importantes **[decidido]**; anexos **não são copiados**, só indexados e baixados do Gmail sob demanda **[decidido]**.
- Painel Vite + React acessível de qualquer lugar via domínio público, com autenticação forte **[decidido]**.
- Chat RAG que consulta o banco, devolve anexos para download, lista pendências de aprovação, e aceita áudio **[decidido]**.
- Observabilidade forte de segurança e do agente: cada passo do agente rastreável **[decidido]**; alertas por e-mail enviado pelo próprio servidor para o João **[decidido]**.
- Agente personalizável o suficiente para alterar qualquer comportamento **[decidido]**.

## Não-metas

- Várias contas Gmail / vários usuários. Single-user **[minha escolha, confirmado implicitamente]**.
- Copiar anexos para storage próprio **[decidido]**.
- Exclusão permanente de e-mail (`messages.delete`) — "excluir" é sempre lixeira **[minha escolha, ver Segurança]**.
- App mobile nativo; o front é web responsivo **[minha escolha]**.
- Design visual neste documento: sai do `/design` no subprojeto do front **[decidido]**.
- Tudo que surgir depois entra incrementalmente **[decidido]**.

## Anti-metas (falha mesmo "funcionando")

- Perder um e-mail importante porque o agente o julgou irrelevante e agiu sozinho. Por isso: **nada que esconda e-mail ou mude estado de leitura roda sem aprovação** — inclusive marcar como lido **[decidido]**.
- Um e-mail malicioso induzir o agente a mudar configuração, criar forwarding/filtro ou exfiltrar dados (prompt injection).
- Alguém na internet controlar a conta Gmail via painel/API.
- Qualquer pessoa conseguir trancar o João para fora com 3 senhas erradas (por isso bloqueio por IP, global só em ataque) **[decidido]**.
- Backfill atrasar o processamento de e-mail novo ou do chat.
- Estourar a cota do Gemini free no modo `free`.
- Loop: alerta do sistema chega na inbox → processado → gera novo alerta.
- Um anexo importante sumir porque o agente sugeriu lixeira sem avisar.

## Restrições e estado atual

- Repo atual: FastAPI (1 worker), SQLAlchemy 2 + Alembic, Postgres 16, Docker Compose (`db`, `migrate`, `api`), pytest. Ver `README.md` e `docs/superpowers/specs/2026-11-16-gmail-webhook-design.md`.
- Docker local: usar o do WSL (`-u root`), não o Docker Desktop.
- Tipagem: todo contrato entre módulos é schema Pydantic; `typing.Any` proibido (imposto por `mypy --strict` com `disallow_any_explicit`); nenhuma coluna `JSON`/`JSONB` no banco — listas e mapas viram tabelas filhas **[decidido]**.
- LLM: Gemini (`GOOGLE_API_KEY`), via `langchain-google-genai`, sempre atrás do gateway **[decidido LangChain; minha escolha: Gemini para tudo, inclusive áudio e embeddings]**.
- OAuth Gmail atual: só `gmail.modify`. Ver "Escopo OAuth".
- Deploy: VPS com nginx no host já com HTTPS; o João executa os passos na VPS. VPS: 4 vCPU (fracas), 24 GB RAM (~19 GB disponíveis), 133 GB de disco livre, sem swap, quase nada rodando — cabe Postgres + pgvector + API + worker + extração de PDF. CPU é o gargalo: OCR fica no Gemini, não local; worker com concorrência baixa (*minha escolha*: 2 jobs simultâneos).
- Privacidade: tudo pode ir para o Gemini free (corpos, áudios, documentos pessoais via OCR); prioridade é qualidade. Repensar se virar produto **[decidido]**.
- Tabela `emails` hoje: só `snippet`, `date` como texto cru. Precisará de corpo em texto, data como `timestamptz`, flags, embeddings.
- `src/agent/tools.py` existe (LangChain `@tool`), sem uso no fluxo atual: `list_emails`, `list_labels`, `get_email`, `mark_as_read`, `archive_email`, `trash_email`, `send_email`.

## Taxonomia

Base: análise de 95 e-mails reais (`exports/2026-11/*.csv`, 16–19/11). Hoje: Newsletter/Promoções 58%, Notificações 35% (gaveta de tudo: ~20 alertas de vaga misturados com um convite real de processo seletivo, MFA, entregas, certificados, fiscal), Urgente mistura prazo real, incidente de infra e OTP.

Categorias (mutuamente exclusivas) **[decidido]**:

| Categoria | Definição |
|---|---|
| Pessoas | humano escrevendo diretamente para o João |
| Carreira | processo seletivo, recrutador, entrevista (não automático) |
| Vagas | alertas automáticos de vagas (LinkedIn, Catho, Glassdoor, Indeed…) |
| Estudos | instituições/cursos em que está matriculado (ex.: 42 SP), certificados |
| Compras | pedido, entrega, NF de compra |
| Finanças | banco, fatura, cobrança, imposto |
| Contas & Segurança | MFA, login, OTP, mudança de termos |
| Infra/Dev | Render, Heroku, AWS, alertas de servidor |
| Conteúdo | newsletter assinada para ler |
| Promoções | marketing puro |
| Social | LinkedIn e redes |

Flags ortogonais: `relevante` (exige ação/prazo dele, ou vem de vínculo real), `prazo` (data extraída), `tem_documento`, `efêmero` (OTP etc.) **[minha escolha, aceito]**.

Preferência seed da KB (não no prompt) **[decidido]**: vagas relevantes = engenharia de software, preferência backend; Python até júnior; Java e outras linguagens só estágio; IA; fullstack também.

## Arquitetura

```
Gmail ──push──▶ Pub/Sub ──▶ API /webhooks/gmail ──enfileira──▶ jobs (Postgres)
                                                                   │
Front (Vite/React) ──cookies──▶ API (auth, leitura, enfileira) ────┤
                                                                   ▼
                                                     worker (agente + tools)
                                                         │        │
                                                 LLM gateway   Gmail API
                                                         │
                                                      Gemini
```

- **API** (FastAPI, container `api`): auth, endpoints de leitura, enfileira jobs, serve download de anexo, SSE/stream do chat **[minha escolha]**. Não chama LLM nem Gmail de escrita diretamente.
- **Worker** (container novo `worker`): consome jobs, roda o agente **[decidido]**.
- **Fila** no Postgres (`SELECT … FOR UPDATE SKIP LOCKED`, sem Redis) **[minha escolha]**. Prioridade: chat > e-mail novo > ação aprovada > backfill **[minha escolha]**. Retry com backoff, dead-letter, chave de idempotência (ex.: `email_id + job_type`).
- **Tipos de job** iniciais **[minha escolha]**: `process_new_email`, `chat_turn`, `execute_action`, `backfill_page`, `extract_attachment`, `reclassify`.
- **LLM gateway**: único módulo para toda chamada de LLM, consumido por todo agente e tool **[decidido]**. Referência: `c:\Users\Geeco\Desktop\Projetos\Pessoal\Agents\services\llm\service.py` (LiteLLM `completion`, entrada `Prompt(messages, tools)`, saída `AssistantMessage` com `tool_calls`, espera mínima entre chamadas no free tier, log JSON por chamada com id, modelo, finish reasons, contagem de tool calls e uso de tokens detalhado por modalidade). O que importa é a ideia: centralizar e cada chamada gerar log com as informações importantes **[decidido]**. Implementação com LangChain (`langchain-google-genai`), **não** LiteLLM — só o desenho segue a referência **[decidido]**. Acréscimos em relação à referência **[minha escolha]**: cada chamada também grava uma linha no DB ligada ao `agent_run`/`agent_step` (prompt, resposta, tokens, custo, latência, erro); rate limit do modo `free` por RPM **e** RPD/TPM com contadores persistidos (sobrevivem a restart); fallback de modelo; suporte a áudio, PDF/imagem e embeddings pela mesma porta.
- **Agente**: loop próprio sobre o gateway (mensagens + tools LangChain via `bind_tools` → `tool_calls` → executa → repete), no estilo da referência, **sem LangGraph** **[minha escolha]** — LangGraph chamaria o modelo por fora do gateway. A "interrupção para aprovação" não precisa de checkpoint: a tool sensível só grava em `suggested_actions` e encerra; a execução real é um job `execute_action` depois da aprovação. `src/agent/tools.py` continua com `@tool` do LangChain.
- **Modos do worker**: `free` respeita RPM/RPD/TPM do tier gratuito; `fast` só reage a 429. Limites em config (mudam com frequência), alteráveis em runtime **[decidido modos; minha escolha config]**.
- **Vetores** (RAG + KB + anexos): pgvector no Postgres existente, embeddings do Gemini **[minha escolha]**.
- **Áudio**: enviado ao Gemini nativo (transcreve + entende em uma chamada); áudio original guardado junto do feedback/turno **[minha escolha]**.

## Política de ações

| Classe | Ações | Comportamento |
|---|---|---|
| Automática | aplicar labels do próprio agente (ex.: `IA/Promoções`, `IA/Relevante`) | executa direto |
| Sugerida | marcar lido, estrela, arquivar, lixeira, bloquear remetente (filtro), enviar/responder, criar/alterar filtros e settings, mudanças de config do agente | vai para `suggested_actions` |
| Proibida | `messages.delete` (permanente) | não existe como tool |

**[decidido]** para o que é sugerido; estrela como sugerida e a lista automática **[minha escolha, aceita]**.

- Aprovação individual ou **em lote** (por regra/remetente/categoria) **[decidido]**; também pelo chat ("o que tenho para aprovar?") **[decidido]**. Aba e chat usam a mesma fila.
- Recusa/aprovação/comentário (texto ou áudio) geram `feedback` → KB **[decidido]**.
- Sugestão de lixeira para e-mail com anexo indexado como importante exige aviso explícito no card **[minha escolha]**.
- Enviar/responder e settings de forwarding/filters **nunca** podem ficar em `auto`, nem por regra **[minha escolha]**. Lixeira e arquivar podem.

## Regras e KB

Fluxo principal do chat **[decidido]**: João manda áudio/texto com uma regra → Gemini transcreve/interpreta → agente monta regra estruturada → card "Entendi assim: … Afeta ~N e-mails existentes. Confirmar / Ajustar / Cancelar" → confirmada, vale do próximo e-mail em diante.

Dois tipos de regra, decididos pelo agente e mostrados no card **[minha escolha]**:
- **Preferência** (soft): texto + embedding na KB; recuperada por similaridade e ponderada pelo agente.
- **Automação** (hard): condição determinística (remetente, domínio, categoria, assunto…) → ação, sem LLM. Campo `mode`: `suggest` (padrão) | `auto` **[decidido]**.

- Promoção `suggest → auto` pelo chat ou pela aba Agente, com card de confirmação mostrando o histórico (disparos, aprovadas, recusadas) **[decidido promoção; minha escolha histórico]**.
- Rebaixamento automático `auto → suggest` + alerta se o João desfizer uma ação executada pela regra (ex.: tirar da lixeira) **[minha escolha]**.
- Opcional na criação: aplicar a regra também aos e-mails existentes (gera sugestões em lote) **[minha escolha]**.

## Personalização e rastreio do agente

Todo comportamento é dado versionado **[decidido]**. Entidades **[minha escolha]**:

| Entidade | Conteúdo |
|---|---|
| `agent_profiles` + `agent_config_versions` | modelo, temperatura, teto de custo diário, prompts; versão imutável por mudança, diff, rollback |
| `categories` (versionado) | nome, definição, exemplos, cor, ordem; o prompt do classificador é montado daqui |
| `tool_policies` | por tool e por contexto (webhook × chat): `auto` / `suggest` / `deny` |
| `kb_entries` | preferências/feedbacks: texto, embedding, origem (UI/chat/áudio/aprovação), ativo, link ao feedback de origem |
| `automation_rules` | condição, ação, `mode`, contadores, origem, versão |
| `agent_runs` | gatilho (webhook/chat/backfill), versão de config usada, status, tokens, custo, duração |
| `agent_steps` | por passo, em ordem: prompt, KB recuperada, resposta do LLM, tool + args + resultado, erro |
| `suggested_actions` | ação, alvo, motivo, status (pendente/aprovada/recusada/executada/falhou), lote, run e regra de origem |
| `feedback` | texto ou transcrição + áudio, ligado a ação/e-mail, regra/KB gerada |
| `jobs` | tipo, payload, prioridade, status, tentativas, chave de idempotência, erro |
| `notifications` | alertas enviados: tipo, payload, agregação |
| `config_audit` | o que mudou, quando, por qual canal (UI/CLI/chat), antes → depois |

Requisito: responder "por que ele sugeriu isso?" em 2 cliques (sugestão → run → steps) **[minha escolha]**.

## Segurança

### Autenticação **[decidido salvo indicação]**
- Usuário único criado via CLI na VPS (`src.cli.create_user`): senha (argon2) + segredo TOTP exibido como QR + 10 recovery codes de uso único **[minha escolha: CLI e recovery codes]**.
- Login: senha → TOTP (Google Authenticator ou similar) **[decidido]**.
- Access JWT curto (~10 min **[minha escolha]**) em cookie `HttpOnly; Secure; SameSite=Strict`.
- Refresh opaco (~14 dias **[minha escolha]**), guardado como hash; rotação a cada uso; reuso de refresh antigo → revoga a família inteira + evento de segurança + alerta; revogação no logout; tela de sessões ativas com revogar **[minha escolha: tela]**.
- **Bloqueio por IP**: 3 falhas (senha ou TOTP) → IP bloqueado até desbloqueio manual via CLI na VPS **[decidido]**. IP real de `X-Forwarded-For` só se a conexão vier do nginx local.
- **Bloqueio global** só em ataque: falhas de ≥3 IPs distintos em 24h **[minha escolha do limiar]** → login fechado até desbloqueio na VPS **[decidido]**.
- CLI: `src.cli.unlock --ip X` / `--global`.

### Superfície **[minha escolha, em resposta a "só aceitar requisições do frontend"]**
"Só do front" não é garantível (headers são forjáveis fora do navegador). Implementado como: auth obrigatória em toda rota exceto login, webhook e `/healthz`; CORS travado no domínio do front; CSRF double-submit + checagem de `Origin` em rotas que mudam estado; HSTS, CSP, X-Frame-Options, nosniff; rate limit geral por rota (~60 req/min); `/docs` e `/openapi.json` desligados em produção.

- Webhook: verificar o JWT OIDC assinado pelo Pub/Sub no lugar de `?token=` **[minha escolha]**.
- Download de anexo: autenticado, URL de curta duração, registrado em `security_events` **[minha escolha]**.

### Prompt injection **[minha escolha]**
- Conteúdo de e-mail entra no modelo marcado como dado, nunca como instrução.
- No pipeline do webhook o agente nunca altera config, regras ou políticas.
- No chat, mudanças de config/regra/política são ações sugeridas com card de confirmação.

### Escopo OAuth **[minha escolha]**
Adicionar `gmail.settings.basic` (filtros para bloquear remetente). **Não** pedir `https://mail.google.com/` (permite exclusão permanente). Exige reautorizar (`python -m src.gmail.authorize`) e recopiar `token.json` para a VPS. `gmail.settings.sharing` (forwarding/delegates) fica fora.

## Observabilidade e alertas

- `security_events`: login ok/falha, bloqueios, reuso de refresh, CSRF inválido, download de anexo **[minha escolha]**.
- Trilha completa do agente em `agent_runs`/`agent_steps` **[decidido]**.
- **Alertas por e-mail** enviados pelo próprio servidor, via Gmail API, para o próprio endereço, assunto `[gmail-integration] …` **[decidido]**. Envio de sistema é código fixo, não passa pelo agente nem pela fila de aprovação **[minha escolha]**.
- Anti-loop: header próprio + label `Sistema/Alerta`; o sync ignora esses e-mails **[minha escolha]**.
- Gatilhos **[minha escolha]**: bloqueio de IP, bloqueio global, reuso de refresh, login de IP novo, falha repetida de sync/watch, erros em série do agente, custo acima do teto diário, regra rebaixada.
- Agregação: no máximo 1 alerta por tipo a cada 15 min **[minha escolha]**.

## Backfill e anexos

- Dados da conta: 45.014 mensagens; 10.075 no último ano; 17.444 em 2 anos; 2.226 com anexo.
- Backfill como jobs `backfill_page`, retomável, prioridade mínima, do mais novo para o mais antigo, cobrindo todo o histórico **[decidido jobs graduais; minha escolha ordem]**. Sem prazo: no modo `free` pode levar semanas e isso é aceitável ("não importa o tamanho do trabalho, executa gradualmente") **[decidido]**. Regras de automação (sem LLM) rodam antes do LLM para baratear **[minha escolha]**.
- Backfill **classifica e indexa**, mas **não gera sugestões de ação** para e-mails antigos por padrão (evita inundar a fila); limpeza do passado vem por regras aplicadas explicitamente **[minha escolha]**.
- Anexos: classificar em `pessoal` (RG, CPF, CNH, comprovante de residência, certidões), `trabalho` (contratos, holerites, admissão), `fiscal` (NF-e, fatura, boleto, comprovante), `outro` (ignorado: imagens inline pequenas, banners) **[decidido foco; minha escolha classes]**.
- Extração: texto do PDF; OCR via Gemini para imagem/escaneado; metadados por tipo (NF: emitente, valor, data; boleto: vencimento, valor, linha digitável) **[minha escolha]**.
- Guardado: metadados + texto extraído + embedding + `messageId`/`attachmentId`. Download sob demanda via `attachments.get` **[decidido]**.
- O sync passa a guardar o corpo dos e-mails em texto (HTML → texto) + embeddings, necessário para o RAG **[minha escolha]**.

## Painel (inventário funcional)

Visual via `/design` **[decidido]**. Responsivo desde o início **[minha escolha]**. Mesma origem: front em `https://<dominio>/`, API em `https://<dominio>/api`, webhook em `/webhooks/gmail` — cookies sem cross-site, CORS praticamente fechado **[minha escolha]**. Vite + React + TypeScript **[decidido Vite/React; minha escolha TS]**, servido pelo nginx da VPS.

| Aba | Conteúdo |
|---|---|
| Login | senha → TOTP → (recovery code) |
| Relevantes (home) | flag `relevante`, ordenado por prazo, destaque do que vence logo |
| Caixa por categoria | lista filtrável (categorias + flags), busca, detalhe com categoria, motivo, run de origem, ações, "reclassificar" (vira feedback) |
| Aprovações | fila agrupada por regra/remetente; aprovar/recusar individual ou em lote; feedback texto/áudio; "discutir no chat" |
| Chat | texto + áudio; cards de confirmação (ação, regra, promoção para `auto`); links de anexo; pendências de aprovação |
| Documentos | anexos indexados por classe, metadados, download |
| Agente | prompts, categorias, políticas de tool, regras, KB, modo `free`/`fast`; histórico e rollback |
| Execuções | runs → timeline de steps; fila de jobs (pendentes, falhas, dead-letter, progresso do backfill); custo/tokens por dia |
| Segurança | eventos, sessões ativas (revogar), IPs bloqueados (somente leitura), alertas enviados |

## Subprojetos e ordem

Ordem **[decidido]**, cada um com brainstorm curto + plano próprio:

1. **Taxonomia + classificador novo** — categorias/flags em tabela versionada, prompt montado a partir dela, KB seed de preferências, reclassificar só os e-mails já presentes no Postgres via CLI pontual (o histórico completo é do subprojeto 3) **[minha escolha]**.
2. **Núcleo do agente** — fila + worker (+ modos), LLM gateway (o classificador do subprojeto 1 já nasce sobre ele: o gateway mínimo entra no início do subprojeto 1 **[minha escolha]**), tools da Gmail API, classificador como tool, política de ações, `suggested_actions`, regras, KB, feedback, trilha `agent_runs`/`agent_steps`, alertas por e-mail. Webhook passa a enfileirar `process_new_email`.
3. **Backfill + anexos** — jobs de backfill, corpo + embeddings, indexação de anexos, download sob demanda.
4. **API autenticada + painel** — auth/segurança completa, endpoints de leitura/ação/config, front Vite/React (design via `/design`). Nada é exposto publicamente antes da auth deste subprojeto estar pronta.
5. **Chat RAG + áudio** — turnos de chat via fila, RAG sobre e-mails/anexos/KB, regras por áudio, cards de confirmação, streaming.

Até o subprojeto 4, aprovações e configuração podem ser feitas via CLI para validar o agente **[minha escolha]**.

## Deixado ao builder

- Esquema exato das tabelas, índices e migrações Alembic.
- Layout de módulos em `src/` (seguindo o estilo atual).
- Biblioteca de rate limit, de JWT e de TOTP; parser HTML → texto; extração de PDF (leve em CPU).
- Formato do streaming do chat (SSE ou WebSocket).
- Valores numéricos marcados como **[minha escolha]** podem ser ajustados com justificativa.
