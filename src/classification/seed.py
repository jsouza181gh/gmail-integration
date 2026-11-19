"""Taxonomy v1, built from real inbox samples (Nov/2026).

FROZEN: migration 0003 inserts exactly this. A new taxonomy is a new
constant plus a new migration (or a set created at runtime); never edit V1.
"""
from src.classification.taxonomy import CategoryDef, FlagDef, PreferenceSeed, TaxonomySpec

V1 = TaxonomySpec(
    categories=(
        CategoryDef(
            name="Pessoas",
            definition="Um humano escrevendo diretamente para o usuário (não automático, não em massa).",
            examples=("resposta direta de alguém conhecido", "e-mail pessoal de amigo ou familiar"),
        ),
        CategoryDef(
            name="Carreira",
            definition="Processo seletivo, recrutador, entrevista ou contato de RH dirigido ao usuário (não alerta automático de vagas).",
            examples=("Convite para participar de processo seletivo (RH de empresa)", "agendamento de entrevista"),
        ),
        CategoryDef(
            name="Vagas",
            definition="Alertas automáticos de vagas de emprego enviados por plataformas.",
            examples=("Alertas de vaga do LinkedIn", "Catho: veja a vaga do dia", "Glassdoor: novas vagas"),
        ),
        CategoryDef(
            name="Estudos",
            definition="Instituições ou cursos em que o usuário está matriculado: prazos, provas, certificados (não marketing de cursos).",
            examples=("42 São Paulo: prazo para marcar a prova da piscina", "Coursera: seu certificado está pronto"),
        ),
        CategoryDef(
            name="Compras",
            definition="Pedidos, entregas e notas fiscais de compras feitas pelo usuário.",
            examples=("Mercado Livre: sua compra está a caminho", "A NF-e da compra já está disponível"),
        ),
        CategoryDef(
            name="Finanças",
            definition="Banco, fatura, cobrança, boleto, imposto e movimentações financeiras do usuário (não ofertas de bancos).",
            examples=("fatura do cartão disponível", "boleto com vencimento", "aviso de cobrança do banco"),
        ),
        CategoryDef(
            name="Contas & Segurança",
            definition="Segurança e acesso a contas: MFA, login, códigos de verificação, mudança de termos ou de conta.",
            examples=("Heroku: registre um método de MFA de backup", "Telegram: seu código de login"),
        ),
        CategoryDef(
            name="Infra/Dev",
            definition="Serviços de infraestrutura e desenvolvimento usados pelo usuário: alertas de servidor, deploy, faturamento técnico.",
            examples=("Render: Server failure detected", "AWS: mudanças nas notas fiscais brasileiras"),
        ),
        CategoryDef(
            name="Conteúdo",
            definition="Newsletters e conteúdo editorial que o usuário assina para ler (não promoção de vendas).",
            examples=("InfoMoney: análise de mercado", "ngrok news"),
        ),
        CategoryDef(
            name="Promoções",
            definition="Marketing puro: ofertas, descontos, campanhas e convites de venda, inclusive de cursos e bancos.",
            examples=("Zoom Friday: até 80% OFF", "Qconcursos: uma condição especial apareceu", "AliExpress: isso é a sua cara"),
        ),
        CategoryDef(
            name="Social",
            definition="Notificações de redes sociais e comunidades (curtidas, impressões, convites de conexão).",
            examples=("LinkedIn: suas publicações receberam impressões", "LinkedIn: adicione fulano à sua rede"),
        ),
    ),
    flags=(
        FlagDef(
            name="is_relevant",
            definition="Exige ação ou tem prazo para o usuário, ou vem de pessoa/instituição com quem ele tem vínculo real; marketing e alertas genéricos nunca são relevantes.",
        ),
        FlagDef(
            name="deadline",
            definition="Data-limite explícita no e-mail, formato YYYY-MM-DD; sem ano, inferir pelo ano da data do e-mail; sem prazo explícito: null.",
        ),
        FlagDef(
            name="has_document",
            definition="Traz documento útil de guardar (NF, fatura, boleto, comprovante, contrato, certificado, documento pessoal), em anexo ou link.",
        ),
        FlagDef(
            name="ephemeral",
            definition="Só é útil por minutos ou horas (código de verificação, OTP, link de login).",
        ),
    ),
)

V1_PREFERENCES = (
    PreferenceSeed(
        scope="classification",
        text=(
            "Vagas relevantes para o usuário: engenharia de software, preferência backend; "
            "Python até nível júnior; Java e outras linguagens só estágio; também IA e fullstack. "
            "Vagas fora disso (ex.: administrativo, vendas, jovem aprendiz) não são relevantes."
        ),
    ),
)
