"""
Notícias de Portugal — recolha e análise diária.

1. Lê os feeds RSS listados em fontes.json
2. Vai buscar as capas dos jornais (vercapas.com)
3. Classifica as notícias em política / governo / economia / sociedade e cultura / opinião
4. Agrupa notícias de jornais diferentes que falam do mesmo assunto
5. Ordena os temas pela cobertura (quantos jornais falam deles)
6. Gera os ficheiros JSON que a página web (pasta docs/) mostra

Não usa nenhuma IA paga: tudo corre com Python + scikit-learn, de graça.
"""

import hashlib
import inspect
import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indicadores  # noqa: E402
from lugares import LUGARES  # noqa: E402
from rede import obter  # noqa: E402
import tendencias   # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"            # histórico bruto (artigos e temas por dia)
SITE = RAIZ / "docs" / "data"     # o que a página web lê
LISBOA = ZoneInfo("Europe/Lisbon")
MAX_PARALELO = 12        # pedidos em simultâneo no total (rede.POR_SITE limita cada site)

CATEGORIAS = ["politica", "governo", "economia", "sociedade", "opiniao"]
NOTICIAS = ["politica", "governo", "economia", "sociedade"]   # categorias agrupadas por cobertura

# --------------------------------------------------------------------------
# Texto
# --------------------------------------------------------------------------

STOPWORDS = set("""
a à ao aos as às até com como contra da das de del dela dele deles do dos e é em entre era eram essa esse esta está
estão este eu foi foram há isso isto já la lhe lhes mais mas me mesmo meu minha muito na nas nem no nos nós num numa
o os ou para pela pelas pelo pelos por qual quando que quem se sem ser será seu seus sua suas são só também te tem têm
ter um uma umas uns vai vão foi sobre após diz dizem disse ainda pode podem vez anos ano hoje ontem amanhã dia dias
novo nova novos novas sua seu há ser está estar sido sendo tinha todo toda todos todas outro outra outros outras
portugal português portuguesa portugueses portuguesas país vídeo veja fotos foto galeria direto live ao-vivo notícias
""".split())


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def limpar_html(texto: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", unescape(texto or ""))
    return re.sub(r"\s+", " ", texto).strip()


STOP_NORM = {normalizar(p) for p in STOPWORDS}

# --------------------------------------------------------------------------
# Classificação por palavras-chave
# --------------------------------------------------------------------------

EXCLUIR = [
    "desporto", "futebol", "modalidades", "benfica", "sporting", "fc porto", "liga", "mundial de futebol",
    "mundo", "internacional", "globo", "europa", "eua", "estados unidos", "medio oriente", "ucrania", "russia",
    "podcast", "video", "radio", "palavras cruzadas", "horoscopo",
    "lifestyle", "gastronomia", "moda", "famosos", "televisao", "tv", "auto", "motores", "tecnologia",
    "jogos", "passatempos", "boa cama boa mesa", "inimigo publico",
]

# Nos feeds de economia (Negócios, Económico...) a secção "Tecnologia" fala de empresas portuguesas: não exclui
EXCLUIR_SE_ECONOMIA = [e for e in EXCLUIR if e not in ("tecnologia",)]

# Só para notícias que, sem estas palavras, ficavam sem categoria e eram deitadas fora
# (a CNN só manda o título: "Matou o colega de casa…", "…operação Picoas"). Não mudam a categoria das outras.
SOCIEDADE_SE_NADA_MAIS = [
    "matou", "mata", "morto", "morta", "mortos", "morte", "mortes", "homicidio", "assassinado", "assassinada",
    "esfaqueado", "esfaqueada", "baleado", "baleada", "agressao", "agressoes", "agredido", "agredida",
    "violencia", "violencia domestica", "assalto", "roubo", "furto", "burla", "droga", "trafico",
    "preso", "presos", "presa", "prisao", "julgamento", "julgado", "condenado", "condenada", "absolvido",
    "operacao", "buscas", "suspeito", "suspeita", "suspeitos", "inquerito", "juiz", "juiza", "pj",
    "acidente", "atropelado", "atropelada", "colisao", "despiste", "bombeiros", "inem", "urgencias",
    "doentes", "camara de", "autarquia", "freguesia",
    # mau tempo (desde nov/2026 entra na app, em Sociedade)
    "depressao", "tempestade", "temporal", "chuva", "chuvas", "vento", "ventos", "ondulacao", "ondas", "cheias",
    "inundacoes", "trovoada", "granizo", "neve", "nevoeiro", "calor", "frio", "seca", "ipma", "protecao civil",
    "ocorrencias", "meteorologia", "aviso amarelo", "aviso laranja", "aviso vermelho", "previsao do tempo",
]
ECONOMIA_SE_NADA_MAIS = [
    "recessao", "excedente", "telecomunicacoes", "operadoras", "tarifario", "tarifarios", "faturacao", "lucros",
    "prejuizo", "prejuizos", "receitas", "vendas", "consumidores", "custo de vida",
]

PALAVRAS = {
    "politica": [
        "politica", "parlamento", "assembleia da republica", "deputado", "deputada", "deputados",
        "presidente da republica", "belem", "eleicoes", "eleitoral", "autarquicas", "legislativas",
        "presidenciais", "partido", "partidos", "oposicao", "coligacao", "mocao de censura", "votacao",
        "promulga", "veto", "socialista", "social-democrata", "marcelo", "seguro", "ventura", "autarca",
        "camara municipal", "entre politicos", "politica nacional", "lider", "dirigente", "militantes",
        "congresso", "candidato", "candidata", "sondagem",
    ],
    "governo": [
        "governo", "executivo", "conselho de ministros", "ministro", "ministra", "ministros", "ministerio da",
        "ministerio do", "ministerio das", "ministerio dos", "secretario de estado", "secretaria de estado",
        "primeiro-ministro", "montenegro", "sao bento", "decreto-lei", "decreto", "portaria", "governante",
        "governantes", "tutela", "programa do governo",
    ],
    "economia": [
        "economia", "economico", "economica", "mercados", "bolsa", "empresas", "empresa", "inflacao", "bce",
        "banco", "bancos", "juros", "euribor", "pib", "crescimento", "exportacoes", "impostos", "irs", "irc",
        "iva", "orcamento", "oe20", "defice", "divida", "salario", "salarios", "emprego", "desemprego",
        "trabalho", "greve", "pensoes", "reformas", "precos", "combustiveis", "energia", "tap", "habitacao",
        "rendas", "credito", "investimento", "financas", "fiscal", "negocios", "turismo", "industria",
        "mercados", "seguros", "imobiliario", "startup",
    ],
    "sociedade": [
        "sociedade", "pais", "cultura", "saude", "sns", "hospital", "hospitais", "medicos", "enfermeiros",
        "educacao", "escola", "escolas", "professores", "universidade", "ensino", "justica", "tribunal",
        "ministerio publico", "acusacao", "arguidos", "arguido", "acusados", "detido", "detidos", "policia", "psp", "gnr", "crime", "incendio", "incendios", "ambiente", "clima",
        "ciencia", "artes", "cinema", "musica", "livros", "literatura", "teatro", "museu", "exposicao",
        "festival", "patrimonio", "religiao", "igreja", "imigracao", "imigrantes", "aima", "seguranca social",
        "transportes", "comboios", "cp", "metro", "aeroporto", "local", "lisboa", "porto",
    ],
}

# Notícias sobre outros países: descartadas, a não ser que mencionem Portugal
ESTRANGEIRO = [
    "eua", "estados unidos", "trump", "casa branca", "washington", "biden", "china", "chines", "chinesa", "chineses", "chinesas", "americano", "americana", "norte-americano", "norte-americana", "pequim",
    "russia", "russo", "russa", "putin", "moscovo", "kremlin", "ucrania", "ucraniano", "kiev", "zelensky",
    "israel", "israelita", "gaza", "hamas", "netanyahu", "irao", "iraniano", "libano", "siria", "medio oriente",
    "brasil", "brasileiro", "brasileira", "brasileiros", "brasileiras", "lula", "bolsonaro", "venezuela", "colombia", "argentina", "mexico",
    "angola", "angolano", "mocambique", "mocambicano", "sao tome", "cabo verde", "guine-bissau", "timor",
    "espanha", "espanhol", "madrid", "sanchez", "franca", "frances", "francesa", "paris", "macron",
    "alemanha", "alemao", "berlim", "italia", "italiano", "reino unido", "londres", "britanico", "india", "japao",
    "coreia", "taiwan", "asia", "africa do sul", "turquia", "nato", "onu", "papa", "vaticano",
    # acrescentados em nov/2026 (passavam notícias de fora: Albânia, Cracóvia, Macau...)
    "russos", "russas", "ucranianos", "israelitas", "palestina", "palestiniano", "palestinianos", "cisjordania",
    "jerusalem", "tel aviv", "teerao", "iranianos", "hezbollah", "iraque", "afeganistao", "paquistao",
    "arabia saudita", "qatar", "catar", "iemen", "egito", "marrocos", "argelia", "tunisia", "libia", "nigeria",
    "quenia", "etiopia", "sudao", "congo", "senegal", "luanda", "maputo", "bissau", "dili",
    "polonia", "polaco", "polaca", "varsovia", "cracovia", "albania", "tirana", "grecia", "grego", "atenas",
    "chipre", "irlanda", "dublin", "holanda", "paises baixos", "amesterdao", "suica", "austria", "suecia",
    "noruega", "dinamarca", "finlandia", "hungria", "orban", "romenia", "bulgaria", "servia", "croacia",
    "gronelandia", "canada", "chile", "equador", "bolivia", "uruguai", "paraguai", "nicaragua", "haiti",
    "bogota", "caracas", "buenos aires", "nova iorque", "california", "texas", "hong kong",
    "macau", "xangai", "toquio", "vietname", "tailandia", "indonesia", "filipinas", "australia", "nova zelandia",
    "starmer", "merz", "meloni", "le pen", "milei", "kim jong", "xi jinping", "erdogan", "modi",
    "jamaica", "caraibas", "porto rico", "republica dominicana", "bahamas", "furacao", "tufao",
    "japones", "japonesa", "taiwanes", "taiwanesa", "coreano", "coreana", "indiano", "grega", "irlandes",
    "holandes", "neerlandes", "neerlandesa", "sueco", "sueca", "noruegues", "norueguesa", "suico",
]
PORTUGAL = [
    "portugal", "portugues", "portuguesa", "portugueses", "portuguesas", "lisboa", "porto", "coimbra", "braga",
    "faro", "algarve", "acores", "madeira", "funchal", "governo portugues", "montenegro", "marcelo", "seguro",
    "assembleia da republica", "sns", "psp", "gnr", "luso", "lusa", "tap", "cgd", "bcp",
    # mais cidades e regiões, para não deitar fora notícias com ligação a Portugal
    "aveiro", "setubal", "leiria", "evora", "viseu", "guimaraes", "santarem", "beja", "viana do castelo",
    "braganca", "vila real", "castelo branco", "portalegre", "sintra", "cascais", "oeiras", "almada",
    "matosinhos", "gaia", "povoa de varzim", "alentejo", "douro", "minho", "tras-os-montes",
]
# Títulos a ignorar (entretenimento, desporto que escapa às categorias). O mau tempo entra (decisão de nov/2026).
EXCLUIR_TITULO = [
    "mtv", "video music awards", "taylor swift", "formula 1", "cartoon", "horoscopo",
    "bola de ouro", "futebol", "benfica",
    "sporting", "fc porto", "selecao nacional", "liga dos campeoes",
]


OPINIAO_LINK = ["/opiniao", "/opinion", "/colunistas", "/cronica", "/cronicas", "/editorial",
                "observador.pt/programas/", "observador.pt/newsletters/"]
EXCLUIR_LINK = ["/programas/noticiario", "/programas/vamos-a-bola", "/desporto/", "/cartoon"]
OPINIAO_CATS = ["opiniao", "cronica", "cronicas", "colunistas", "colunista", "editorial", "coluna"]


def e_opiniao(link: str, cats_feed: list) -> bool:
    l = (link or "").lower()
    if any(p in l for p in OPINIAO_LINK):
        return True
    cats_norm = normalizar(" | ".join(cats_feed or []))
    return any(_contem(cats_norm, c) for c in OPINIAO_CATS)


def termo_estrangeiro(titulo: str, resumo: str) -> str | None:
    """O país/lugar estrangeiro no título, se a notícia não tiver ligação a Portugal; senão None."""
    t = normalizar(titulo)
    termo = next((e for e in ESTRANGEIRO if _contem(t, e)), None)
    if termo is None:
        return None
    contexto = normalizar(f"{titulo} {resumo[:300]}")
    return None if tem_ligacao_a_portugal(contexto) else termo


def tem_ligacao_a_portugal(texto_norm: str) -> bool:
    return any(_contem(texto_norm, p) for p in PORTUGAL) or any(_contem(texto_norm, p) for p in LUGARES)


def e_estrangeira(titulo: str, resumo: str) -> bool:
    return termo_estrangeiro(titulo, resumo) is not None


# O Governo é quem faz a ação no título: "Governo aprova…", "Ministra do Trabalho afasta…",
# "Habitação: Governo quer…". Não conta "PS pede ao Governo…" (aí o sujeito é um partido) nem governos regionais.
GOVERNO_SUJEITO = re.compile(
    r"^(?:[^:?]{1,40}[:?]\s*)?(?:o |a |os |as )?"
    r"(governo|executivo|conselho de ministros|ministr[oa]s?|primeiro-ministro|secretari[oa]s? de estado|governantes?)\b"
    r"(?! regional| da madeira| dos acores| de espanha| espanhol| frances| alemao| britanico| brasileiro)")


def governo_e_sujeito(titulo_norm: str) -> bool:
    return GOVERNO_SUJEITO.search(titulo_norm) is not None


# Rubricas diárias dos mercados (fecho da bolsa, Euribor, Brent, câmbios, combustíveis da semana...).
# Aparecem todos os dias em vários jornais, por isso na Semana e no Mês pareciam "as histórias do período".
# Continuam a aparecer em Hoje; ficam de fora da Semana, do Mês e do "Em alta".
RUBRICAS = [re.compile(p) for p in (
    r"\beuribor\b",
    r"^(a )?bolsa de lisboa\b", r"^psi\b", r"^(as )?bolsas (europeias|asiaticas|mundiais)\b",
    r"\bwall street\b", r"\b(nasdaq|dow jones|s&p 500|nikkei|hang seng|dax|cac 40|ibex)\b",
    r"^(a )?bolsa de (toquio|nova iorque|madrid|paris|frankfurt|londres|xangai|hong kong)\b",
    r"\b(cotacao|preco) do (brent|petroleo|barril)\b",
    r"^(o )?(brent|petroleo)\b.*\b(sobe|desce|cai|baixa|recua|avanca|valoriza|desvaloriza)",
    r"\b(gasoleo|gasolina|combustiveis)\b.*\b(sobe|sobem|desce|descem|cai|caem|baixa|baixam|aumenta|aumentam|preco)\b"
    r".*\b(semana|centimos?)\b",
    r"^(o )?(euro|dolar|ouro|bitcoin)\b.*\b(sobe|desce|cai|valoriza|desvaloriza|recua|avanca)\b",
    r"\bjuros da divida\b.*\b(sobem|descem|caem|recuam)\b",
)]


def e_rubrica(titulo: str) -> bool:
    t = normalizar(titulo)
    return any(r.search(t) for r in RUBRICAS)


SIGLAS_POLITICA = {"PS", "PSD", "CDS", "IL", "BE", "PCP", "PAN", "JPP", "Livre", "Chega", "AD", "OE"}


@lru_cache(maxsize=None)
def _padrao(termo: str) -> re.Pattern:
    """Prepara a pesquisa de cada termo uma só vez. Sem esta cache, o Python voltava a preparar
    o mesmo padrão centenas de milhares de vezes por atualização (a cache interna do re é pequena)."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(normalizar(termo)) + r"(?![a-z0-9])")


def _contem(texto_norm: str, termo: str) -> bool:
    return _padrao(termo).search(texto_norm) is not None


def classificar(titulo: str, resumo: str, cats_feed: list, categoria_forcada: str, link: str = "") -> str | None:
    """Devolve uma das CATEGORIAS ou None (descartar)."""
    return classificar_com_motivo(titulo, resumo, cats_feed, categoria_forcada, link)[0]


def _primeiro(texto_norm: str, termos) -> str | None:
    return next((t for t in termos if _contem(texto_norm, t)), None)


def classificar_com_motivo(titulo: str, resumo: str, cats_feed: list, categoria_forcada: str,
                           link: str = "") -> tuple[str | None, str]:
    """(categoria, motivo). Quando a notícia é descartada (categoria None), o motivo diz porquê."""
    cats_norm = normalizar(" | ".join(cats_feed))
    termo = next((p for p in EXCLUIR_LINK if p in (link or "").lower()), None)
    if termo:
        return None, f"endereço: {termo}"
    titulo_norm = normalizar(titulo)
    if e_opiniao(link, cats_feed):
        termo = _primeiro(titulo_norm, EXCLUIR_TITULO)
        if termo:
            return None, f"título: {termo}"
        termo = termo_estrangeiro(titulo, resumo)
        if termo:
            return None, f"outro país: {termo}"
        return "opiniao", "opinião"
    # Descartar desporto, internacional, etc. (pelas categorias do próprio jornal)
    if cats_feed:
        excluir = EXCLUIR_SE_ECONOMIA if categoria_forcada == "economia" else EXCLUIR
        termo = _primeiro(cats_norm, excluir)
        if termo and not any(_contem(cats_norm, t) for t in ["politica", "economia", "sociedade", "pais", "cultura"]):
            return None, f"secção do jornal: {termo}"
    termo = _primeiro(titulo_norm, EXCLUIR_TITULO)
    if termo:
        return None, f"título: {termo}"
    termo = termo_estrangeiro(titulo, resumo)
    if termo:
        return None, f"outro país: {termo}"
    # secção "Mundo"/"Internacional" do próprio jornal, sem ligação a Portugal
    if cats_feed and any(_contem(cats_norm, m) for m in ("mundo", "internacional", "globo")):
        contexto = normalizar(f"{titulo} {resumo[:300]}")
        if not tem_ligacao_a_portugal(contexto):
            return None, "secção Mundo, sem ligação a Portugal"

    texto_norm = normalizar(f"{titulo} {resumo}")
    pontos = Counter()
    if categoria_forcada in NOTICIAS:
        pontos[categoria_forcada] += 2   # a secção do feed ajuda, mas o conteúdo pode vencer
    if any(_contem(titulo_norm, t) for t in PALAVRAS["governo"]):
        pontos["governo"] += 2           # "Governo aprova...", "Ministra da Saúde..." no título
    for cat, termos in PALAVRAS.items():
        for t in termos:
            if cats_feed and _contem(cats_norm, t):
                pontos[cat] += 3
            if _contem(texto_norm, t):
                pontos[cat] += 1
    if governo_e_sujeito(titulo_norm):
        pontos["governo"] += 6           # "Governo aprova...", "Ministra do Trabalho diz..." ganha à secção do feed
    tokens = set(re.findall(r"[A-Za-zÀ-ÿ]+", titulo))
    if tokens & SIGLAS_POLITICA:
        pontos["politica"] += 2
    if not pontos:
        # sem palavras de nenhuma categoria: crime, justiça, acidentes, saúde, autarquias…
        if _primeiro(texto_norm, ECONOMIA_SE_NADA_MAIS):
            return "economia", "palavras de economia (último recurso)"
        if _primeiro(texto_norm, SOCIEDADE_SE_NADA_MAIS):
            return "sociedade", "palavras de sociedade (último recurso)"
        # …ou passada numa terra portuguesa ("…em Quarteira", "…no Alentejo")
        if _primeiro(normalizar(f"{titulo} {resumo[:300]}"), LUGARES):
            return "sociedade", "terra portuguesa"
        return None, "sem palavras de nenhuma categoria"
    return pontos.most_common(1)[0][0], "palavras-chave"


def _versao_regras() -> str:
    """Impressão digital das regras de classificação (listas de palavras + código das funções).
    Muda sempre que alguém afina as regras, e aí todo o histórico volta a ser classificado."""
    listas = [EXCLUIR, EXCLUIR_SE_ECONOMIA, LUGARES, SOCIEDADE_SE_NADA_MAIS, ECONOMIA_SE_NADA_MAIS, PALAVRAS, ESTRANGEIRO, PORTUGAL, EXCLUIR_TITULO, OPINIAO_LINK, EXCLUIR_LINK,
              OPINIAO_CATS, sorted(SIGLAS_POLITICA), NOTICIAS, GOVERNO_SUJEITO.pattern]
    partes = [json.dumps(listas, ensure_ascii=False)]
    partes += [inspect.getsource(f) for f in (normalizar, e_opiniao, termo_estrangeiro, tem_ligacao_a_portugal, _padrao, governo_e_sujeito, _primeiro, classificar_com_motivo)]
    return hashlib.sha1("\n".join(partes).encode("utf-8")).hexdigest()[:10]


VERSAO_REGRAS = _versao_regras()


def categoria_atual(a: dict) -> str | None:
    """Categoria do artigo. Cada artigo é classificado uma vez só e o resultado fica guardado
    (com a versão das regras); só volta a ser classificado se as regras mudarem."""
    if a.get("regras") != VERSAO_REGRAS:
        if "categoria_forcada" not in a:   # artigos muito antigos: a categoria guardada fazia de secção do feed
            a["categoria_forcada"] = a.get("categoria", "")
        a["categoria"] = classificar(a["titulo"], a.get("resumo", ""), a.get("cats_feed", []),
                                     a["categoria_forcada"], a.get("link", ""))
        a["regras"] = VERSAO_REGRAS
    return a["categoria"]

# --------------------------------------------------------------------------
# Recolha RSS
# --------------------------------------------------------------------------


def _texto(el, *nomes):
    for n in nomes:
        f = el.find(n)
        if f is not None and (f.text or "").strip():
            return f.text.strip()
    return ""


def _data(valor: str):
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
    r = obter(feed["url"])
    raiz = ET.fromstring(r.content)
    atom = "{http://www.w3.org/2005/Atom}"
    rdf = "{http://purl.org/rss/1.0/}"          # alguns jornais (ex.: CM) usam RSS 1.0
    dc = "{http://purl.org/dc/elements/1.1/}"
    itens = raiz.findall(".//item") or raiz.findall(f".//{rdf}item") or raiz.findall(f".//{atom}entry")
    artigos = []
    for it in itens:
        titulo = limpar_html(_texto(it, "title", f"{atom}title", f"{rdf}title"))
        link = _texto(it, "link", f"{rdf}link")
        if not link:
            l = it.find(f"{atom}link")
            link = l.get("href", "") if l is not None else ""
        resumo = limpar_html(_texto(it, "description", f"{rdf}description", f"{atom}summary", f"{atom}content"))[:3000]
        data = _data(_texto(it, "pubDate", f"{atom}published", f"{atom}updated",
                            "{http://purl.org/dc/elements/1.1/}date"))
        autor = limpar_html(_texto(it, f"{dc}creator", "author", f"{atom}author/{atom}name"))[:80]
        cats = [limpar_html(c.text or "") for c in it.findall("category")]
        cats += [limpar_html(c.text or "") for c in it.findall(f"{dc}subject")]
        cats += [c.get("term", "") for c in it.findall(f"{atom}category")]
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
            "cats_feed": [c for c in cats if c], "categoria_forcada": feed.get("categoria", ""),
            "_feed": feed["url"],      # só para o diagnóstico; não é guardado
        })
    return artigos


def _erro_feed(e: Exception) -> str:
    codigo = getattr(getattr(e, "response", None), "status_code", None)
    return f"erro HTTP {codigo}" if codigo else f"erro: {e.__class__.__name__}"


def _resultado(futuro) -> tuple:
    try:
        return ("ok", futuro.result())
    except Exception as e:
        return ("erro", e)


def recolher(config: dict, feeds: bool = True, capas: bool = True) -> tuple[list, dict, list]:
    """Lê os feeds e as capas ao mesmo tempo, num só conjunto de threads
    (antes as capas só começavam depois de todos os feeds terminarem)."""
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
    for feed, res in zip(feeds, resultados):
        if res[0] == "ok":
            a = res[1]
            todos += a
            estado[feed["url"]] = len(a)
            print(f"  ✓ {feed['fonte']:<22} {len(a):>3} artigos  {feed['url']}")
        else:
            e = res[1]
            estado[feed["url"]] = _erro_feed(e)
            print(f"  ✗ {feed['fonte']:<22} ERRO {e.__class__.__name__}: {str(e)[:80]}")
    return todos, estado


EXEMPLOS_POR_FEED = 5


def _registar_descarte(dg: dict, a: dict, motivo: str) -> None:
    """Guarda porque é que a notícia ficou de fora (contagem por motivo + alguns exemplos)."""
    tipo = motivo.split(":")[0]
    dg.setdefault("motivos", Counter())[tipo] += 1
    exemplos = dg.setdefault("exemplos_fora", [])
    if len(exemplos) < EXEMPLOS_POR_FEED:
        exemplos.append({"titulo": a["titulo"][:140], "motivo": motivo, "link": a.get("link", "")})


def _relatorio_diagnostico(diag: dict) -> None:
    """Mostra, para cada feed, o que aconteceu às notícias recebidas. Um feed com todas as
    notícias "antigas" está parado (o jornal deixou de o atualizar ou mudou de endereço)."""
    if not diag:
        return
    print("Diagnóstico dos feeds (recebidas → antigas / sem categoria / aceites · mais recente):")
    for url, d in diag.items():
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


def ler_capa(c: dict) -> dict:
    pagina = f"https://www.vercapas.com/capa/{c['slug']}.html"
    html = obter(pagina).text
    m = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+)', html) or \
        re.search(r'content=["\']([^"\']+)["\']\s+property=["\']og:image', html)
    if not m:
        m = re.search(r'(https://imgs\.vercapas\.com/covers/[^"\']+\.jpe?g)', html)
    if not m:
        raise ValueError("imagem não encontrada")
    miniatura = m.group(1)
    imagem = re.sub(r"/thumbc/\d+/", "/", miniatura)
    d = re.search(r"(\d{4}-\d{2}-\d{2})", imagem)
    return {"nome": c["nome"], "slug": c["slug"], "imagem": imagem, "miniatura": miniatura,
            "pagina": pagina, "data": d.group(1) if d else None}


def _relatorio_capas(lista: list, resultados: list) -> list:
    print("Capas:")
    capas = []
    for c, res in zip(lista, resultados):
        if res[0] == "ok":
            capas.append(res[1])
            print(f"  ✓ capa {c['nome']}")
        else:
            print(f"  ✗ capa {c['nome']}: {res[1]}")
    return capas

# --------------------------------------------------------------------------
# Agrupar em temas
# --------------------------------------------------------------------------


def _vetorizar(textos: list):
    vec = TfidfVectorizer(preprocessor=normalizar, stop_words=sorted(STOP_NORM), ngram_range=(1, 2),
                          sublinear_tf=True, token_pattern=r"(?u)\b[a-z0-9][a-z0-9\-]+\b")
    return vec, vec.fit_transform(textos)


def _agrupar(X, limiar: float, separar=None) -> np.ndarray:
    """Agrupa por semelhança de texto. `separar(dist)` pode marcar pares que nunca se devem juntar."""
    n = X.shape[0]
    if n == 1:
        return np.array([0])
    dist = np.clip(1 - cosine_similarity(X), 0, 2)
    if separar is not None:
        separar(dist, limiar)
    modelo = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                     distance_threshold=limiar)
    return modelo.fit_predict(dist)


def _palavras_chave(vec, X, idx, k=4) -> list:
    centro = np.asarray(X[idx].mean(axis=0)).ravel()
    if not hasattr(vec, "_nomes"):        # a lista de palavras é igual para todos os grupos: calcula-se uma vez
        vec._nomes = vec.get_feature_names_out()
    nomes = vec._nomes
    top = [nomes[i] for i in centro.argsort()[::-1][:k * 2] if centro[i] > 0]
    escolhidas = []
    for t in top:  # evita repetir "orcamento" e "orcamento estado"
        if not any(t in e or e in t for e in escolhidas):
            escolhidas.append(t)
    return escolhidas[:k]


def _categoria_maioritaria(membros: list, peso: str | None = None, preferida: str | None = None) -> str:
    c = Counter()
    for m in membros:
        c[m["categoria"]] += m[peso] if peso else 1
    # em caso de empate ganha a categoria da notícia principal do tema (a do título que aparece);
    # depois, a ordem: política, governo, economia, sociedade
    return max(NOTICIAS, key=lambda k: (c[k], k == preferida, -NOTICIAS.index(k)))


def sem_duplicados(artigos: list) -> list:
    """Quando um jornal corrige o título, o link muda mas a hora de publicação não:
    fica só a versão mais recente (a última guardada) de cada (jornal, hora)."""
    unicos = {}
    for a in artigos:
        unicos[(a.get("fonte"), a.get("data"))] = a
    return list(unicos.values())


def temas_do_dia(artigos: list) -> list:
    """Agrupa os artigos de um dia em temas e ordena por importância."""
    temas = []
    grupo, opinioes = [], []
    for a in sem_duplicados(artigos):
        # usa a categoria guardada; se as regras foram afinadas, reclassifica (também o histórico)
        cat = categoria_atual(a)
        if cat == "opiniao":
            opinioes.append(dict(a, categoria=cat))
        elif cat:
            grupo.append(dict(a, categoria=cat))
    if grupo:
        textos = [f"{a['titulo']} {a['titulo']} {a['resumo'][:200]}" for a in grupo]
        vec, X = _vetorizar(textos)
        rotulos = _agrupar(X, limiar=0.86)
        for r in set(rotulos):
            idx = [i for i, x in enumerate(rotulos) if x == r]
            membros = [grupo[i] for i in idx]
            fontes = sorted({m["fonte"] for m in membros})
            # título representativo: o mais parecido com os outros (preferindo jornais, não o Google)
            sim = cosine_similarity(X[idx]).mean(axis=1)
            ordem = sorted(range(len(idx)), key=lambda j: (membros[j]["fonte"].startswith("Google"), -sim[j]))
            principal = membros[ordem[0]]
            tema = {
                "titulo": principal["titulo"],
                "resumo": principal["resumo"],
                "categoria": _categoria_maioritaria(membros, preferida=principal["categoria"]),
                "palavras": _palavras_chave(vec, X, idx),
                "fontes": fontes,
                "n_fontes": len(fontes),
                "n_artigos": len(membros),
                "pontuacao": len(fontes) * 3 + len(membros),
                "artigos": sorted(
                    [{"titulo": m["titulo"], "link": m["link"], "fonte": m["fonte"], "data": m["data"]}
                     for m in membros], key=lambda m: m["data"], reverse=True)[:12],
            }
            if 2 * sum(e_rubrica(m["titulo"]) for m in membros) >= len(membros):
                tema["rubrica"] = True      # fica em Hoje, mas não entra na Semana/Mês
            temas.append(tema)
    temas.sort(key=lambda t: t["pontuacao"], reverse=True)
    # Opinião: cada artigo é um "tema" próprio, do mais recente para o mais antigo
    for a in sorted(opinioes, key=lambda a: a["data"], reverse=True):
        temas.append({
            "titulo": a["titulo"], "resumo": a.get("resumo", ""), "categoria": "opiniao",
            "autor": a.get("autor", ""), "data": a["data"], "palavras": [], "fontes": [a["fonte"]],
            "n_fontes": 1, "n_artigos": 1, "pontuacao": 0,
            "artigos": [{"titulo": a["titulo"], "link": a["link"], "fonte": a["fonte"], "data": a["data"]}],
        })
    return temas


# --------------------------------------------------------------------------
# Histórias da Semana/Mês: não juntar notícias "com o mesmo molde" sobre empresas diferentes
# ("Lucro do BCP sobe…", "Lucro do BPI cai…", "EDP registou lucro…" eram uma só história)
# --------------------------------------------------------------------------

_PALAVRA = re.compile(r"[\wÀ-ÿ&\-]+")
# Nomes que aparecem em muitas histórias diferentes (partidos, Governo, instituições, países): não servem para separar
_NAO_SEPARAM = {normalizar(n) for k in tendencias.CONHECIDOS for n in [k, *k.split()]} | {
    "ue", "eua", "g7", "g20", "onu", "nato", "europa", "portugal", "lisboa", "porto", "pr", "ar", "governo",
    "estado", "bce", "fmi", "ocde", "ine", "bdp", "pj", "mp", "oe", "oe2027", "parlamento", "conselho", "camara",
}


def _nomes_proprios(titulos: list) -> set:
    """Palavras que, a meio do título, aparecem (quase) sempre com maiúscula: nomes de empresas, pessoas, sítios."""
    total, maiusc = Counter(), Counter()
    for t in titulos:
        for k, w in enumerate(_PALAVRA.findall(t)):
            if k == 0:
                continue
            n = normalizar(w)
            total[n] += 1
            maiusc[n] += w[0].isupper()
    return {n for n in total if maiusc[n] >= 0.8 * total[n]}


def _entidades(texto: str, proprios: set) -> set:
    out = set()
    for w in _PALAVRA.findall(texto):
        n = normalizar(w)
        if len(n) < 2 or n in STOP_NORM or n in _NAO_SEPARAM or w.isdigit():
            continue
        if (w.isupper() and len(w) >= 2) or (w[0].isupper() and n in proprios):
            out.add(n)
    return out


def _separar_moldes(temas: list):
    """Devolve a função que impede juntar dois temas quando o "sujeito" do título (as primeiras palavras)
    é uma entidade diferente e não têm nenhuma entidade em comum. Ex.: "Lucro do BCP…" vs "Lucro do BPI…"."""
    titulos = [a["titulo"] for t in temas for a in t["artigos"]]
    proprios = _nomes_proprios(titulos)
    sujeito = [_entidades(" ".join(_PALAVRA.findall(t["titulo"])[:4]), proprios) for t in temas]
    todas = [set().union(*(_entidades(a["titulo"], proprios) for a in t["artigos"][:8]), sujeito[i])
             for i, t in enumerate(temas)]

    def separar(dist, limiar):
        for i, j in np.argwhere(dist < limiar):
            if i < j and sujeito[i] and sujeito[j] and not (todas[i] & todas[j]):
                dist[i, j] = dist[j, i] = 10.0   # com a ligação "average", 10 impede a junção
    return separar


def _versao_temas() -> str:
    """Versão das regras + do agrupamento dos temas do dia. Quando muda, os temas de todos os dias
    guardados são refeitos (senão a Semana e o Mês continuavam com os temas antigos)."""
    partes = [VERSAO_REGRAS] + [r.pattern for r in RUBRICAS]
    partes += [inspect.getsource(f) for f in (_vetorizar, _agrupar, _palavras_chave, _categoria_maioritaria,
                                              sem_duplicados, e_rubrica, temas_do_dia)]
    return hashlib.sha1("\n".join(partes).encode("utf-8")).hexdigest()[:10]


def temas_do_periodo(dias: dict) -> list:
    """Junta os temas de vários dias em 'histórias' (semana / mês)."""
    lista = [dict(t, dia=d) for d, ts in dias.items() for t in ts
             if t["categoria"] in NOTICIAS and (t["n_fontes"] >= 2 or t["n_artigos"] >= 3) and not t.get("rubrica")]
    if not lista:
        return []
    historias = []
    grupo = lista
    if grupo:
        textos = [" ".join([t["titulo"]] * 2 + [a["titulo"] for a in t["artigos"][:6]] + t["palavras"] * 2)
                  for t in grupo]
        vec, X = _vetorizar(textos)
        rotulos = _agrupar(X, limiar=0.80, separar=_separar_moldes(grupo))
        for r in set(rotulos):
            membros = [grupo[i] for i, x in enumerate(rotulos) if x == r]
            idx = [i for i, x in enumerate(rotulos) if x == r]
            membros.sort(key=lambda t: t["pontuacao"], reverse=True)
            dias_presentes = sorted({m["dia"] for m in membros})
            fontes = sorted({f for m in membros for f in m["fontes"]})
            linha = {}
            for m in membros:
                if m["dia"] not in linha or m["pontuacao"] > linha[m["dia"]]["pontuacao"]:
                    linha[m["dia"]] = {"dia": m["dia"], "titulo": m["titulo"], "pontuacao": m["pontuacao"]}
            artigos, vistos = [], set()
            for m in membros:
                for a in m["artigos"]:
                    if a["link"] not in vistos:
                        vistos.add(a["link"])
                        artigos.append(a)
            historias.append({
                "titulo": membros[0]["titulo"],
                "resumo": membros[0].get("resumo", ""),
                "categoria": _categoria_maioritaria(membros, peso="pontuacao", preferida=membros[0]["categoria"]),
                "palavras": _palavras_chave(vec, X, idx),
                "dias": dias_presentes,
                "n_dias": len(dias_presentes),
                "fontes": fontes,
                "n_fontes": len(fontes),
                "n_artigos": sum(m["n_artigos"] for m in membros),
                "pontuacao": sum(m["pontuacao"] for m in membros) + 5 * (len(dias_presentes) - 1),
                "cronologia": sorted(linha.values(), key=lambda x: x["dia"]),
                "artigos": sorted(artigos, key=lambda a: a["data"], reverse=True)[:15],
            })
    historias.sort(key=lambda h: h["pontuacao"], reverse=True)
    return historias

# --------------------------------------------------------------------------
# Guardar / carregar
# --------------------------------------------------------------------------


def ler_json(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return padrao


def escrever_json(caminho: Path, dados, compacto=False):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=None if compacto else 1), encoding="utf-8")


def resumo_publico(temas: list, por_categoria: int) -> dict:
    return {cat: [t for t in temas if t["categoria"] == cat][:por_categoria] for cat in CATEGORIAS}


DIAS_A_GUARDAR = 30


def apagar_antigos(hoje) -> None:
    """Apaga notícias, temas e capas com mais de DIAS_A_GUARDAR dias."""
    limite = (hoje - timedelta(days=DIAS_A_GUARDAR)).isoformat()
    apagados = 0
    for pasta in (DADOS / "artigos", DADOS / "temas", DADOS / "capas", DADOS / "descartados", SITE / "dias"):
        for f in pasta.glob("*.json"):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem) and f.stem < limite:
                f.unlink()
                apagados += 1
    if apagados:
        print(f"Limpeza: {apagados} ficheiros com mais de {DIAS_A_GUARDAR} dias apagados")


@contextmanager
def etapa(nome: str, tempos: list):
    """Mede quanto tempo demora cada parte da atualização (aparece no fim do registo do Actions)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        tempos.append((nome, time.perf_counter() - t0))


def main(artigos_teste: list | None = None, capas_teste: list | None = None):
    inicio_total = time.perf_counter()
    tempos = []
    config = ler_json(RAIZ / "fontes.json", None)
    agora = datetime.now(LISBOA)
    hoje = agora.date().isoformat()
    print(f"== Atualização {agora:%Y-%m-%d %H:%M} (Lisboa) ==")

    # 1. Recolher feeds e capas (ao mesmo tempo)
    with etapa("recolha", tempos):
        novos, estado, capas = recolher(config, feeds=artigos_teste is None, capas=capas_teste is None)
    if artigos_teste is not None:
        novos, estado = artigos_teste, {"teste": len(artigos_teste)}
    if capas_teste is not None:
        capas = capas_teste

    # 2. Classificar e juntar ao histórico de cada dia (os feeds só guardam as últimas horas,
    #    por isso o script corre várias vezes por dia e vai acumulando)
    with etapa("classificação e temas do dia", tempos):
        limite = agora - timedelta(days=2)
        por_dia, descartadas = {}, {}
        nomes = {f["url"]: f["fonte"] for f in config["feeds"]}
        diag = {url: {"fonte": nomes.get(url, url), "recebidas": n, "antigas": 0, "sem_categoria": 0, "aceites": 0,
                      "mais_recente": None}
                for url, n in estado.items() if isinstance(n, int) and url in nomes}
        diag.update({url: {"fonte": nomes[url], "erro": v} for url, v in estado.items()
                     if isinstance(v, str) and url in nomes})
        for a in novos:
            a = dict(a)
            dg = diag.get(a.pop("_feed", None))
            d = datetime.fromisoformat(a["data"]).astimezone(LISBOA)
            if dg is not None and (dg["mais_recente"] is None or d > datetime.fromisoformat(dg["mais_recente"])):
                dg["mais_recente"] = d.isoformat(timespec="minutes")
            if d < limite:
                if dg is not None:
                    dg["antigas"] += 1
                continue
            if not categoria_atual(a):
                motivo = classificar_com_motivo(a["titulo"], a.get("resumo", ""), a.get("cats_feed", []),
                                                a.get("categoria_forcada", ""), a.get("link", ""))[1]
                if dg is not None:
                    dg["sem_categoria"] += 1
                    _registar_descarte(dg, a, motivo)
                descartadas.setdefault(d.date().isoformat(), []).append({
                    "titulo": a["titulo"], "link": a["link"], "fonte": a["fonte"], "data": a["data"],
                    "resumo": a.get("resumo", "")[:300], "cats_feed": a.get("cats_feed", []),
                    "categoria_forcada": a.get("categoria_forcada", ""), "motivo": motivo})
                continue
            if dg is not None:
                dg["aceites"] += 1
            por_dia.setdefault(d.date().isoformat(), []).append(a)
        _relatorio_diagnostico(diag)
        # As notícias descartadas ficam guardadas 30 dias (não aparecem na app): servem para afinar as regras
        for dia, lista in descartadas.items():
            f = DADOS / "descartados" / f"{dia}.json"
            existentes = {a["link"]: a for a in ler_json(f, [])}
            for a in lista:
                existentes[a["link"]] = a          # com o motivo das regras atuais
            escrever_json(f, list(existentes.values()), compacto=True)

        dias_tocados = set(por_dia) | {hoje}
        f_versao = DADOS / "versao_temas.txt"
        versao = _versao_temas()
        refazer = f_versao.exists() is False or f_versao.read_text(encoding="utf-8").strip() != versao
        if refazer:   # as regras ou o agrupamento mudaram: refaz os temas de todo o histórico (uma vez)
            guardar_desde = (agora.date() - timedelta(days=DIAS_A_GUARDAR)).isoformat()
            antigos = {p.stem for p in (DADOS / "artigos").glob("*.json") if p.stem >= guardar_desde}
            print(f"Regras novas: a refazer os temas de {len(antigos | dias_tocados)} dias")
            dias_tocados |= antigos
            # notícias descartadas antes que, com as regras novas, já entram
            recuperadas = 0
            for f in (DADOS / "descartados").glob("*.json"):
                guardadas = ler_json(f, [])
                ficam = []
                for a in guardadas:
                    novo = {k: v for k, v in a.items() if k != "motivo"}
                    novo.setdefault("autor", "")
                    if categoria_atual(novo):
                        por_dia.setdefault(f.stem, []).append(novo)
                        dias_tocados.add(f.stem)
                        recuperadas += 1
                    else:
                        ficam.append(a)
                if len(ficam) < len(guardadas):
                    escrever_json(f, ficam, compacto=True)
            if recuperadas:
                print(f"  {recuperadas} notícias descartadas passam a entrar com as regras novas")
        for dia in sorted(dias_tocados):
            f = DADOS / "artigos" / f"{dia}.json"
            existentes = {a["link"]: a for a in ler_json(f, [])}
            for a in por_dia.get(dia, []):
                existentes.setdefault(a["link"], a)
            artigos_dia = sem_duplicados(list(existentes.values()))
            for a in artigos_dia:
                categoria_atual(a)          # só classifica os novos (ou todos, se as regras mudaram)
            escrever_json(f, artigos_dia)
            temas = temas_do_dia(artigos_dia)
            escrever_json(DADOS / "temas" / f"{dia}.json", temas)
            if not refazer or dia in por_dia or dia == hoje:
                print(f"Dia {dia}: {len(artigos_dia)} artigos → {len(temas)} temas")
        if refazer and artigos_teste is None:
            f_versao.parent.mkdir(parents=True, exist_ok=True)
            f_versao.write_text(versao + "\n", encoding="utf-8")

    # 3. Capas do dia
    f_capas = DADOS / "capas" / f"{hoje}.json"
    if capas:
        escrever_json(f_capas, capas)
    capas_hoje = ler_json(f_capas, [])
    recente = (agora.date() - timedelta(days=8)).isoformat()
    capas_validas = [c for c in capas_hoje if not c.get("data") or c["data"] >= recente]
    if len(capas_validas) != len(capas_hoje):
        escrever_json(f_capas, capas_validas)
        capas_hoje = capas_validas

    # 4. Limpeza: só se guardam os últimos DIAS_A_GUARDAR dias (chega para a vista Mês)
    apagar_antigos(agora.date())

    # 5. Ficheiros para o site
    todos_dias = sorted(p.stem for p in (DADOS / "temas").glob("*.json"))
    for dia in dias_tocados:
        temas = ler_json(DADOS / "temas" / f"{dia}.json", [])
        escrever_json(SITE / "dias" / f"{dia}.json", {
            "dia": dia,
            "capas": ler_json(DADOS / "capas" / f"{dia}.json", []),
            "temas": resumo_publico(temas, 15),
            "n_artigos": sum(t["n_artigos"] for t in temas),
        }, compacto=True)

    # A semana está dentro do mês: cada dia é lido (e, se preciso, classificado) uma vez só
    temas_cache, validos_cache = {}, {}

    def temas_de(d: str) -> list:
        if d not in temas_cache:
            temas_cache[d] = ler_json(DADOS / "temas" / f"{d}.json", [])
        return temas_cache[d]

    def validos_de(d: str) -> list:
        """Artigos com categoria (os que contam para "quem está nas notícias")."""
        if d not in validos_cache:
            f = DADOS / "artigos" / f"{d}.json"
            artigos = ler_json(f, [])
            por_classificar = any(a.get("regras") != VERSAO_REGRAS for a in artigos)
            validos_cache[d] = [a for a in artigos if categoria_atual(a)]
            if por_classificar:
                escrever_json(f, artigos)   # guarda a classificação para não a repetir na próxima vez
        return validos_cache[d]

    for nome, n in (("semana", 7), ("mes", 30)):
        with etapa(nome, tempos):
            inicio = agora.date() - timedelta(days=n - 1)
            janela = [(inicio + timedelta(days=i)).isoformat() for i in range(n)]   # todos os dias, mesmo sem dados
            com_dados = [d for d in todos_dias if d >= janela[0]]
            dias = {d: temas_de(d) for d in com_dados}
            historias = temas_do_periodo(dias)
            tendencias.acrescentar_series(historias, janela)
            alta = tendencias.em_alta(historias, com_dados) if nome == "semana" else []
            artigos_janela = {d: [] for d in janela}
            for d in com_dados:
                artigos_janela[d] = validos_de(d)
            pessoas = tendencias.quem_esta_nas_noticias(artigos_janela, 15)
            opinioes = sorted((t for ts in dias.values() for t in ts if t["categoria"] == "opiniao"),
                              key=lambda t: t.get("data", ""), reverse=True)[:20]
            publico = resumo_publico(historias, 15)
            publico["opiniao"] = opinioes
            escrever_json(SITE / f"{nome}.json", {
                "de": janela[0], "ate": hoje, "n_dias": len(com_dados), "dias": janela,
                "temas": publico, "em_alta": alta, "pessoas": pessoas,
            }, compacto=True)
            print(f"{nome}: {len(com_dados)} dias → {len(historias)} histórias, {len(alta)} em alta, "
                  f"{len(pessoas)} entidades")

    # 6. Indicadores económicos (uma vez por dia chega: os dados são mensais/trimestrais)
    f_ind = SITE / "indicadores.json"
    atual = ler_json(f_ind, {})
    if atual.get("obtido") != hoje and artigos_teste is None:
        with etapa("indicadores", tempos):
            print("Indicadores:")
            lista = indicadores.recolher()
            if lista:
                escrever_json(f_ind, {"obtido": hoje, "fonte": "Eurostat", "indicadores": lista})

    escrever_json(SITE / "indice.json", {
        "atualizado": agora.isoformat(timespec="minutes"),
        "hoje": hoje,
        "dias": sorted(p.stem for p in (SITE / "dias").glob("*.json")),
        "fontes": estado,
        "diagnostico": diag,
        "n_capas": len(capas_hoje),
        "nomes_fontes": sorted({f["fonte"] for f in config["feeds"]}),
        "nomes_capas": [c["nome"] for c in config["capas"]],
    })
    tempos.append(("total", time.perf_counter() - inicio_total))
    print("Tempos: " + " · ".join(f"{nome} {seg:.1f}s" for nome, seg in tempos))
    print("Concluído.")


if __name__ == "__main__":
    sys.exit(main())
