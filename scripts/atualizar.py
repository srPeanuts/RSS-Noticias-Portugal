"""
Notícias de Portugal — recolha e análise diária.

1. Lê os feeds RSS listados em fontes.json
2. Vai buscar as capas dos jornais (vercapas.com)
3. Classifica as notícias em política / governo / economia / sociedade e cultura / opinião
4. Agrupa notícias de jornais diferentes que falam do mesmo assunto
5. Ordena os temas pela cobertura (quantos jornais falam deles)
6. Gera os ficheiros JSON que a página web (pasta docs/) mostra

Não usa nenhuma IA paga: tudo corre com Python + scikit-learn, de graça.

Este ficheiro junta as etapas; o trabalho está repartido por:
  texto.py          normalizar texto e pesquisar termos
  classificacao.py  regras de classificação de cada notícia
  recolha.py        feeds RSS, capas e diagnóstico dos feeds
  agrupamento.py    temas do dia e histórias da Semana/Mês
"""

import heapq
import json
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indicadores  # noqa: E402
import tendencias   # noqa: E402
from agrupamento import _versao_temas, sem_duplicados, temas_do_dia, temas_do_periodo  # noqa: E402
from classificacao import (CATEGORIAS, VERSAO_REGRAS, _classificar, categoria_atual,  # noqa: E402
                           classificar_com_motivo)
from recolha import _registar_descarte, _relatorio_diagnostico, recolher  # noqa: E402

# Continuam acessíveis como atualizar.X (usados pelos testes e por scripts/testar_feeds.py)
from agrupamento import _categoria_maioritaria  # noqa: E402,F401
from classificacao import classificar, e_estrangeira, e_opiniao, e_rubrica  # noqa: E402,F401
from recolha import ler_capa, ler_feed  # noqa: E402,F401

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"            # histórico bruto (artigos e temas por dia)
SITE = RAIZ / "docs" / "data"     # o que a página web lê
LISBOA = ZoneInfo("Europe/Lisbon")


def _historico_com_regras_antigas() -> bool:
    """Há notícias guardadas classificadas com outras regras? (acontece, por exemplo, quando se juntam
    branches e os dados de um vêm com o ficheiro de versão do outro). Nesse caso os temas são refeitos."""
    return any(a.get("regras") != VERSAO_REGRAS
               for f in (DADOS / "artigos").glob("*.json") for a in ler_json(f, []))


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
