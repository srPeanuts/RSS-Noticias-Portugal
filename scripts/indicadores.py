"""
Indicadores económicos de Portugal (dados oficiais do Eurostat, API gratuita e sem registo).

Cada indicador guarda o último valor, o anterior e uma série curta para o gráfico.
"""

from concurrent.futures import ThreadPoolExecutor

from rede import obter

API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"

INDICADORES = [
    {
        "id": "inflacao", "nome": "Inflação", "unidade": "%", "descricao": "Variação homóloga dos preços (IHPC)",
        "dataset": "prc_hicp_minr", "params": {"coicop18": "TOTAL", "unit": "RCH_A"},
        "comparar": "EA", "bom": "estavel", "n": 24,
        "link": "https://ec.europa.eu/eurostat/databrowser/view/prc_hicp_minr/default/table",
    },
    {
        "id": "desemprego", "nome": "Desemprego", "unidade": "%", "descricao": "Taxa de desemprego (ajustada)",
        "dataset": "une_rt_m", "params": {"s_adj": "SA", "age": "TOTAL", "sex": "T", "unit": "PC_ACT"},
        "comparar": "EA", "bom": "baixo", "n": 24,
        "link": "https://ec.europa.eu/eurostat/databrowser/view/une_rt_m/default/table",
    },
    {
        "id": "pib", "nome": "Crescimento do PIB", "unidade": "%", "descricao": "Variação homóloga do PIB real",
        "dataset": "namq_10_gdp", "params": {"unit": "CLV_PCH_SM", "s_adj": "SCA", "na_item": "B1GQ"},
        "comparar": "EA", "bom": "alto", "n": 12,
        "link": "https://ec.europa.eu/eurostat/databrowser/view/namq_10_gdp/default/table",
    },
    {
        "id": "euribor", "nome": "Euribor 12 meses", "unidade": "%", "descricao": "Média mensal (conta para o crédito à habitação)",
        "dataset": "irt_st_m", "params": {"int_rt": "IRT_M12"}, "geo": "EA",
        "comparar": None, "bom": "baixo", "n": 24,
        "link": "https://ec.europa.eu/eurostat/databrowser/view/irt_st_m/default/table",
    },
    {
        "id": "juros", "nome": "Juros da dívida a 10 anos", "unidade": "%", "descricao": "Taxa das obrigações do Tesouro",
        "dataset": "irt_lt_mcby_m", "params": {}, "comparar": None, "bom": "baixo", "n": 24,
        "link": "https://ec.europa.eu/eurostat/databrowser/view/irt_lt_mcby_m/default/table",
    },
]


def _serie(dataset: str, params: dict, geo: str, n: int) -> list:
    """Devolve [(periodo, valor), ...] do mais antigo para o mais recente."""
    q = dict(params, geo=geo, lastTimePeriod=n)
    d = obter(API + dataset, params=q, timeout=(5, 30)).json()
    ids, tamanhos = d["id"], d["size"]
    # passo (stride) de cada dimensão no índice "achatado" do JSON-stat
    passos, acc = {}, 1
    for dim, tam in reversed(list(zip(ids, tamanhos))):
        passos[dim] = acc
        acc *= tam
    tempos = d["dimension"]["time"]["category"]["index"]
    pos_tempo = {v: k for k, v in tempos.items()}
    valores = d["value"]
    # posição no índice "achatado" -> valor (a lista já traz a posição; o dict traz-na como texto)
    if isinstance(valores, list):
        itens = ((i, v) for i, v in enumerate(valores) if v is not None)
    else:
        itens = ((int(k), v) for k, v in valores.items())
    serie = {}
    for i, v in itens:
        t = (i // passos["time"]) % tamanhos[ids.index("time")]
        serie[pos_tempo[t]] = round(float(v), 2)
    return sorted(serie.items())


def _um_indicador(ind: dict) -> tuple[dict | None, str]:
    """Devolve (resultado, linha para o registo)."""
    geo = ind.get("geo", "PT")
    try:
        serie = _serie(ind["dataset"], ind["params"], geo, ind["n"])
        if not serie:
            raise ValueError("sem dados")
        comparacao = None
        if ind["comparar"]:
            try:
                s2 = dict(_serie(ind["dataset"], ind["params"], ind["comparar"], 3))
                comparacao = s2.get(serie[-1][0])
            except Exception:
                pass
        ultimo, anterior = serie[-1], (serie[-2] if len(serie) > 1 else None)
        return {
            "id": ind["id"], "nome": ind["nome"], "descricao": ind["descricao"], "unidade": ind["unidade"],
            "bom": ind["bom"], "link": ind["link"], "geo": geo,
            "periodo": ultimo[0], "valor": ultimo[1],
            "anterior": anterior[1] if anterior else None,
            "variacao": round(ultimo[1] - anterior[1], 2) if anterior else None,
            "zona_euro": comparacao,
            "serie": [{"p": p, "v": v} for p, v in serie],
        }, f"  ✓ {ind['nome']}: {ultimo[1]}{ind['unidade']} ({ultimo[0]})"
    except Exception as e:
        return None, f"  ✗ {ind['nome']}: {e.__class__.__name__} {str(e)[:80]}"


def recolher() -> list:
    """Pede os indicadores todos ao mesmo tempo (antes era um de cada vez); mantém a ordem."""
    with ThreadPoolExecutor(max_workers=len(INDICADORES)) as pool:
        resultados = list(pool.map(_um_indicador, INDICADORES))
    lista = []
    for res, linha in resultados:
        print(linha)
        if res:
            lista.append(res)
    return lista
