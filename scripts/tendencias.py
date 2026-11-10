"""
Tendências ao longo do tempo:
- temas "em alta" (cobertura a crescer nos últimos dias)
- quem está nas notícias (pessoas, partidos e instituições mais mencionados)
- série diária de cobertura de cada história (para o gráfico)
"""

import heapq
import re
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache


@lru_cache(maxsize=None)
def _norm(t: str) -> str:
    """Minúsculas e sem acentos (com memória: os mesmos nomes repetem-se em milhares de notícias)."""
    t = unicodedata.normalize("NFKD", t.lower())
    if t.isascii():
        return t
    return "".join(c for c in t if not unicodedata.combining(c))

# --------------------------------------------------------------------------
# Série diária e "em alta"
# --------------------------------------------------------------------------


def acrescentar_series(historias: list, dias: list) -> None:
    """Acrescenta a cada história a pontuação de cada dia da janela (0 quando não apareceu)."""
    for h in historias:
        por_dia = {c["dia"]: c["pontuacao"] for c in h.get("cronologia", [])}
        h["serie"] = [por_dia.get(d, 0) for d in dias]


def em_alta(historias: list, dias: list, n: int = 5) -> list:
    """Histórias cuja cobertura de hoje é bem maior do que a média dos 3 dias anteriores."""
    if len(dias) < 2:
        return []
    candidatos = []
    for h in historias:
        serie = h.get("serie") or []
        if len(serie) < 2:
            continue
        hoje = serie[-1]
        anteriores = serie[-4:-1]
        media = sum(anteriores) / len(anteriores)
        if hoje >= 7 and hoje >= 2 * media:
            h["em_alta"] = True
            candidatos.append((hoje - media, media, h))
    return [{"titulo": h["titulo"], "categoria": h["categoria"], "serie": h["serie"], "hoje": h["serie"][-1],
             "media_anterior": round(media, 1), "novo": sum(h["serie"][:-1]) == 0, "n_fontes": h["n_fontes"],
             "artigos": h["artigos"][:6]}
            for _, media, h in heapq.nlargest(n, candidatos, key=lambda x: x[0])]

# --------------------------------------------------------------------------
# Quem está nas notícias
# --------------------------------------------------------------------------

# Nome canónico -> padrões (sensíveis a maiúsculas) que o identificam
CONHECIDOS = {
    # partidos
    "PS": [r"\bPS\b", r"Partido Socialista"],
    "PSD": [r"\bPSD\b", r"Partido Social Democrata"],
    "Chega": [r"(?<!^)\bChega\b", r"^Chega\b(?! a | ao | à | aos | às )"],
    "Iniciativa Liberal": [r"\bIL\b", r"Iniciativa Liberal"],
    "Livre": [r"(?<!^)\bLivre\b"],
    "Bloco de Esquerda": [r"\bBE\b", r"Bloco de Esquerda", r"\bBloco\b"],
    "PCP": [r"\bPCP\b", r"Partido Comunista"],
    "CDS": [r"\bCDS(-PP)?\b"],
    "PAN": [r"\bPAN\b"],
    "JPP": [r"\bJPP\b"],
    "AD": [r"\bAD\b", r"Aliança Democrática"],
    # instituições
    "Ministério Público": [r"Ministério Público", r"\bMP\b"],
    "Assembleia da República": [r"Assembleia da República", r"\bParlamento\b"],
    "Presidente da República": [r"Presidente da República", r"\bBelém\b"],
    "Banco de Portugal": [r"Banco de Portugal"],
    "BCE": [r"\bBCE\b", r"Banco Central Europeu"],
    "Comissão Europeia": [r"Comissão Europeia", r"\bBruxelas\b"],
    "Tribunal Constitucional": [r"Tribunal Constitucional"],
    "CMVM": [r"\bCMVM\b"],
    "SNS": [r"\bSNS\b", r"Serviço Nacional de Saúde"],
    "PSP": [r"\bPSP\b"],
    "GNR": [r"\bGNR\b"],
    "Polícia Judiciária": [r"\bPJ\b", r"Polícia Judiciária"],
    "INE": [r"\bINE\b"],
    "TAP": [r"\bTAP\b"],
    "CGD": [r"\bCGD\b", r"Caixa Geral de Depósitos"],
    "AIMA": [r"\bAIMA\b"],
    "CP": [r"\bCP\b", r"Comboios de Portugal"],
    # pessoas (apelidos usados sozinhos nos títulos)
    "Luís Montenegro": [r"\bMontenegro\b"],
    "Marcelo Rebelo de Sousa": [r"\bMarcelo\b"],
    "António José Seguro": [r"António José Seguro", r"(?<!^)\bSeguro\b"],
    "André Ventura": [r"\bVentura\b"],
    "José Luís Carneiro": [r"José Luís Carneiro", r"(?<!^)\bCarneiro\b"],
    "Pedro Nuno Santos": [r"Pedro Nuno Santos"],
    "Carlos Moedas": [r"\bMoedas\b"],
    "Mário Centeno": [r"\bCenteno\b"],
    "Joaquim Miranda Sarmento": [r"Miranda Sarmento"],
    "Mariana Mortágua": [r"\bMortágua\b"],
    "Paulo Raimundo": [r"Paulo Raimundo"],
    "Rui Tavares": [r"Rui Tavares"],
    "Rui Rocha": [r"Rui Rocha"],
    "Mariana Leitão": [r"Mariana Leitão"],
    "Gouveia e Melo": [r"Gouveia e Melo"],
    "Pedro Passos Coelho": [r"Passos Coelho"],
    "António Costa": [r"António Costa"],
    "Hugo Soares": [r"Hugo Soares"],
    "Isaltino Morais": [r"\bIsaltino\b"],
    "João Galamba": [r"\bGalamba\b"],
    "Leitão Amaro": [r"Leitão Amaro"],
    "Paulo Rangel": [r"Paulo Rangel"],
    "Ana Paula Martins": [r"Ana Paula Martins"],
}
# Os padrões de cada entidade numa só expressão; e todos juntos, para saltar logo as frases sem nenhuma
_PADROES = {k: re.compile("|".join(f"(?:{p})" for p in v)) for k, v in CONHECIDOS.items()}
_QUALQUER = re.compile("|".join(p.pattern for p in _PADROES.values()))
# "n está contido num nome conhecido" passa a ser uma só pesquisa numa string
# (o separador "\0" nunca aparece num nome detetado, por isso não se encontram nomes "colados")
_CONHECIDOS_JUNTOS = "\0".join(_norm(k) for k in CONHECIDOS)
_LIGACOES = frozenset(("de", "da", "do", "dos", "das", "e"))

# Nomes próprios em sequência: "Lacerda Sales", "Maria Lúcia Amaral", "Rui Pinto da Costa"
_MAIUSC = r"[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç'’\-]+"
_SEQ = re.compile(rf"{_MAIUSC}(?:\s+(?:de|da|do|dos|das|e)\s+{_MAIUSC}|\s+{_MAIUSC})+")

IGNORAR = {_norm(p) for p in """
Portugal Lisboa Porto Madeira Açores Algarve Coimbra Braga Faro Setúbal Aveiro Leiria Évora Funchal Sintra Cascais
Europa Estado Estados Unidos Reino Unido Médio Oriente União Europeia Casa Branca Nações Unidas
Janeiro Fevereiro Março Abril Maio Junho Julho Agosto Setembro Outubro Novembro Dezembro
Segunda Terça Quarta Quinta Sexta Sábado Domingo Governo Ministro Ministra Primeiro Presidente
Santa São Ponta Delgada Grande Nacional Norte Sul Centro Tejo Douro Oeiras Almada Amadora Loures
Câmara Municipal Junta Freguesia Governo Regional Estado Novo Dia Mundial Liga Taça Seleção
""".split()}


_RE_FRASES = re.compile(r"(?<=[.!?:;])\s+|\s+[-–—]\s+")


def _frases(texto: str) -> list:
    return [f.strip(" \"“”«»'") for f in _RE_FRASES.split(texto) if f.strip()]


def extrair(texto: str) -> set:
    """Entidades mencionadas num texto (título + início do resumo)."""
    encontrados = set()
    for frase in _frases(texto):
        if _QUALQUER.search(frase):     # a maior parte das frases não tem nenhuma entidade conhecida
            encontrados.update(nome for nome, padrao in _PADROES.items() if padrao.search(frase))
        for m in _SEQ.finditer(frase):
            if m.start() == 0:          # a 1.ª palavra da frase tem sempre maiúscula
                palavras = m.group(0).split()
                if len(palavras) < 3:
                    continue
                i = 1
                while i < len(palavras) and palavras[i] in _LIGACOES:
                    i += 1
                cand = " ".join(palavras[i:])
            else:
                cand = m.group(0)
            partes = [p for p in cand.split() if p not in _LIGACOES]
            if len(partes) < 2 or any(_norm(p) in IGNORAR for p in partes):
                continue
            if _norm(cand) in _CONHECIDOS_JUNTOS:     # é (parte de) um nome conhecido
                continue
            encontrados.add(cand)
    return encontrados


@lru_cache(maxsize=None)
def _entidades(texto: str) -> tuple:
    """extrair() com memória: a vista Mês reaproveita o trabalho já feito para a Semana."""
    return tuple(extrair(texto))


def quem_esta_nas_noticias(artigos_por_dia: dict, n: int = 15) -> list:
    """Conta em quantas notícias aparece cada entidade, e a série por dia."""
    dias = sorted(artigos_por_dia)
    total = Counter()
    por_dia = defaultdict(Counter)
    for d in dias:
        for a in artigos_por_dia[d]:
            texto = f"{a['titulo']}. {a.get('resumo', '')[:300]}"
            for e in _entidades(texto):
                total[e] += 1
                por_dia[e][d] += 1
    resultado = []
    for nome, c in total.most_common(n * 2):
        if c < 2:
            break
        resultado.append({"nome": nome, "n": c, "serie": [por_dia[nome][d] for d in dias],
                          "tipo": "conhecido" if nome in CONHECIDOS else "detetado"})
    return resultado[:n]
