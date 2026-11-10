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
import heapq
import inspect
import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.sparse import csr_matrix        # já vem com o scikit-learn (as matrizes TF-IDF são deste tipo)
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
    """Minúsculas e sem acentos. O texto só com ASCII (muito comum) não precisa de ser percorrido."""
    texto = unicodedata.normalize("NFKD", texto.lower())
    if texto.isascii():
        return texto
    return "".join(c for c in texto if not unicodedata.combining(c))


@lru_cache(maxsize=None)
def _normalizar_palavra(palavra: str) -> str:
    """normalizar() com memória, para palavras soltas (repetem-se milhares de vezes nos títulos)."""
    return normalizar(palavra)


_RE_ETIQUETA = re.compile(r"<[^>]+>")
_RE_ESPACOS = re.compile(r"\s+")


def limpar_html(texto: str) -> str:
    texto = _RE_ETIQUETA.sub(" ", unescape(texto or ""))
    return _RE_ESPACOS.sub(" ", texto).strip()


STOP_NORM = {normalizar(p) for p in STOPWORDS}
_STOP_LISTA = sorted(STOP_NORM)      # o TfidfVectorizer recebe sempre a mesma lista: prepara-se uma vez

# --------------------------------------------------------------------------
# Pesquisa rápida de termos
# --------------------------------------------------------------------------

_RE_PALAVRA_NORM = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=None)
def _padrao(termo: str) -> re.Pattern:
    """Termo inteiro (não colado a letras ou dígitos) num texto normalizado."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(normalizar(termo)) + r"(?![a-z0-9])")


class TextoNorm:
    """Texto já normalizado, com o conjunto das suas palavras (calculado só quando é preciso).

    Um termo de uma só palavra aparece no texto se e só se for uma dessas palavras, por isso
    a verificação passa a ser uma pesquisa O(1) num set, em vez de uma expressão regular.
    """

    __slots__ = ("texto", "_palavras")

    def __init__(self, texto: str) -> None:
        self.texto = texto
        self._palavras: frozenset[str] | None = None

    @property
    def palavras(self) -> frozenset[str]:
        if self._palavras is None:
            self._palavras = frozenset(_RE_PALAVRA_NORM.findall(self.texto))
        return self._palavras


def _como_texto(texto: "str | TextoNorm") -> TextoNorm:
    return texto if isinstance(texto, TextoNorm) else TextoNorm(texto)


class ListaTermos:
    """Lista de termos pesquisados como palavras inteiras, com o mesmo resultado que testar
    cada termo com _padrao(), mas sem percorrer a lista toda com expressões regulares:
    - termos de uma palavra: interseção de sets;
    - termos compostos ("fc porto", "tras-os-montes"): só se testa a regex quando todas
      as palavras do termo estão no texto.
    A ordem da lista conta: primeiro() devolve o primeiro termo da lista que aparece."""

    __slots__ = ("termos", "_simples", "_chaves", "_compostos")

    def __init__(self, termos: Iterable[str]) -> None:
        self.termos = list(termos)
        self._simples: dict[str, list[int]] = {}   # palavra normalizada -> posições na lista
        self._compostos: list[tuple[int, frozenset[str], re.Pattern]] = []
        for i, termo in enumerate(self.termos):
            n = normalizar(termo)
            if _RE_PALAVRA_NORM.fullmatch(n):
                self._simples.setdefault(n, []).append(i)
            else:
                self._compostos.append((i, frozenset(_RE_PALAVRA_NORM.findall(n)), _padrao(termo)))
        self._chaves = frozenset(self._simples)

    def _posicoes(self, texto: TextoNorm) -> Iterator[int]:
        """Posições (na lista) de todos os termos que aparecem no texto, sem ordem definida."""
        palavras = texto.palavras
        for p in palavras & self._chaves:
            yield from self._simples[p]
        for i, partes, padrao in self._compostos:
            if partes <= palavras and padrao.search(texto.texto):
                yield i

    def primeiro(self, texto: TextoNorm) -> str | None:
        melhor = min((self._simples[p][0] for p in texto.palavras & self._chaves), default=len(self.termos))
        for i, partes, padrao in self._compostos:      # estão por ordem da lista
            if i >= melhor:
                break
            if partes <= texto.palavras and padrao.search(texto.texto):
                melhor = i
                break
        return self.termos[melhor] if melhor < len(self.termos) else None

    def algum(self, texto: TextoNorm) -> bool:
        return next(self._posicoes(texto), None) is not None

    def contar(self, texto: TextoNorm) -> int:
        """Quantos termos da lista aparecem (um termo repetido na lista conta as vezes que lá está)."""
        return sum(1 for _ in self._posicoes(texto))


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
        "ministerio publico", "acusacao", "arguidos", "arguido", "acusados", "detido", "detidos", "policia", "psp",
        "gnr", "crime", "incendio", "incendios", "ambiente", "clima",
        "ciencia", "artes", "cinema", "musica", "livros", "literatura", "teatro", "museu", "exposicao",
        "festival", "patrimonio", "religiao", "igreja", "imigracao", "imigrantes", "aima", "seguranca social",
        "transportes", "comboios", "cp", "metro", "aeroporto", "local", "lisboa", "porto",
    ],
}

# Notícias sobre outros países: descartadas, a não ser que mencionem Portugal
ESTRANGEIRO = [
    "eua", "estados unidos", "trump", "casa branca", "washington", "biden", "china", "chines", "chinesa", "chineses",
    "chinesas", "americano", "americana", "norte-americano", "norte-americana", "pequim",
    "russia", "russo", "russa", "putin", "moscovo", "kremlin", "ucrania", "ucraniano", "kiev", "zelensky",
    "israel", "israelita", "gaza", "hamas", "netanyahu", "irao", "iraniano", "libano", "siria", "medio oriente",
    "brasil", "brasileiro", "brasileira", "brasileiros", "brasileiras", "lula", "bolsonaro", "venezuela", "colombia",
    "argentina", "mexico",
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

# As listas preparadas para pesquisa rápida (ver ListaTermos)
_EXCLUIR = ListaTermos(EXCLUIR)
_EXCLUIR_SE_ECONOMIA = ListaTermos(EXCLUIR_SE_ECONOMIA)
_SOCIEDADE_SE_NADA_MAIS = ListaTermos(SOCIEDADE_SE_NADA_MAIS)
_ECONOMIA_SE_NADA_MAIS = ListaTermos(ECONOMIA_SE_NADA_MAIS)
_PALAVRAS = {cat: ListaTermos(termos) for cat, termos in PALAVRAS.items()}
_ESTRANGEIRO = ListaTermos(ESTRANGEIRO)
_PORTUGAL = ListaTermos(PORTUGAL)
_LUGARES = ListaTermos(LUGARES)
_EXCLUIR_TITULO = ListaTermos(EXCLUIR_TITULO)
_OPINIAO_CATS = ListaTermos(OPINIAO_CATS)
_SECCOES_NOTICIAS = ListaTermos(["politica", "economia", "sociedade", "pais", "cultura"])
_SECCOES_MUNDO = ListaTermos(["mundo", "internacional", "globo"])


def _e_opiniao(link_min: str, cats: TextoNorm) -> bool:
    return any(p in link_min for p in OPINIAO_LINK) or _OPINIAO_CATS.algum(cats)


def e_opiniao(link: str, cats_feed: list) -> bool:
    return _e_opiniao((link or "").lower(), TextoNorm(normalizar(" | ".join(cats_feed or []))))


def _termo_estrangeiro(titulo: TextoNorm, contexto: Callable[[], TextoNorm]) -> str | None:
    termo = _ESTRANGEIRO.primeiro(titulo)
    if termo is None:
        return None
    return None if tem_ligacao_a_portugal(contexto()) else termo


def termo_estrangeiro(titulo: str, resumo: str) -> str | None:
    """O país/lugar estrangeiro no título, se a notícia não tiver ligação a Portugal; senão None."""
    return _termo_estrangeiro(TextoNorm(normalizar(titulo)),
                              lambda: TextoNorm(normalizar(f"{titulo} {resumo[:300]}")))


def tem_ligacao_a_portugal(texto_norm: "str | TextoNorm") -> bool:
    texto = _como_texto(texto_norm)
    return _PORTUGAL.algum(texto) or _LUGARES.algum(texto)


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
_RUBRICA = re.compile("|".join(f"(?:{r.pattern})" for r in RUBRICAS))   # uma só pesquisa em vez de 12


def e_rubrica(titulo: str) -> bool:
    return _RUBRICA.search(normalizar(titulo)) is not None


SIGLAS_POLITICA = {"PS", "PSD", "CDS", "IL", "BE", "PCP", "PAN", "JPP", "Livre", "Chega", "AD", "OE"}
_RE_SIGLA = re.compile(r"[A-Za-zÀ-ÿ]+")


def classificar(titulo: str, resumo: str, cats_feed: list, categoria_forcada: str, link: str = "") -> str | None:
    """Devolve uma das CATEGORIAS ou None (descartar)."""
    return classificar_com_motivo(titulo, resumo, cats_feed, categoria_forcada, link)[0]


def classificar_com_motivo(titulo: str, resumo: str, cats_feed: list, categoria_forcada: str,
                           link: str = "") -> tuple[str | None, str]:
    """(categoria, motivo). Quando a notícia é descartada (categoria None), o motivo diz porquê.
    Cada texto é normalizado uma só vez; o contexto (título + início do resumo) só quando é preciso."""
    cats = TextoNorm(normalizar(" | ".join(cats_feed)))
    link_min = (link or "").lower()
    termo = next((p for p in EXCLUIR_LINK if p in link_min), None)
    if termo:
        return None, f"endereço: {termo}"
    titulo_n = TextoNorm(normalizar(titulo))
    guardado: list[TextoNorm] = []

    def contexto() -> TextoNorm:
        if not guardado:
            guardado.append(TextoNorm(normalizar(f"{titulo} {resumo[:300]}")))
        return guardado[0]

    if _e_opiniao(link_min, cats):
        termo = _EXCLUIR_TITULO.primeiro(titulo_n)
        if termo:
            return None, f"título: {termo}"
        termo = _termo_estrangeiro(titulo_n, contexto)
        if termo:
            return None, f"outro país: {termo}"
        return "opiniao", "opinião"
    # Descartar desporto, internacional, etc. (pelas categorias do próprio jornal)
    if cats_feed:
        excluir = _EXCLUIR_SE_ECONOMIA if categoria_forcada == "economia" else _EXCLUIR
        termo = excluir.primeiro(cats)
        if termo and not _SECCOES_NOTICIAS.algum(cats):
            return None, f"secção do jornal: {termo}"
    termo = _EXCLUIR_TITULO.primeiro(titulo_n)
    if termo:
        return None, f"título: {termo}"
    termo = _termo_estrangeiro(titulo_n, contexto)
    if termo:
        return None, f"outro país: {termo}"
    # secção "Mundo"/"Internacional" do próprio jornal, sem ligação a Portugal
    if cats_feed and _SECCOES_MUNDO.algum(cats) and not tem_ligacao_a_portugal(contexto()):
        return None, "secção Mundo, sem ligação a Portugal"

    texto = TextoNorm(normalizar(f"{titulo} {resumo}"))
    pontos = Counter()
    if categoria_forcada in NOTICIAS:
        pontos[categoria_forcada] += 2   # a secção do feed ajuda, mas o conteúdo pode vencer
    if _PALAVRAS["governo"].algum(titulo_n):
        pontos["governo"] += 2           # "Governo aprova...", "Ministra da Saúde..." no título
    for cat, termos in _PALAVRAS.items():
        n = termos.contar(texto) + (3 * termos.contar(cats) if cats_feed else 0)
        if n:
            pontos[cat] += n
    if governo_e_sujeito(titulo_n.texto):
        pontos["governo"] += 6           # "Governo aprova...", "Ministra do Trabalho diz..." ganha à secção do feed
    if not SIGLAS_POLITICA.isdisjoint(_RE_SIGLA.findall(titulo)):
        pontos["politica"] += 2
    if not pontos:
        # sem palavras de nenhuma categoria: crime, justiça, acidentes, saúde, autarquias…
        if _ECONOMIA_SE_NADA_MAIS.algum(texto):
            return "economia", "palavras de economia (último recurso)"
        if _SOCIEDADE_SE_NADA_MAIS.algum(texto):
            return "sociedade", "palavras de sociedade (último recurso)"
        # …ou passada numa terra portuguesa ("…em Quarteira", "…no Alentejo")
        if _LUGARES.algum(contexto()):
            return "sociedade", "terra portuguesa"
        return None, "sem palavras de nenhuma categoria"
    return pontos.most_common(1)[0][0], "palavras-chave"


def _versao_regras() -> str:
    """Impressão digital das regras de classificação (listas de palavras + código das funções).
    Muda sempre que alguém afina as regras, e aí todo o histórico volta a ser classificado."""
    listas = [EXCLUIR, EXCLUIR_SE_ECONOMIA, LUGARES, SOCIEDADE_SE_NADA_MAIS, ECONOMIA_SE_NADA_MAIS, PALAVRAS,
              ESTRANGEIRO, PORTUGAL, EXCLUIR_TITULO, OPINIAO_LINK, EXCLUIR_LINK, OPINIAO_CATS,
              sorted(SIGLAS_POLITICA), NOTICIAS, GOVERNO_SUJEITO.pattern]
    partes = [json.dumps(listas, ensure_ascii=False)]
    # das classes entram os métodos (o código de uma função encontra-se sempre, o de uma classe nem sempre)
    metodos = [f for classe in (TextoNorm, ListaTermos) for f in vars(classe).values() if inspect.isfunction(f)]
    metodos.append(TextoNorm.palavras.fget)
    partes += [inspect.getsource(f) for f in (normalizar, _padrao, *metodos, _e_opiniao, _termo_estrangeiro,
                                              tem_ligacao_a_portugal, governo_e_sujeito, classificar_com_motivo)]
    return hashlib.sha1("\n".join(partes).encode("utf-8")).hexdigest()[:10]


VERSAO_REGRAS = _versao_regras()


def _classificar(a: dict) -> str:
    """Classifica o artigo, guarda a categoria e a versão das regras e devolve o motivo."""
    if "categoria_forcada" not in a:   # artigos muito antigos: a categoria guardada fazia de secção do feed
        a["categoria_forcada"] = a.get("categoria", "")
    a["categoria"], motivo = classificar_com_motivo(a["titulo"], a.get("resumo", ""), a.get("cats_feed", []),
                                                    a["categoria_forcada"], a.get("link", ""))
    a["regras"] = VERSAO_REGRAS
    return motivo


def categoria_atual(a: dict) -> str | None:
    """Categoria do artigo. Cada artigo é classificado uma vez só e o resultado fica guardado
    (com a versão das regras); só volta a ser classificado se as regras mudarem."""
    if a.get("regras") != VERSAO_REGRAS:
        _classificar(a)
    return a["categoria"]

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

# --------------------------------------------------------------------------
# Agrupar em temas
# --------------------------------------------------------------------------


def _vetorizar(textos: list):
    vec = TfidfVectorizer(preprocessor=normalizar, stop_words=_STOP_LISTA, ngram_range=(1, 2),
                          sublinear_tf=True, token_pattern=r"(?u)\b[a-z0-9][a-z0-9\-]+\b")
    return vec, vec.fit_transform(textos)


def _agrupar(dist: np.ndarray, limiar: float, separar=None) -> np.ndarray:
    """Agrupa por distância de texto (1 - semelhança). `separar(dist)` pode marcar pares que nunca se devem juntar."""
    if separar is not None:
        separar(dist, limiar)
    modelo = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                     distance_threshold=limiar)
    return modelo.fit_predict(dist)


def _distancias(semelhanca: np.ndarray, reutilizar: bool = False) -> np.ndarray:
    """1 - semelhança, limitado a [0, 2]. Com reutilizar=True escreve por cima da matriz recebida
    (poupa duas cópias n×n quando a semelhança já não é precisa)."""
    dist = np.subtract(1, semelhanca, out=semelhanca if reutilizar else None)
    return np.clip(dist, 0, 2, out=dist)


def _grupos(rotulos: np.ndarray) -> list[list[int]]:
    """Índices de cada grupo numa só passagem, pela ordem dos rótulos (0, 1, 2…)."""
    grupos: dict[int, list[int]] = {}
    for i, r in enumerate(rotulos.tolist()):
        grupos.setdefault(r, []).append(i)
    return [grupos[r] for r in sorted(grupos)]


def _centros(X, grupos: list[list[int]]) -> csr_matrix:
    """A média das linhas de cada grupo (uma linha por grupo), numa só multiplicação de matrizes
    esparsas. Dá exatamente os mesmos valores que X[idx].mean(axis=0) grupo a grupo, sem copiar linhas."""
    tamanhos = [len(g) for g in grupos]
    linhas = np.repeat(np.arange(len(grupos)), tamanhos)
    colunas = np.fromiter((i for g in grupos for i in g), dtype=np.intp, count=len(linhas))
    pesos = np.repeat([1.0 / t for t in tamanhos], tamanhos)
    return csr_matrix((pesos, (linhas, colunas)), shape=(len(grupos), X.shape[0])) @ X


def _palavras_chave(nomes: np.ndarray, centros: csr_matrix, r: int, k: int = 4) -> list:
    """Os k termos com mais peso no centro do grupo r (sem repetir "orcamento" e "orcamento estado")."""
    centro = np.zeros(centros.shape[1])
    inicio, fim = centros.indptr[r], centros.indptr[r + 1]
    centro[centros.indices[inicio:fim]] = centros.data[inicio:fim]
    top = [str(nomes[i]) for i in centro.argsort()[::-1][:k * 2] if centro[i] > 0]
    escolhidas = []
    for t in top:
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
    return list({(a.get("fonte"), a.get("data")): a for a in artigos}.values())


def _resumo_artigo(a: dict) -> dict:
    return {"titulo": a["titulo"], "link": a["link"], "fonte": a["fonte"], "data": a["data"]}


def temas_do_dia(artigos: list) -> list:
    """Agrupa os artigos de um dia em temas e ordena por importância."""
    temas = []
    grupo, opinioes = [], []
    for a in sem_duplicados(artigos):
        # usa a categoria guardada; se as regras foram afinadas, reclassifica (também o histórico)
        cat = categoria_atual(a)
        if cat == "opiniao":
            opinioes.append(a)
        elif cat:
            grupo.append(a)
    if grupo:
        textos = [f"{a['titulo']} {a['titulo']} {a['resumo'][:200]}" for a in grupo]
        vec, X = _vetorizar(textos)
        nomes = vec.get_feature_names_out()
        if len(grupo) > 1:
            # a matriz de semelhança serve para agrupar e, depois, para escolher o título de cada tema
            semelhanca = cosine_similarity(X)
            rotulos = _agrupar(_distancias(semelhanca), limiar=0.86)
        else:
            semelhanca, rotulos = None, np.array([0])
        grupos = _grupos(rotulos)
        centros = _centros(X, grupos)
        for r, idx in enumerate(grupos):
            membros = [grupo[i] for i in idx]
            fontes = sorted({m["fonte"] for m in membros})
            # título representativo: o mais parecido com os outros (preferindo jornais, não o Google)
            if len(idx) > 1:
                sim = semelhanca[np.ix_(idx, idx)].mean(axis=1)
                ordem = sorted(range(len(idx)), key=lambda j: (membros[j]["fonte"].startswith("Google"), -sim[j]))
                principal = membros[ordem[0]]
            else:
                principal = membros[0]
            tema = {
                "titulo": principal["titulo"],
                "resumo": principal["resumo"],
                "categoria": _categoria_maioritaria(membros, preferida=principal["categoria"]),
                "palavras": _palavras_chave(nomes, centros, r),
                "fontes": fontes,
                "n_fontes": len(fontes),
                "n_artigos": len(membros),
                "pontuacao": len(fontes) * 3 + len(membros),
                "artigos": [_resumo_artigo(m) for m in heapq.nlargest(12, membros, key=lambda m: m["data"])],
            }
            if 2 * sum(map(e_rubrica, (m["titulo"] for m in membros))) >= len(membros):
                tema["rubrica"] = True      # fica em Hoje, mas não entra na Semana/Mês
            temas.append(tema)
    temas.sort(key=lambda t: t["pontuacao"], reverse=True)
    # Opinião: cada artigo é um "tema" próprio, do mais recente para o mais antigo
    for a in sorted(opinioes, key=lambda a: a["data"], reverse=True):
        temas.append({
            "titulo": a["titulo"], "resumo": a.get("resumo", ""), "categoria": "opiniao",
            "autor": a.get("autor", ""), "data": a["data"], "palavras": [], "fontes": [a["fonte"]],
            "n_fontes": 1, "n_artigos": 1, "pontuacao": 0, "artigos": [_resumo_artigo(a)],
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
        for w in _PALAVRA.findall(t)[1:]:
            n = _normalizar_palavra(w)
            total[n] += 1
            maiusc[n] += w[0].isupper()
    return {n for n, c in total.items() if maiusc[n] >= 0.8 * c}


def _entidades(texto: str, proprios: set) -> set:
    out = set()
    for w in _PALAVRA.findall(texto):
        n = _normalizar_palavra(w)
        if len(n) < 2 or n in STOP_NORM or n in _NAO_SEPARAM or w.isdigit():
            continue
        if (w.isupper() and len(w) >= 2) or (w[0].isupper() and n in proprios):
            out.add(n)
    return out


def _separar_moldes(temas: list) -> Callable[[np.ndarray, float], None]:
    """Devolve a função que impede juntar dois temas quando o "sujeito" do título (as primeiras palavras)
    é uma entidade diferente e não têm nenhuma entidade em comum. Ex.: "Lucro do BCP…" vs "Lucro do BPI…"."""
    proprios = _nomes_proprios([a["titulo"] for t in temas for a in t["artigos"]])
    sujeito = [_entidades(" ".join(_PALAVRA.findall(t["titulo"])[:4]), proprios) for t in temas]
    todas = [s.union(*(_entidades(a["titulo"], proprios) for a in t["artigos"][:8]))
             for s, t in zip(sujeito, temas)]

    def separar(dist: np.ndarray, limiar: float) -> None:
        for i, j in zip(*np.nonzero(np.triu(dist < limiar, k=1))):   # só os pares i < j
            if sujeito[i] and sujeito[j] and todas[i].isdisjoint(todas[j]):
                dist[i, j] = dist[j, i] = 10.0   # com a ligação "average", 10 impede a junção
    return separar


def _historico_com_regras_antigas() -> bool:
    """Há notícias guardadas classificadas com outras regras? (acontece, por exemplo, quando se juntam
    branches e os dados de um vêm com o ficheiro de versão do outro). Nesse caso os temas são refeitos."""
    return any(a.get("regras") != VERSAO_REGRAS
               for f in (DADOS / "artigos").glob("*.json") for a in ler_json(f, []))


def _versao_temas() -> str:
    """Versão das regras + do agrupamento dos temas do dia. Quando muda, os temas de todos os dias
    guardados são refeitos (senão a Semana e o Mês continuavam com os temas antigos)."""
    partes = [VERSAO_REGRAS] + [r.pattern for r in RUBRICAS]
    partes += [inspect.getsource(f) for f in (_vetorizar, _agrupar, _distancias, _grupos, _centros, _palavras_chave,
                                              _categoria_maioritaria, sem_duplicados, e_rubrica, temas_do_dia)]
    return hashlib.sha1("\n".join(partes).encode("utf-8")).hexdigest()[:10]


def temas_do_periodo(dias: dict) -> list:
    """Junta os temas de vários dias em 'histórias' (semana / mês)."""
    lista = [dict(t, dia=d) for d, ts in dias.items() for t in ts
             if t["categoria"] in NOTICIAS and (t["n_fontes"] >= 2 or t["n_artigos"] >= 3) and not t.get("rubrica")]
    if not lista:
        return []
    textos = [" ".join([t["titulo"]] * 2 + [a["titulo"] for a in t["artigos"][:6]] + t["palavras"] * 2)
              for t in lista]
    vec, X = _vetorizar(textos)
    nomes = vec.get_feature_names_out()
    if len(lista) > 1:
        rotulos = _agrupar(_distancias(cosine_similarity(X), reutilizar=True), limiar=0.80,
                           separar=_separar_moldes(lista))
    else:
        rotulos = np.array([0])
    grupos = _grupos(rotulos)
    centros = _centros(X, grupos)
    historias = []
    for r, idx in enumerate(grupos):
        membros = sorted((lista[i] for i in idx), key=lambda t: t["pontuacao"], reverse=True)
        dias_presentes = sorted({m["dia"] for m in membros})
        fontes = sorted({f for m in membros for f in m["fontes"]})
        linha = {}
        for m in membros:
            if m["dia"] not in linha or m["pontuacao"] > linha[m["dia"]]["pontuacao"]:
                linha[m["dia"]] = {"dia": m["dia"], "titulo": m["titulo"], "pontuacao": m["pontuacao"]}
        artigos = {}                       # sem repetir links; fica a 1.ª ocorrência
        for m in membros:
            for a in m["artigos"]:
                artigos.setdefault(a["link"], a)
        historias.append({
            "titulo": membros[0]["titulo"],
            "resumo": membros[0].get("resumo", ""),
            "categoria": _categoria_maioritaria(membros, peso="pontuacao", preferida=membros[0]["categoria"]),
            "palavras": _palavras_chave(nomes, centros, r),
            "dias": dias_presentes,
            "n_dias": len(dias_presentes),
            "fontes": fontes,
            "n_fontes": len(fontes),
            "n_artigos": sum(m["n_artigos"] for m in membros),
            "pontuacao": sum(m["pontuacao"] for m in membros) + 5 * (len(dias_presentes) - 1),
            "cronologia": sorted(linha.values(), key=lambda x: x["dia"]),
            "artigos": heapq.nlargest(15, artigos.values(), key=lambda a: a["data"]),
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


def escrever_json(caminho: Path, dados, compacto: bool = False) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=None if compacto else 1), encoding="utf-8")


def resumo_publico(temas: list, por_categoria: int) -> dict:
    """Os primeiros `por_categoria` temas de cada categoria, numa só passagem."""
    publico = {cat: [] for cat in CATEGORIAS}
    for t in temas:
        lista = publico.get(t["categoria"])
        if lista is not None and len(lista) < por_categoria:
            lista.append(t)
    return publico


DIAS_A_GUARDAR = 30
_RE_DIA = re.compile(r"\d{4}-\d{2}-\d{2}")


def apagar_antigos(hoje) -> str:
    """Apaga notícias, temas e capas com mais de DIAS_A_GUARDAR dias. Devolve o dia limite."""
    limite = (hoje - timedelta(days=DIAS_A_GUARDAR)).isoformat()
    apagados = 0
    for pasta in (DADOS / "artigos", DADOS / "temas", DADOS / "capas", DADOS / "descartados", SITE / "dias"):
        for f in pasta.glob("*.json"):
            if _RE_DIA.fullmatch(f.stem) and f.stem < limite:
                f.unlink()
                apagados += 1
    if apagados:
        print(f"Limpeza: {apagados} ficheiros com mais de {DIAS_A_GUARDAR} dias apagados")
    return limite


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
        diag = {url: {"fonte": nomes[url], "recebidas": n, "antigas": 0, "sem_categoria": 0, "aceites": 0,
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
            # classifica uma só vez e aproveita logo o motivo, caso a notícia fique de fora
            motivo = _classificar(a) if a.get("regras") != VERSAO_REGRAS else None
            if not a["categoria"]:
                motivo = motivo or classificar_com_motivo(a["titulo"], a.get("resumo", ""), a.get("cats_feed", []),
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
            existentes.update((a["link"], a) for a in lista)     # com o motivo das regras atuais
            escrever_json(f, list(existentes.values()), compacto=True)

        dias_tocados = set(por_dia) | {hoje}
        f_versao = DADOS / "versao_temas.txt"
        versao = _versao_temas()
        refazer = (not f_versao.exists() or f_versao.read_text(encoding="utf-8").strip() != versao
                   or _historico_com_regras_antigas())
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
        # o que se acabou de calcular fica em memória: não volta a ser lido do disco mais abaixo
        temas_cache, validos_cache = {}, {}
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
            temas_cache[dia] = temas
            validos_cache[dia] = [a for a in artigos_dia if a["categoria"]]
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
    limite_apagar = apagar_antigos(agora.date())
    # o que acabou de ser apagado do disco também sai da memória
    temas_cache = {d: t for d, t in temas_cache.items() if d >= limite_apagar}
    validos_cache = {d: v for d, v in validos_cache.items() if d >= limite_apagar}

    # A semana está dentro do mês: cada dia é lido (e, se preciso, classificado) uma vez só
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

    # 5. Ficheiros para o site
    todos_dias = sorted(p.stem for p in (DADOS / "temas").glob("*.json"))
    for dia in dias_tocados:
        temas = temas_de(dia)
        escrever_json(SITE / "dias" / f"{dia}.json", {
            "dia": dia,
            "capas": ler_json(DADOS / "capas" / f"{dia}.json", []),
            "temas": resumo_publico(temas, 15),
            "n_artigos": sum(t["n_artigos"] for t in temas),
        }, compacto=True)

    for nome, n in (("semana", 7), ("mes", 30)):
        with etapa(nome, tempos):
            inicio = agora.date() - timedelta(days=n - 1)
            janela = [(inicio + timedelta(days=i)).isoformat() for i in range(n)]   # todos os dias, mesmo sem dados
            com_dados = [d for d in todos_dias if d >= janela[0]]
            dias = {d: temas_de(d) for d in com_dados}
            historias = temas_do_periodo(dias)
            tendencias.acrescentar_series(historias, janela)
            alta = tendencias.em_alta(historias, com_dados) if nome == "semana" else []
            artigos_janela = dict.fromkeys(janela, [])
            artigos_janela.update((d, validos_de(d)) for d in com_dados)
            pessoas = tendencias.quem_esta_nas_noticias(artigos_janela, 15)
            opinioes = heapq.nlargest(20, (t for ts in dias.values() for t in ts if t["categoria"] == "opiniao"),
                                      key=lambda t: t.get("data", ""))
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


def atualizado_ha_pouco(minutos: int, agora: datetime | None = None) -> bool:
    """A última atualização foi há menos de `minutos`? (para o agendamento do GitHub, que chega muitas vezes
    atrasado, não repetir uma atualização que o cron-job.org acabou de fazer)"""
    agora = agora or datetime.now(LISBOA)
    try:
        ultima = datetime.fromisoformat(ler_json(SITE / "indice.json", {})["atualizado"])
    except Exception:
        return False
    return (agora - ultima).total_seconds() < minutos * 60


if __name__ == "__main__":
    if "--se-passaram" in sys.argv:
        minutos = int(sys.argv[sys.argv.index("--se-passaram") + 1])
        if atualizado_ha_pouco(minutos):
            print(f"A última atualização foi há menos de {minutos} minutos: nada a fazer.")
            sys.exit(0)
    sys.exit(main())
