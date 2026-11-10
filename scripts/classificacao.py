"""Classificação de cada notícia (política, governo, economia, sociedade, opinião) por regras."""

import hashlib
import inspect
import json
import re
from collections import Counter
from collections.abc import Callable

from lugares import LUGARES
from texto import ListaTermos, TextoNorm, _como_texto, _padrao, normalizar

CATEGORIAS = ["politica", "governo", "economia", "sociedade", "opiniao"]
NOTICIAS = ["politica", "governo", "economia", "sociedade"]   # categorias agrupadas por cobertura

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
