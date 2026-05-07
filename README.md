# Monitor IBS/CBS — Agente Autônomo de Monitoramento Normativo

Agente Python que monitora automaticamente novas normas relativas ao **IBS**
(Imposto sobre Bens e Serviços) e à **CBS** (Contribuição sobre Bens e Serviços)
da Reforma Tributária brasileira (EC 132/2023 / LC 214/2025).

A cada execução o agente:
1. Vasculha fontes oficiais (DOU, Planalto, CGIBS, Receita Federal)
2. Filtra publicações relacionadas ao IBS/CBS por termos-alvo
3. Analisa cada norma nova com a API Claude (relevância, impacto, resumo)
4. Persiste os registros num banco SQLite local
5. Envia e-mail formatado em HTML listando as novidades

---

## Estrutura do projeto

```
ibs_cbs_agent/
├── .github/
│   └── workflows/
│       └── monitor.yml        ← Agendamento GitHub Actions
├── src/
│   ├── agent.py               ← Orquestrador principal
│   ├── database.py            ← Camada SQLite
│   └── consultar.py           ← CLI de consulta local
├── data/
│   └── ibs_cbs.db             ← Banco de dados (gerado automaticamente)
├── .env.example               ← Modelo de variáveis de ambiente
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Configuração — passo a passo

### 1. Clonar e instalar dependências

```bash
git clone https://github.com/SEU_USUARIO/ibs-cbs-monitor.git
cd ibs-cbs-monitor
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Edite o .env com seus dados reais
```

Preencha no `.env`:

| Variável | Descrição |
|---|---|
| `ANTHROPIC_API_KEY` | Chave da API Anthropic (console.anthropic.com) |
| `SMTP_USER` | Seu e-mail Gmail |
| `SMTP_PASS` | **Senha de App** do Google (não a senha normal) |
| `EMAIL_DESTINO` | E-mail que receberá os alertas |

#### Como criar uma Senha de App no Gmail

> Obrigatório se você usa autenticação de 2 fatores (recomendado).

1. Acesse: https://myaccount.google.com/apppasswords
2. Selecione **"Outro (nome personalizado)"**
3. Digite `Monitor IBS/CBS`
4. Copie a senha gerada (16 caracteres) para `SMTP_PASS` no `.env`

### 3. Testar localmente

```bash
# Executa o agente uma vez
python src/agent.py

# Consulta o banco de dados gerado
python src/consultar.py stats
python src/consultar.py listar
python src/consultar.py listar --alto
python src/consultar.py buscar "split payment"
python src/consultar.py historico
```

---

## Agendamento automático via GitHub Actions (gratuito)

O arquivo `.github/workflows/monitor.yml` configura execução automática:
- **08:00 BRT** (segunda a sexta)
- **18:00 BRT** (segunda a sexta)

### Configurar os Secrets no GitHub

1. Abra seu repositório no GitHub
2. Vá em **Settings → Secrets and variables → Actions**
3. Clique em **New repository secret** para cada um:

| Nome do Secret | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | Sua chave da API Anthropic |
| `SMTP_USER` | Seu e-mail Gmail |
| `SMTP_PASS` | Senha de App do Google |
| `EMAIL_DESTINO` | E-mail destinatário dos alertas |

### Disparar manualmente

Na aba **Actions** do seu repositório → **Monitor IBS/CBS** → **Run workflow**.

### Banco de dados persistente

O banco SQLite é salvo como **cache entre execuções** via `actions/cache`.
Isso garante que o agente nunca reenvie a mesma norma duas vezes.
O banco também é salvo como **artefato** por 30 dias para consulta.

---

## Fontes monitoradas

| Fonte | Tipo | URL |
|---|---|---|
| DOU Federal | API (Querido Diário) | queridodiario.ok.org.br |
| Portal do Planalto | Scraping | planalto.gov.br |
| CGIBS | Scraping | cgibs.gov.br |
| Receita Federal / Fazenda | Scraping | gov.br/fazenda |
| Portal da Reforma Tributária | Scraping | reformatributaria.com |

---

## Score de relevância (Claude)

| Score | Tipo de norma |
|---|---|
| 10 | Lei Complementar ou Decreto regulamentador principal |
| 8–9 | Resolução CGIBS, Ato Conjunto RFB/CGIBS, Portaria com impacto operacional |
| 5–7 | Instrução Normativa, Nota Técnica, orientação administrativa |
| 1–4 | Referência indireta, notícia sem força normativa (filtrada, não enviada) |

Apenas normas com **score ≥ 4** são salvas e notificadas.

---

## Base normativa de referência

| Norma | Data | Descrição |
|---|---|---|
| EC nº 132/2023 | Dez/2023 | Base constitucional da reforma |
| LC nº 214/2025 | Jan/2025 | Institui IBS, CBS e IS — 544 artigos |
| LC nº 227/2026 | 2026 | 2ª etapa da regulamentação |
| Ato Conjunto RFB/CGIBS nº 1/2025 | Dez/2025 | Obrigações acessórias 2026 |
| Decreto nº 12.955/2026 (RCBS) | Abr/2026 | Regulamento CBS — 620 artigos |
| Resolução CGIBS nº 6/2026 (RIBS) | Abr/2026 | Regulamento IBS — 617 artigos |
| Portaria Conjunta MF/CGIBS nº 7/2026 | Abr/2026 | Reconhece disposições comuns |

**Marco crítico: 01/08/2026** — Início da exigibilidade de penalidades por
descumprimento das obrigações acessórias de IBS/CBS (art. 3º, Ato Conjunto
RFB/CGIBS nº 1/2025 + arts. 619 RCBS e 617 RIBS).

---

## Aviso legal

As informações são extraídas de fontes oficiais e analisadas por IA.
Sempre verifique o texto original das normas antes de tomar decisões jurídicas
ou operacionais. Este projeto não substitui assessoria jurídica especializada.
