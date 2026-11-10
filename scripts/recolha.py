"""Recolha dos feeds RSS e das capas (VerCapas), e o diagnóstico de cada feed."""

import re
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from rede import obter
from texto import limpar_html

MAX_PARALELO = 12        # pedidos em simultâneo no total (rede.POR_SITE limita cada site)

# --------------------------------------------------------------------------
# Recolha RSS
# --------------------------------------------------------------------------

ATOM = "{http://www.w3.org/2005/Atom}"
RDF = "{http://purl.org/rss/1.0/}"          # alguns jornais (ex.: CM) usam RSS 1.0
DC = "{http://purl.org/dc/elements/1.1/}"


def _texto(el: ET.Element, *nomes: str) -> str:
    for n in nomes:
        f = el.find(n)
        if f is not None and (f.text or "").strip():
            return f.text.strip()
    return ""


def _data(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        d = parsedate_to_datetime(valor)
    except Exception:
        try:
            d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def ler_feed(feed: dict) -> list:
    raiz = ET.fromstring(obter(feed["url"]).content)
    itens = raiz.findall(".//item") or raiz.findall(f".//{RDF}item") or raiz.findall(f".//{ATOM}entry")
    categoria_forcada = feed.get("categoria", "")
    artigos = []
    for it in itens:
        titulo = limpar_html(_texto(it, "title", f"{ATOM}title", f"{RDF}title"))
        link = _texto(it, "link", f"{RDF}link")
        if not link:
            el_link = it.find(f"{ATOM}link")
            link = el_link.get("href", "") if el_link is not None else ""
        resumo = limpar_html(_texto(it, "description", f"{RDF}description", f"{ATOM}summary", f"{ATOM}content"))[:3000]
        data = _data(_texto(it, "pubDate", f"{ATOM}published", f"{ATOM}updated", f"{DC}date"))
        autor = limpar_html(_texto(it, f"{DC}creator", "author", f"{ATOM}author/{ATOM}name"))[:80]
        cats = [limpar_html(c.text or "") for c in it.findall("category")]
        cats += [limpar_html(c.text or "") for c in it.findall(f"{DC}subject")]
        cats += [c.get("term", "") for c in it.findall(f"{ATOM}category")]
        fonte = feed["fonte"]
        # Google Notícias: o título vem como "Título - Jornal"
        src = it.find("source")
        if src is not None and (src.text or "").strip():
            fonte = src.text.strip()
            titulo = re.sub(r"\s+-\s+" + re.escape(fonte) + r"$", "", titulo)
            resumo = ""
        if not titulo or not link:
            continue
        artigos.append({
            "titulo": titulo, "link": link, "resumo": resumo, "fonte": fonte, "autor": autor,
            "data": (data or datetime.now(timezone.utc)).isoformat(),
            "cats_feed": [c for c in cats if c], "categoria_forcada": categoria_forcada,
            "_feed": feed["url"],      # só para o diagnóstico; não é guardado
        })
    return artigos


def _erro_feed(e: Exception) -> str:
    codigo = getattr(getattr(e, "response", None), "status_code", None)
    return f"erro HTTP {codigo}" if codigo else f"erro: {e.__class__.__name__}"


def _resultado(futuro: Future) -> tuple:
    try:
        return ("ok", futuro.result())
    except Exception as e:
        return ("erro", e)


def recolher(config: dict, feeds: bool = True, capas: bool = True) -> tuple[list, dict, list]:
    """Lê os feeds e as capas ao mesmo tempo, num só conjunto de threads."""
    lista_feeds = config["feeds"] if feeds else []
    lista_capas = config["capas"] if capas else []
    with ThreadPoolExecutor(max_workers=MAX_PARALELO) as pool:
        fut_feeds = [pool.submit(ler_feed, f) for f in lista_feeds]
        fut_capas = [pool.submit(ler_capa, c) for c in lista_capas]
        res_feeds = [_resultado(f) for f in fut_feeds]
        res_capas = [_resultado(f) for f in fut_capas]
    artigos, estado = _relatorio_feeds(lista_feeds, res_feeds) if feeds else ([], {})
    return artigos, estado, (_relatorio_capas(lista_capas, res_capas) if capas else [])


def _relatorio_feeds(feeds: list, resultados: list) -> tuple[list, dict]:
    print("Feeds RSS:")
    todos, estado = [], {}
    for feed, (situacao, valor) in zip(feeds, resultados):
        if situacao == "ok":
            todos += valor
            estado[feed["url"]] = len(valor)
            print(f"  ✓ {feed['fonte']:<22} {len(valor):>3} artigos  {feed['url']}")
        else:
            estado[feed["url"]] = _erro_feed(valor)
            print(f"  ✗ {feed['fonte']:<22} ERRO {valor.__class__.__name__}: {str(valor)[:80]}")
    return todos, estado


EXEMPLOS_POR_FEED = 5


def _registar_descarte(dg: dict, a: dict, motivo: str) -> None:
    """Guarda porque é que a notícia ficou de fora (contagem por motivo + alguns exemplos)."""
    dg.setdefault("motivos", Counter())[motivo.split(":")[0]] += 1
    exemplos = dg.setdefault("exemplos_fora", [])
    if len(exemplos) < EXEMPLOS_POR_FEED:
        exemplos.append({"titulo": a["titulo"][:140], "motivo": motivo, "link": a.get("link", "")})


def _relatorio_diagnostico(diag: dict) -> None:
    """Mostra, para cada feed, o que aconteceu às notícias recebidas. Um feed com todas as
    notícias "antigas" está parado (o jornal deixou de o atualizar ou mudou de endereço)."""
    if not diag:
        return
    print("Diagnóstico dos feeds (recebidas → antigas / sem categoria / aceites · mais recente):")
    for d in diag.values():
        if "erro" in d:
            continue
        parado = d["recebidas"] > 0 and d["antigas"] == d["recebidas"]
        recente = d["mais_recente"][:16].replace("T", " ") if d["mais_recente"] else "—"
        print(f"  {'⚠' if parado else ' '} {d['fonte']:<22} {d['recebidas']:>3} → {d['antigas']:>3} / "
              f"{d['sem_categoria']:>3} / {d['aceites']:>3} · {recente}" + ("  (feed parado?)" if parado else ""))
        if d.get("motivos"):
            print("        fora: " + ", ".join(f"{m} {n}" for m, n in d["motivos"].most_common()))
            for e in d.get("exemplos_fora", []):
                print(f"          - {e['titulo'][:90]}  [{e['motivo']}]")

# --------------------------------------------------------------------------
# Capas
# --------------------------------------------------------------------------

_RE_OG_IMAGEM = (re.compile(r'property=["\']og:image["\']\s+content=["\']([^"\']+)'),
                 re.compile(r'content=["\']([^"\']+)["\']\s+property=["\']og:image'),
                 re.compile(r'(https://imgs\.vercapas\.com/covers/[^"\']+\.jpe?g)'))
_RE_THUMB = re.compile(r"/thumbc/\d+/")
_RE_DATA = re.compile(r"(\d{4}-\d{2}-\d{2})")


def ler_capa(c: dict) -> dict:
    pagina = f"https://www.vercapas.com/capa/{c['slug']}.html"
    html = obter(pagina).text
    m = next((m for m in (r.search(html) for r in _RE_OG_IMAGEM) if m), None)
    if not m:
        raise ValueError("imagem não encontrada")
    miniatura = m.group(1)
    imagem = _RE_THUMB.sub("/", miniatura)
    d = _RE_DATA.search(imagem)
    return {"nome": c["nome"], "slug": c["slug"], "imagem": imagem, "miniatura": miniatura,
            "pagina": pagina, "data": d.group(1) if d else None}


def _relatorio_capas(lista: list, resultados: list) -> list:
    print("Capas:")
    capas = []
    for c, (situacao, valor) in zip(lista, resultados):
        if situacao == "ok":
            capas.append(valor)
            print(f"  ✓ capa {c['nome']}")
        else:
            print(f"  ✗ capa {c['nome']}: {valor}")
    return capas
