from unittest.mock import MagicMock

import requests

import atualizar
import indicadores
import recolha


def test_feeds_e_capas_juntos_com_erros(monkeypatch):
    def feed(f):
        if f["url"] == "mau":
            raise requests.HTTPError(response=MagicMock(status_code=403))
        return [{"titulo": "t", "link": f["url"]}]

    def capa(c):
        if c["nome"] == "C2":
            raise ValueError("imagem não encontrada")
        return {"nome": c["nome"]}

    monkeypatch.setattr(recolha, "ler_feed", feed)
    monkeypatch.setattr(recolha, "ler_capa", capa)
    config = {"feeds": [{"fonte": "A", "url": "bom"}, {"fonte": "B", "url": "mau"}],
              "capas": [{"nome": "C1"}, {"nome": "C2"}, {"nome": "C3"}]}
    artigos, estado, capas = recolha.recolher(config)
    assert len(artigos) == 1
    assert estado == {"bom": 1, "mau": "erro HTTP 403"}
    assert [c["nome"] for c in capas] == ["C1", "C3"]


def test_so_capas(monkeypatch):
    monkeypatch.setattr(recolha, "ler_feed", lambda f: (_ for _ in ()).throw(AssertionError("não devia ler feeds")))
    monkeypatch.setattr(recolha, "ler_capa", lambda c: {"nome": c["nome"]})
    artigos, estado, capas = recolha.recolher({"feeds": [{"fonte": "A", "url": "x"}], "capas": [{"nome": "C1"}]},
                                                feeds=False)
    assert (artigos, estado, len(capas)) == ([], {}, 1)


def test_indicadores_em_paralelo_mantem_a_ordem(monkeypatch):
    def serie(dataset, params, geo, n):
        if dataset == "une_rt_m":
            raise requests.Timeout()
        return [("2026-07", 1.0), ("2026-08", 2.0)]

    monkeypatch.setattr(indicadores, "_serie", serie)
    lista = indicadores.recolher()
    esperados = [i["id"] for i in indicadores.INDICADORES if i["dataset"] != "une_rt_m"]
    assert [i["id"] for i in lista] == esperados
    assert lista[0]["valor"] == 2.0 and lista[0]["variacao"] == 1.0


def test_atualizado_ha_pouco(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    monkeypatch.setattr(atualizar, "SITE", tmp_path)
    (tmp_path / "indice.json").write_text(json.dumps({"atualizado": "2026-11-10T09:12+00:00"}), encoding="utf-8")
    agora = datetime.fromisoformat("2026-11-10T09:40+00:00")
    assert atualizar.atualizado_ha_pouco(50, agora) is True
    assert atualizar.atualizado_ha_pouco(20, agora) is False
