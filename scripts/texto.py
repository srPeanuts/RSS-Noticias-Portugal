"""Texto: normalizar, limpar HTML e pesquisa rápida de termos (palavras inteiras, sem acentos)."""

import re
import unicodedata
from collections.abc import Iterable, Iterator
from functools import lru_cache
from html import unescape

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
