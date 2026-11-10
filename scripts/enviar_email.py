"""
Resumo diário por email.

Lê os dados já gerados em docs/data e envia um email com os temas principais do dia.
Configuração (em GitHub → Settings → Secrets and variables → Actions):
  EMAIL_UTILIZADOR   conta que envia (ex.: o teu Gmail)
  EMAIL_PASSWORD     palavra-passe de aplicação dessa conta (não a palavra-passe normal)
  EMAIL_PARA         para onde enviar (pode ser o mesmo endereço)
  EMAIL_SMTP         servidor SMTP (opcional, por omissão smtp.gmail.com)
  EMAIL_PORTA        porta SMTP (opcional, por omissão 465)
  SITE_URL           endereço da tua app (opcional, para o link no email)

Sem os segredos definidos, gera só o ficheiro de pré-visualização (resumo_email.html) e não envia nada.
"""

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
SITE = RAIZ / "docs" / "data"
LISBOA = ZoneInfo("Europe/Lisbon")

NOMES = {"politica": "Política", "governo": "Governo", "economia": "Economia",
         "sociedade": "Sociedade e Cultura", "opiniao": "Opinião"}
CORES = {"politica": "#2a78d6", "governo": "#eb6834", "economia": "#1baf7a",
         "sociedade": "#eda100", "opiniao": "#e87ba4"}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]
DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def ler(caminho: Path, padrao=None):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return padrao


def data_extenso(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{DIAS_SEMANA[d.weekday()]}, {d.day} de {MESES[d.month - 1]}"


def num(v, casas=1) -> str:
    return f"{v:.{casas}f}".replace(".", ",") if v is not None else "–"


def bloco_tema(t: dict) -> str:
    a = t["artigos"][0] if t.get("artigos") else {}
    fontes = f'{t["n_fontes"]} fonte{"s" if t["n_fontes"] != 1 else ""}'
    resumo = escape(t.get("resumo", "")[:260]) + ("…" if len(t.get("resumo", "")) > 260 else "")
    return f"""
    <tr><td style="padding:10px 0;border-bottom:1px solid #ecebe6">
      <a href="{escape(a.get('link', '#'))}" style="font:600 16px/1.35 Georgia,serif;color:#1b1a17;text-decoration:none">{escape(t['titulo'])}</a>
      <div style="font:12px Arial,sans-serif;color:#6b675e;margin-top:3px">{fontes}{(' · ' + escape(', '.join(t['fontes'][:4]))) if t.get('fontes') else ''}</div>
      {f'<div style="font:13px/1.45 Arial,sans-serif;color:#4a4740;margin-top:4px">{resumo}</div>' if resumo else ''}
    </td></tr>"""


def bloco_opiniao(t: dict) -> str:
    a = t["artigos"][0] if t.get("artigos") else {}
    autor = f'{escape(t["autor"])} · ' if t.get("autor") else ""
    return f"""
    <tr><td style="padding:8px 0;border-bottom:1px solid #ecebe6">
      <a href="{escape(a.get('link', '#'))}" style="font:600 15px/1.35 Georgia,serif;color:#1b1a17;text-decoration:none">{escape(t['titulo'])}</a>
      <div style="font:12px Arial,sans-serif;color:#6b675e;margin-top:3px">{autor}{escape(a.get('fonte', ''))}</div>
    </td></tr>"""


def seccao(titulo: str, cor: str, linhas: str) -> str:
    if not linhas:
        return ""
    return f"""
  <tr><td style="padding:22px 0 4px">
    <div style="font:700 12px Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#6b675e">
      <span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:{cor};margin-right:6px"></span>{titulo}
    </div>
  </td></tr>
  <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{linhas}</table></td></tr>"""


def construir(n_por_categoria: int = 3) -> tuple[str, str, str]:
    indice = ler(SITE / "indice.json", {})
    hoje = indice.get("hoje") or datetime.now(LISBOA).date().isoformat()
    dia = ler(SITE / "dias" / f"{hoje}.json") or ler(SITE / "dias" / f"{indice.get('dias', [hoje])[-1]}.json", {})
    temas = dia.get("temas", {})
    semana = ler(SITE / "semana.json", {})
    ind = ler(SITE / "indicadores.json", {})
    site = os.environ.get("SITE_URL", "").strip()

    corpo = ""
    # Em alta
    alta = semana.get("em_alta", [])[:3]
    if alta:
        linhas = "".join(bloco_tema({**h, "fontes": [], "resumo": ""}) for h in alta)
        corpo += seccao("▲ Em alta", "#b3261e", linhas)
    # Categorias de notícias
    for c in ("politica", "governo", "economia", "sociedade"):
        linhas = "".join(bloco_tema(t) for t in temas.get(c, [])[:n_por_categoria])
        corpo += seccao(NOMES[c], CORES[c], linhas)
    # Opinião
    linhas = "".join(bloco_opiniao(t) for t in temas.get("opiniao", [])[:n_por_categoria])
    corpo += seccao("Opinião", CORES["opiniao"], linhas)
    # Indicadores
    if ind.get("indicadores"):
        celulas = "".join(
            f'<td style="padding:8px;border:1px solid #ecebe6;border-radius:6px;font:12px Arial,sans-serif;color:#6b675e">'
            f'{escape(i["nome"])}<br><b style="font:600 18px Arial,sans-serif;color:#1b1a17">{num(i["valor"], 1)}{escape(i["unidade"])}</b></td>'
            for i in ind["indicadores"])
        corpo += f"""
  <tr><td style="padding:22px 0 6px"><div style="font:700 12px Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#6b675e">Indicadores económicos</div></td></tr>
  <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="4"><tr>{celulas}</tr></table></td></tr>"""

    principal = next((temas[c][0]["titulo"] for c in ("politica", "governo", "economia", "sociedade") if temas.get(c)), "")
    assunto = f"Notícias de Portugal · {data_extenso(hoje)}" + (f" — {principal}" if principal else "")
    botao = (f'<p style="text-align:center;margin:28px 0 8px"><a href="{escape(site)}" style="background:#1b1a17;color:#fff;'
             f'font:600 14px Arial,sans-serif;padding:10px 18px;border-radius:8px;text-decoration:none">Abrir a app</a></p>') if site else ""
    html = f"""<!doctype html><html lang="pt-PT"><body style="margin:0;background:#f7f5f0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f5f0"><tr><td align="center" style="padding:20px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#ffffff;border:1px solid #e4e0d6;border-radius:12px;padding:20px 22px">
  <tr><td>
    <div style="font:700 24px Georgia,serif;color:#1b1a17">Notícias <span style="color:#b3261e">de Portugal</span></div>
    <div style="font:13px Arial,sans-serif;color:#6b675e;margin-top:4px">Resumo de {data_extenso(hoje)} · ordenado pelo número de jornais que falam de cada tema</div>
  </td></tr>
  {corpo}
  <tr><td>{botao}<p style="font:11px Arial,sans-serif;color:#9a968c;text-align:center;margin-top:18px">Gerado automaticamente a partir dos RSS dos jornais portugueses e do Eurostat.</p></td></tr>
</table></td></tr></table></body></html>"""

    texto = [assunto, ""]
    for c in ("politica", "governo", "economia", "sociedade", "opiniao"):
        if temas.get(c):
            texto.append(NOMES[c].upper())
            for t in temas[c][:n_por_categoria]:
                texto.append(f"- {t['titulo']} ({t['artigos'][0]['link'] if t.get('artigos') else ''})")
            texto.append("")
    if site:
        texto.append(f"Abrir a app: {site}")
    return assunto, html, "\n".join(texto)


HORA_ENVIO = 12         # hora de Lisboa a partir da qual o resumo do dia pode sair (uma vez por dia)
RECOLHA_MAX_MINUTOS = 30   # a recolha que antecede o envio tem de ter sido feita há menos disto
RECOLHA_MIN_FONTES = 0.5   # e pelo menos metade dos feeds tem de ter respondido com notícias


def _saida_github(chave: str, valor: str) -> None:
    caminho = os.environ.get("GITHUB_OUTPUT")
    if caminho:
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(f"{chave}={valor}\n")


MARCADOR = RAIZ / "dados" / "email_enviado.txt"   # data (AAAA-MM-DD) do último resumo enviado


def enviado_hoje(agora: datetime | None = None) -> bool:
    agora = agora or datetime.now(LISBOA)
    try:
        return MARCADOR.read_text(encoding="utf-8").strip() == agora.date().isoformat()
    except OSError:
        return False


def marcar_enviado(agora: datetime | None = None) -> None:
    agora = agora or datetime.now(LISBOA)
    MARCADOR.parent.mkdir(parents=True, exist_ok=True)
    MARCADOR.write_text(agora.date().isoformat() + "\n", encoding="utf-8")


def e_automatico(evento: str | None = None, agendado: str | None = None) -> bool:
    """Execução automática: agendamento do GitHub ou chamada do cron-job.org (que manda agendado=true).
    O "Run workflow" carregado à mão não é automático."""
    evento = evento if evento is not None else os.environ.get("GITHUB_EVENT_NAME", "")
    agendado = agendado if agendado is not None else os.environ.get("AGENDADO", "")
    return evento == "schedule" or str(agendado).strip().lower() in ("true", "1", "sim")


def deve_enviar(agora: datetime | None = None, evento: str | None = None, agendado: str | None = None,
                ja_enviado: bool | None = None) -> bool:
    """Automático: envia uma vez por dia, a partir das HORA_ENVIO de Lisboa (um atraso já não cancela o envio,
    e se o cron-job.org e o agendamento do GitHub correrem os dois, só o primeiro envia).
    Manual (Run workflow): envia sempre."""
    agora = agora or datetime.now(LISBOA)
    if not e_automatico(evento, agendado):
        return True
    if ja_enviado is None:
        ja_enviado = enviado_hoje(agora)
    return agora.hour >= HORA_ENVIO and not ja_enviado


def _motivo_para_nao_enviar(agora: datetime) -> str:
    if enviado_hoje(agora):
        return "o resumo de hoje já foi enviado. Nada a fazer."
    return f"ainda não são {HORA_ENVIO}h. Nada a fazer."


def verificar_recolha(agora: datetime | None = None) -> tuple[bool, list]:
    """Confirma que a recolha de notícias acabada de fazer correu bem. Devolve (ok, mensagens)."""
    agora = agora or datetime.now(LISBOA)
    indice = ler(SITE / "indice.json", {}) or {}
    problemas, info = [], []

    if indice.get("hoje") != agora.date().isoformat():
        problemas.append(f"os dados publicados são de {indice.get('hoje')}, não de hoje")
    try:
        atualizado = datetime.fromisoformat(indice["atualizado"])
        minutos = (agora - atualizado).total_seconds() / 60
        info.append(f"recolha feita às {atualizado:%H:%M}")
        if minutos > RECOLHA_MAX_MINUTOS:
            problemas.append(f"a última recolha foi há {minutos:.0f} minutos")
    except Exception:
        problemas.append("não foi possível ler a hora da última recolha")

    fontes = indice.get("fontes", {})
    ok = sum(1 for v in fontes.values() if isinstance(v, int) and v > 0)
    info.append(f"{ok} de {len(fontes)} feeds com notícias")
    if not fontes or ok < RECOLHA_MIN_FONTES * len(fontes):
        problemas.append(f"só {ok} de {len(fontes)} feeds responderam")

    dia = ler(SITE / "dias" / f"{agora.date().isoformat()}.json", {}) or {}
    n = dia.get("n_artigos", 0)
    info.append(f"{n} notícias hoje")
    if not n:
        problemas.append("não há notícias de hoje")

    return (not problemas), (problemas or info)


def main():
    argumentos = sys.argv[1:]
    agora = datetime.now(LISBOA)
    evento = os.environ.get("GITHUB_EVENT_NAME", "")

    # --hora: diz ao workflow se esta execução deve enviar (para não recolher notícias à toa)
    if "--hora" in argumentos:
        enviar = deve_enviar(agora, evento)
        print(f"São {agora:%H:%M} em Lisboa: " + ("é para enviar o resumo." if enviar
              else _motivo_para_nao_enviar(agora)))
        _saida_github("enviar", "true" if enviar else "false")
        return 0

    # --verificar: confirma que a recolha que antecede o envio correu bem (falha o workflow se não)
    if "--verificar" in argumentos:
        ok, mensagens = verificar_recolha(agora)
        print(("✓ Recolha confirmada: " if ok else "✗ A recolha falhou: ") + "; ".join(mensagens))
        return 0 if ok else 1

    if not deve_enviar(agora, evento):
        print(f"São {agora:%H:%M} em Lisboa: {_motivo_para_nao_enviar(agora)}")
        return 0
    assunto, html, texto = construir()
    (RAIZ / "resumo_email.html").write_text(html, encoding="utf-8")
    utilizador = os.environ.get("EMAIL_UTILIZADOR", "").strip()
    password = os.environ.get("EMAIL_PASSWORD", "").strip()
    para = os.environ.get("EMAIL_PARA", "").strip() or utilizador
    if not (utilizador and password):
        print("Segredos de email não definidos: gerei só a pré-visualização (resumo_email.html).")
        return 0
    servidor = os.environ.get("EMAIL_SMTP", "").strip() or "smtp.gmail.com"
    porta = int(os.environ.get("EMAIL_PORTA", "").strip() or 465)

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = f"Notícias de Portugal <{utilizador}>"
    msg["To"] = para
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")

    contexto = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(servidor, porta, context=contexto, timeout=30) as s:
            s.login(utilizador, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(servidor, porta, timeout=30) as s:
            s.starttls(context=contexto)
            s.login(utilizador, password)
            s.send_message(msg)
    marcar_enviado(agora)   # as outras execuções automáticas de hoje já não enviam
    print(f"Email enviado para {para}: {assunto}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
