import json
from datetime import datetime, timedelta, timezone

import atualizar


def _artigo(url, horas, titulo="Governo aprova decreto-lei sobre habitação"):
    data = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()
    return {"titulo": titulo, "link": f"https://x.pt/{url}/{horas}/{titulo[:5]}", "resumo": "", "fonte": url,
            "autor": "", "data": data, "cats_feed": [], "categoria_forcada": "", "_feed": url}


def test_diagnostico_conta_antigas_fora_e_aceites(tmp_path, monkeypatch):
    for pasta in ("dados", "docs/data"):
        (tmp_path / pasta).mkdir(parents=True)
    (tmp_path / "fontes.json").write_text(json.dumps({
        "feeds": [{"fonte": "Bom", "url": "bom"}, {"fonte": "Parado", "url": "parado"}, {"fonte": "Mau", "url": "mau"}],
        "capas": []}), encoding="utf-8")
    monkeypatch.setattr(atualizar, "RAIZ", tmp_path)
    monkeypatch.setattr(atualizar, "DADOS", tmp_path / "dados")
    monkeypatch.setattr(atualizar, "SITE", tmp_path / "docs" / "data")
    artigos = [_artigo("bom", 1), _artigo("bom", 2, "Benfica vence o Sporting no dérbi"),
               _artigo("parado", 24 * 400), _artigo("parado", 24 * 300)]
    monkeypatch.setattr(atualizar, "recolher",
                        lambda config, feeds=True, capas=True: (artigos, {"bom": 2, "parado": 2, "mau": "erro HTTP 403"}, []))
    monkeypatch.setattr(atualizar.indicadores, "recolher", lambda: [])
    atualizar.main()
    indice = json.loads((tmp_path / "docs" / "data" / "indice.json").read_text(encoding="utf-8"))
    d = indice["diagnostico"]
    assert (d["bom"]["recebidas"], d["bom"]["antigas"], d["bom"]["sem_categoria"], d["bom"]["aceites"]) == (2, 0, 1, 1)
    assert (d["parado"]["antigas"], d["parado"]["aceites"]) == (2, 0)
    assert d["mau"] == {"fonte": "Mau", "erro": "erro HTTP 403"}
    assert d["bom"]["motivos"] == {"título": 1}
    assert d["bom"]["exemplos_fora"][0]["motivo"] == "título: benfica"
    # as descartadas ficam guardadas à parte, com o motivo
    descartadas = [a for f in (tmp_path / "dados" / "descartados").glob("*.json")
                   for a in json.loads(f.read_text(encoding="utf-8"))]
    assert [a["motivo"] for a in descartadas] == ["título: benfica"]
    # o campo interno "_feed" não fica guardado nos ficheiros de notícias
    for f in (tmp_path / "dados" / "artigos").glob("*.json"):
        assert '"_feed"' not in f.read_text(encoding="utf-8")
