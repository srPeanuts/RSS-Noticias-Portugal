"""Notícias reais de 3 dias: a classificação e o agrupamento não podem mudar sem darmos por isso.

Se mudares as regras de propósito, corre `python tests/referencia/gerar.py --aceitar` e faz commit."""

import json
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent / "referencia"
sys.path.insert(0, str(PASTA))
from gerar import calcular  # noqa: E402

NOTICIAS = json.loads((PASTA / "noticias.json").read_text(encoding="utf-8"))
ESPERADO = json.loads((PASTA / "esperado.json").read_text(encoding="utf-8"))
ATUAL = calcular(NOTICIAS)

AJUDA = "\nSe a mudança é de propósito: python tests/referencia/gerar.py --aceitar"


def test_categorias_iguais():
    titulos = {a["link"]: a["titulo"] for ts in NOTICIAS.values() for a in ts}
    mudadas = [f"{titulos[k][:70]}: {v} → {ATUAL['categorias'].get(k)}"
               for k, v in ESPERADO["categorias"].items() if ATUAL["categorias"].get(k) != v]
    assert not mudadas, f"{len(mudadas)} notícias mudaram de categoria:\n" + "\n".join(mudadas[:15]) + AJUDA


def test_temas_do_dia_iguais():
    for dia, esperados in ESPERADO["temas"].items():
        def chave(t):
            return (tuple(t["links"]), t["categoria"], t["rubrica"])
        antes = {chave(t): t["titulo"] for t in esperados}
        agora = {chave(t): t["titulo"] for t in ATUAL["temas"][dia]}
        so_antes = [antes[k][:70] for k in antes.keys() - agora.keys()]
        so_agora = [agora[k][:70] for k in agora.keys() - antes.keys()]
        assert not (so_antes or so_agora), (f"{dia}: temas diferentes\n  deixaram de existir: {so_antes[:8]}"
                                            f"\n  novos: {so_agora[:8]}" + AJUDA)


def test_ordem_dos_temas_igual():
    for dia, esperados in ESPERADO["temas"].items():
        assert [t["titulo"] for t in ATUAL["temas"][dia]] == [t["titulo"] for t in esperados], \
            f"{dia}: a ordem dos temas mudou" + AJUDA


def test_historias_do_periodo_iguais():
    antes = [(h["titulo"], h["categoria"], h["dias"]) for h in ESPERADO["historias"]]
    agora = [(h["titulo"], h["categoria"], h["dias"]) for h in ATUAL["historias"]]
    diferentes = [a[0][:70] for a in antes if a not in agora] + [a[0][:70] for a in agora if a not in antes]
    assert antes == agora, f"histórias diferentes: {diferentes[:10]}" + AJUDA


def test_casos_conhecidos_nos_dados_reais():
    # rubricas dos mercados ficam marcadas e não entram nas histórias
    rubricas = [t["titulo"] for ts in ATUAL["temas"].values() for t in ts if t["rubrica"]]
    assert rubricas, "devia haver rubricas (bolsa, Euribor...) nestes dias"
    titulos_historias = {d for h in ATUAL["historias"] for d in h["dias"]}
    assert not titulos_historias & set(rubricas)
