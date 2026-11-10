"""
Gera (ou atualiza) a referência dos testes com notícias reais.

    python tests/referencia/gerar.py            # cria noticias.json a partir de 3 dias do histórico (só na 1.ª vez)
    python tests/referencia/gerar.py --aceitar  # depois de mudares as regras de propósito: grava o resultado novo

O teste tests/test_referencia.py corre a classificação e o agrupamento sobre estas notícias e compara com
esperado.json. Se mudares as regras e o resultado mudar, o teste falha e mostra o que mudou; se a mudança
for a que querias, corre este script com --aceitar e faz commit dos dois ficheiros.
"""

import json
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
RAIZ = PASTA.parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

DIAS = ["2026-11-06", "2026-11-07", "2026-11-08"]
CAMPOS = ("titulo", "link", "resumo", "fonte", "autor", "data", "cats_feed", "categoria_forcada")


def criar_noticias() -> dict:
    noticias = {}
    for dia in DIAS:
        artigos = json.loads((RAIZ / "dados" / "artigos" / f"{dia}.json").read_text(encoding="utf-8"))
        noticias[dia] = [{k: a.get(k, "") for k in CAMPOS} | {"resumo": a.get("resumo", "")[:400]} for a in artigos]
    return noticias


def calcular(noticias: dict) -> dict:
    """O que a app faria com estas notícias: categoria de cada uma, temas de cada dia e histórias do período."""
    from atualizar import categoria_atual, temas_do_dia, temas_do_periodo
    categorias, temas = {}, {}
    for dia, artigos in noticias.items():
        artigos = [dict(a) for a in artigos]
        for a in artigos:
            categorias[a["link"]] = categoria_atual(a)
        temas[dia] = temas_do_dia(artigos)
    historias = temas_do_periodo(temas)
    return {
        "categorias": categorias,
        "temas": {dia: [{"titulo": t["titulo"], "categoria": t["categoria"], "rubrica": bool(t.get("rubrica")),
                         "links": sorted(a["link"] for a in t["artigos"])} for t in ts] for dia, ts in temas.items()},
        "historias": [{"titulo": h["titulo"], "categoria": h["categoria"],
                       "dias": [c["titulo"] for c in h["cronologia"]]} for h in historias],
    }


def main():
    f_noticias = PASTA / "noticias.json"
    if not f_noticias.exists():
        f_noticias.write_text(json.dumps(criar_noticias(), ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"Criado {f_noticias.name}")
    elif "--aceitar" not in sys.argv:
        print("noticias.json já existe. Para gravar o resultado atual como referência, usa --aceitar.")
        return 1
    noticias = json.loads(f_noticias.read_text(encoding="utf-8"))
    (PASTA / "esperado.json").write_text(json.dumps(calcular(noticias), ensure_ascii=False, indent=0),
                                         encoding="utf-8")
    print("Gravado esperado.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
