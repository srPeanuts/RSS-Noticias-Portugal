"""Agrupar notícias em temas do dia e temas do dia em histórias da Semana/Mês."""

import hashlib
import heapq
import inspect
import re
from collections import Counter
from collections.abc import Callable

import numpy as np
from scipy.sparse import csr_matrix        # já vem com o scikit-learn (as matrizes TF-IDF são deste tipo)
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import tendencias
from texto import STOP_NORM, _STOP_LISTA, _normalizar_palavra, normalizar
from classificacao import NOTICIAS, RUBRICAS, VERSAO_REGRAS, categoria_atual, e_rubrica

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
