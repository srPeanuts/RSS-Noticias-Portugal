"""
Procura e testa feeds RSS de um jornal (para substituir um feed parado).

Uso:
    python scripts/testar_feeds.py                     # testa os feeds de fontes.json
    python scripts/testar_feeds.py https://www.dn.pt   # procura feeds num site
    python scripts/testar_feeds.py https://exemplo.pt/rss.xml
    python scripts/testar_feeds.py -v https://www.jn.pt     # mostra também os endereços que falharam e porquê

Para cada endereço mostra: se respondeu, quantas notícias tem e a data da mais recente.
Um feed bom tem notícias de hoje.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atualizar import RAIZ, ler_feed  # noqa: E402
from rede import obter  # noqa: E402

CAMINHOS = ["/rss", "/rss/", "/rss.xml", "/feed", "/feed/", "/feed/rss", "/stories.rss", "/index.rss", "/rss/feed.xml",
            "/arc/outboundfeeds/rss/", "/arc/outboundfeeds/rss/?outputType=xml", "/rss/ultimas", "/rss/ultimas.xml",
            "/rss/ultima-hora", "/rss/ultima-hora.xml", "/rss/home", "/rss/noticias", "/feeds/rss", "/feeds/ultimas",
            "/ultimas/rss", "/rss/nacional", "/rss/politica", "/rss/economia", "/rss/sociedade"]
SITES_A_PROCURAR = []          # sites onde procurar feeds quando o script corre sem argumentos


def google_noticias(site: str) -> str:
    """Último recurso: as notícias do site segundo o Google Notícias (sem resumo, mas com título, link e hora)."""
    dominio = site.split("//")[-1].strip("/").removeprefix("www.")
    return f"https://news.google.com/rss/search?q=site:{dominio}+when:1d&hl=pt-PT&gl=PT&ceid=PT:pt-150"


def testar(url: str) -> tuple[bool, str]:
    try:
        artigos = ler_feed({"fonte": "teste", "url": url, "categoria": ""})
    except Exception as e:
        return False, f"✗ {e.__class__.__name__}: {str(e)[:70]}"
    if not artigos:
        return False, "✗ respondeu, mas sem notícias"
    recente = max(datetime.fromisoformat(a["data"]) for a in artigos)
    horas = (datetime.now(timezone.utc) - recente).total_seconds() / 3600
    estado = "✓" if horas < 24 else "⚠ parado?"
    return horas < 24, f"{estado} {len(artigos)} notícias · mais recente há {horas:.0f} h ({recente:%d/%m %H:%M})"


def procurar(site: str) -> list:
    """Endereços de RSS anunciados na página inicial + caminhos habituais."""
    candidatos = []
    try:
        html = obter(site).text
        for m in re.finditer(r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', html, re.I):
            href = re.search(r'href=["\']([^"\']+)', m.group(0))
            if href:
                candidatos.append(urljoin(site + "/", href.group(1)))
    except Exception as e:
        print(f"  (não consegui abrir {site}: {e.__class__.__name__})")
    candidatos += [site.rstrip("/") + c for c in CAMINHOS]
    return list(dict.fromkeys(candidatos))


def main():
    args = sys.argv[1:]
    detalhe = "-v" in args
    args = [a for a in args if a != "-v"]
    if not args:
        print("== Feeds atuais (fontes.json) ==")
        for f in json.loads((RAIZ / "fontes.json").read_text(encoding="utf-8"))["feeds"]:
            print(f"  {f['fonte']:<20} {testar(f['url'])[1]}  {f['url']}")
        args = SITES_A_PROCURAR
    for a in args:
        if re.search(r"(rss|feed|xml|atom)", a.split("/", 3)[-1] if a.count("/") > 2 else "", re.I):
            print(f"  {testar(a)[1]}  {a}")
            continue
        print(f"\n== À procura de feeds em {a} ==")
        encontrados = 0
        for url in procurar(a):
            ok, txt = testar(url)
            if ok or "notícias" in txt or detalhe:
                print(f"  {txt}  {url}")
                encontrados += ok
        if not detalhe:
            print("  (só aparecem os endereços que responderam com notícias; usa -v para ver os que falharam)")
        if not encontrados:
            url = google_noticias(a)
            print(f"  Alternativa pelo Google Notícias: {testar(url)[1]}\n    {url}")


if __name__ == "__main__":
    sys.exit(main())
