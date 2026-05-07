"""
Agente Autônomo de Monitoramento Normativo — IBS/CBS
=====================================================
Busca novas normas relativas ao IBS e CBS, armazena em banco SQLite
e notifica por e-mail via Gmail SMTP quando há novidades.

Fontes monitoradas:
  - Diário Oficial da União (DOU) via API do Querido Diário
  - Portal do Planalto (planalto.gov.br)
  - Portal CGIBS (cgibs.gov.br)
  - Receita Federal (gov.br/fazenda)
  - Portal da Reforma Tributária (reformatributaria.com)

Dependências (ver requirements.txt):
  anthropic, requests, beautifulsoup4, python-dotenv
"""

import os
import json
import hashlib
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic
from dotenv import load_dotenv

from database import Database

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração central
# ---------------------------------------------------------------------------
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS       = 2048
REQUEST_TIMEOUT  = 20  # segundos

FONTES = [
    {
        "nome": "Planalto — Leis Complementares",
        "url": "https://www.planalto.gov.br/ccivil_03/leis/lcp/",
        "seletor": "a[href*='lcp']",
        "tipo": "planalto",
    },
    {
        "nome": "CGIBS — Comitê Gestor do IBS",
        "url": "https://www.cgibs.gov.br/legislacao",
        "seletor": "a",
        "tipo": "cgibs",
    },
    {
        "nome": "Portal da Reforma Tributária",
        "url": "https://www.reformatributaria.com/legislacao/",
        "seletor": "article a, h2 a, h3 a",
        "tipo": "portal_rt",
    },
    {
        "nome": "Receita Federal — Reforma Tributária",
        "url": "https://www.gov.br/fazenda/pt-br/assuntos/reforma-tributaria",
        "seletor": "a",
        "tipo": "rfb",
    },
    {
        "nome": "DOU — API Querido Diário (IBS)",
        "url": (
            "https://queridodiario.ok.org.br/api/gazettes"
            "?querystring=IBS+CBS&level=federal&size=10&sort_by=date"
        ),
        "seletor": None,
        "tipo": "api_dou",
    },
]

TERMOS_ALVO = [
    "IBS", "CBS", "imposto sobre bens e serviços",
    "contribuição sobre bens e serviços", "reforma tributária",
    "LC 214", "LC 227", "CGIBS", "split payment", "imposto seletivo",
    "RCBS", "RIBS", "Decreto 12.955", "Resolução CGIBS",
]


# ---------------------------------------------------------------------------
# Busca de links nas fontes HTML
# ---------------------------------------------------------------------------

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def buscar_links_html(fonte: dict) -> list[dict]:
    """Faz scraping de uma fonte HTML e extrai links relevantes."""
    links = []
    try:
        r = requests.get(fonte["url"], timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "MonitorIBS-CBS/1.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        elementos = soup.select(fonte["seletor"])
        for el in elementos:
            href = el.get("href", "")
            texto = el.get_text(strip=True)
            if not href or len(texto) < 10:
                continue
            # Filtra apenas links relacionados aos termos-alvo
            texto_lower = (texto + " " + href).lower()
            if any(t.lower() in texto_lower for t in TERMOS_ALVO):
                url_completa = href if href.startswith("http") else (
                    fonte["url"].rstrip("/") + "/" + href.lstrip("/")
                )
                links.append({
                    "titulo": texto[:300],
                    "url": url_completa,
                    "fonte": fonte["nome"],
                    "hash": _hash(url_completa),
                })
    except Exception as exc:
        log.warning("Erro ao buscar %s: %s", fonte["url"], exc)
    return links


def buscar_api_dou(fonte: dict) -> list[dict]:
    """Busca normas via API do Querido Diário (DOU federal)."""
    links = []
    try:
        r = requests.get(fonte["url"], timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        dados = r.json()
        for g in dados.get("gazettes", []):
            for excerpt in g.get("excerpts", []):
                if any(t.lower() in excerpt.lower() for t in TERMOS_ALVO):
                    links.append({
                        "titulo": excerpt[:300],
                        "url": g.get("url", fonte["url"]),
                        "fonte": "DOU Federal (Querido Diário)",
                        "hash": _hash(g.get("url", "") + excerpt[:100]),
                    })
    except Exception as exc:
        log.warning("Erro na API DOU: %s", exc)
    return links


# ---------------------------------------------------------------------------
# Análise com Claude
# ---------------------------------------------------------------------------

def analisar_norma(cliente: Anthropic, titulo: str, url: str, fonte: str) -> dict:
    """
    Usa a API Claude para analisar e resumir uma norma.
    Retorna dict com tipo, ementa, pontos_principais, impacto e relevancia.
    """
    prompt = f"""Você é um especialista em direito tributário brasileiro com foco na Reforma Tributária do Consumo (EC 132/2023, LC 214/2025).

Analise a seguinte norma e retorne um JSON com a estrutura abaixo.
Caso não seja possível identificar com certeza algum campo, use null.

Norma:
- Título: {titulo}
- URL: {url}
- Fonte: {fonte}

Estrutura JSON esperada (retorne APENAS o JSON, sem markdown):
{{
  "tipo": "lei_complementar|decreto|resolucao|ato_conjunto|portaria|instrucao_normativa|nota_tecnica|outro",
  "numero": "número/ano da norma ou null",
  "data_publicacao": "DD/MM/AAAA ou null",
  "ementa": "resumo de 1 a 2 frases do que a norma trata",
  "pontos_principais": ["ponto 1", "ponto 2", "ponto 3"],
  "tributos_afetados": ["IBS", "CBS", "IS"],
  "impacto": "alto|medio|baixo",
  "urgencia": "imediata|curto_prazo|medio_prazo",
  "relevancia_score": 1,
  "observacoes": "observação adicional ou null"
}}

Critérios de relevância (1-10):
- 10: Lei Complementar ou Decreto regulamentador principal
- 8-9: Resolução CGIBS, Ato Conjunto RFB/CGIBS, Portaria com impacto operacional
- 5-7: Instrução Normativa, Nota Técnica, orientação administrativa
- 1-4: Notícia ou referência indireta sem força normativa
"""
    try:
        resp = cliente.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Remove blocos markdown se Claude incluir por engano
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as exc:
        log.warning("Erro na análise Claude para '%s': %s", titulo[:60], exc)
        return {
            "tipo": "outro",
            "numero": None,
            "data_publicacao": None,
            "ementa": titulo[:200],
            "pontos_principais": [],
            "tributos_afetados": [],
            "impacto": "baixo",
            "urgencia": "medio_prazo",
            "relevancia_score": 3,
            "observacoes": f"Análise automática falhou: {exc}",
        }


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------

EMAIL_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alerta Normativo IBS/CBS</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
        background:#f5f5f5;margin:0;padding:20px;color:#1a1a1a}}
  .container{{max-width:680px;margin:0 auto;background:#fff;
              border-radius:12px;overflow:hidden;
              box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  .header{{background:#0F6E56;padding:28px 32px;color:#fff}}
  .header h1{{margin:0;font-size:20px;font-weight:600}}
  .header p{{margin:6px 0 0;font-size:13px;opacity:.85}}
  .body{{padding:28px 32px}}
  .summary-box{{background:#E1F5EE;border-left:4px solid #1D9E75;
                border-radius:0 8px 8px 0;padding:16px;margin-bottom:24px}}
  .summary-box p{{margin:0;font-size:14px;line-height:1.6}}
  .norma-card{{border:1px solid #e5e7eb;border-radius:10px;
               padding:20px;margin-bottom:16px}}
  .norma-card:last-child{{margin-bottom:0}}
  .norma-header{{display:flex;align-items:flex-start;
                 justify-content:space-between;gap:12px;margin-bottom:12px}}
  .norma-titulo{{font-size:14px;font-weight:600;line-height:1.4;
                 color:#111;flex:1}}
  .badge{{display:inline-block;padding:3px 10px;border-radius:20px;
          font-size:11px;font-weight:600;white-space:nowrap}}
  .badge-alto{{background:#FCEBEB;color:#A32D2D}}
  .badge-medio{{background:#FAEEDA;color:#854F0B}}
  .badge-baixo{{background:#E1F5EE;color:#0F6E56}}
  .norma-ementa{{font-size:13px;color:#444;line-height:1.6;margin-bottom:12px}}
  .pontos{{margin:0;padding-left:18px}}
  .pontos li{{font-size:12px;color:#555;line-height:1.7}}
  .meta{{display:flex;gap:16px;margin-top:12px;flex-wrap:wrap}}
  .meta span{{font-size:11px;color:#777}}
  .meta strong{{color:#444}}
  .btn{{display:inline-block;padding:8px 18px;background:#0F6E56;
        color:#fff;text-decoration:none;border-radius:8px;
        font-size:13px;font-weight:500;margin-top:12px}}
  .footer{{background:#f9fafb;padding:20px 32px;border-top:1px solid #e5e7eb;
           font-size:11px;color:#888;line-height:1.6}}
  .chip{{display:inline-block;background:#f3f4f6;padding:2px 8px;
         border-radius:12px;font-size:11px;color:#555;margin:2px}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📋 Novas Normas IBS/CBS Detectadas</h1>
    <p>Monitor Automático de Reforma Tributária • {data_execucao}</p>
  </div>
  <div class="body">
    <div class="summary-box">
      <p><strong>{total_normas} nova(s) norma(s)</strong> relativa(s) ao IBS/CBS foram identificadas
      desde a última verificação. Confira o resumo abaixo.</p>
    </div>
    {cards_normas}
  </div>
  <div class="footer">
    <p>Este alerta foi gerado automaticamente pelo <strong>Monitor IBS/CBS</strong>.
    As informações são extraídas de fontes oficiais (DOU, Planalto, CGIBS, RFB)
    e analisadas por IA. Verifique sempre o texto oficial antes de tomar decisões.</p>
    <p>Fontes monitoradas: DOU Federal • planalto.gov.br • cgibs.gov.br •
    gov.br/fazenda • reformatributaria.com</p>
  </div>
</div>
</body>
</html>
"""

CARD_TEMPLATE = """
<div class="norma-card">
  <div class="norma-header">
    <div class="norma-titulo">{titulo}</div>
    <span class="badge badge-{impacto}">{impacto_label}</span>
  </div>
  <div class="norma-ementa">{ementa}</div>
  {pontos_html}
  <div class="meta">
    <span>📅 <strong>Publicação:</strong> {data_pub}</span>
    <span>🏷️ <strong>Tipo:</strong> {tipo}</span>
    <span>📍 <strong>Fonte:</strong> {fonte}</span>
  </div>
  {tributos_html}
  {link_html}
</div>
"""


def _build_card(norma: dict) -> str:
    analise = norma.get("analise", {}) or {}
    pontos = analise.get("pontos_principais", []) or []
    pontos_html = ""
    if pontos:
        items = "".join(f"<li>{p}</li>" for p in pontos[:5])
        pontos_html = f'<ul class="pontos">{items}</ul>'

    tributos = analise.get("tributos_afetados", []) or []
    tributos_html = ""
    if tributos:
        chips = "".join(f'<span class="chip">{t}</span>' for t in tributos)
        tributos_html = f'<div style="margin-top:8px">{chips}</div>'

    url = norma.get("url", "")
    link_html = (f'<a href="{url}" class="btn">Acessar norma →</a>'
                 if url else "")

    impacto = analise.get("impacto", "baixo")
    impacto_labels = {"alto": "Impacto Alto", "medio": "Impacto Médio",
                      "baixo": "Impacto Baixo"}

    return CARD_TEMPLATE.format(
        titulo=norma.get("titulo", "Sem título")[:200],
        impacto=impacto,
        impacto_label=impacto_labels.get(impacto, "Impacto Baixo"),
        ementa=analise.get("ementa", norma.get("titulo", ""))[:400],
        pontos_html=pontos_html,
        data_pub=analise.get("data_publicacao") or "—",
        tipo=(analise.get("tipo") or "outro").replace("_", " ").title(),
        fonte=norma.get("fonte", "—")[:60],
        tributos_html=tributos_html,
        link_html=link_html,
    )


def enviar_email(normas_novas: list[dict]) -> bool:
    """Monta e envia o e-mail de alerta com as normas novas."""
    smtp_host   = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port   = int(os.getenv("SMTP_PORT", "587"))
    smtp_user   = os.getenv("SMTP_USER", "")
    smtp_pass   = os.getenv("SMTP_PASS", "")
    email_dest  = os.getenv("EMAIL_DESTINO", smtp_user)
    email_from  = os.getenv("EMAIL_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        log.error("Credenciais SMTP não configuradas. Defina SMTP_USER e SMTP_PASS.")
        return False

    cards = "".join(_build_card(n) for n in normas_novas)
    html_body = EMAIL_HTML_TEMPLATE.format(
        data_execucao=datetime.now().strftime("%d/%m/%Y às %H:%M"),
        total_normas=len(normas_novas),
        cards_normas=cards,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"[IBS/CBS Monitor] {len(normas_novas)} nova(s) norma(s) detectada(s) "
        f"— {datetime.now().strftime('%d/%m/%Y')}"
    )
    msg["From"]    = email_from
    msg["To"]      = email_dest
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(email_from, [email_dest], msg.as_string())
        log.info("E-mail enviado para %s.", email_dest)
        return True
    except Exception as exc:
        log.error("Falha ao enviar e-mail: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------

def executar() -> None:
    log.info("=== Iniciando ciclo de monitoramento IBS/CBS ===")
    db      = Database()
    cliente = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # 1. Coleta de candidatos em todas as fontes
    candidatos: list[dict] = []
    for fonte in FONTES:
        log.info("Buscando em: %s", fonte["nome"])
        if fonte["tipo"] == "api_dou":
            links = buscar_api_dou(fonte)
        else:
            links = buscar_links_html(fonte)
        log.info("  → %d link(s) relevante(s) encontrado(s)", len(links))
        candidatos.extend(links)

    # 2. Remove duplicatas por hash e filtra já conhecidos
    vistos: set[str] = set()
    novos: list[dict] = []
    for c in candidatos:
        if c["hash"] in vistos:
            continue
        vistos.add(c["hash"])
        if not db.norma_existe(c["hash"]):
            novos.append(c)

    log.info("Total de candidatos: %d | Novos (não indexados): %d",
             len(candidatos), len(novos))

    if not novos:
        log.info("Nenhuma norma nova detectada. Encerrando.")
        db.registrar_execucao(normas_encontradas=0, email_enviado=False)
        return

    # 3. Análise de cada nova norma com Claude
    normas_para_salvar: list[dict] = []
    for item in novos:
        log.info("Analisando: %s", item["titulo"][:80])
        analise = analisar_norma(cliente, item["titulo"], item["url"], item["fonte"])
        score   = analise.get("relevancia_score", 1)
        log.info("  → Score de relevância: %s/10 | Impacto: %s",
                 score, analise.get("impacto"))
        if score >= 4:  # Filtra ruído — apenas normas com score >= 4
            normas_para_salvar.append({**item, "analise": analise})

    log.info("Normas relevantes (score ≥ 4): %d", len(normas_para_salvar))

    if not normas_para_salvar:
        log.info("Candidatos encontrados, mas nenhum com relevância suficiente.")
        db.registrar_execucao(normas_encontradas=0, email_enviado=False)
        return

    # 4. Persiste no banco de dados
    for n in normas_para_salvar:
        db.inserir_norma(
            hash_id      = n["hash"],
            titulo       = n["titulo"],
            url          = n["url"],
            fonte        = n["fonte"],
            tipo         = n["analise"].get("tipo", "outro"),
            numero       = n["analise"].get("numero"),
            data_pub     = n["analise"].get("data_publicacao"),
            ementa       = n["analise"].get("ementa"),
            pontos       = json.dumps(n["analise"].get("pontos_principais", []),
                                      ensure_ascii=False),
            tributos     = json.dumps(n["analise"].get("tributos_afetados", []),
                                      ensure_ascii=False),
            impacto      = n["analise"].get("impacto", "baixo"),
            urgencia     = n["analise"].get("urgencia", "medio_prazo"),
            score        = n["analise"].get("relevancia_score", 1),
            observacoes  = n["analise"].get("observacoes"),
        )
        log.info("Salvo: %s", n["titulo"][:80])

    # 5. Envia e-mail de alerta
    # Ordena por impacto: alto > medio > baixo
    _ordem = {"alto": 0, "medio": 1, "baixo": 2}
    normas_para_salvar.sort(
        key=lambda x: _ordem.get(x["analise"].get("impacto", "baixo"), 2)
    )
    enviado = enviar_email(normas_para_salvar)
    db.registrar_execucao(
        normas_encontradas=len(normas_para_salvar),
        email_enviado=enviado,
    )
    log.info("=== Ciclo concluído. Normas salvas: %d | E-mail: %s ===",
             len(normas_para_salvar), "OK" if enviado else "FALHOU")


if __name__ == "__main__":
    executar()
