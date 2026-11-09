from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from enviar_email import deve_enviar, verificar_recolha

LISBOA = ZoneInfo("Europe/Lisbon")


def test_schedule_so_as_12h():
    assert deve_enviar(datetime(2026, 1, 15, 12, 30, tzinfo=LISBOA), "schedule") is True
    assert deve_enviar(datetime(2026, 1, 15, 11, 30, tzinfo=LISBOA), "schedule") is False
    assert deve_enviar(datetime(2026, 7, 15, 12, 30, tzinfo=LISBOA), "schedule") is True
    assert deve_enviar(datetime(2026, 7, 15, 11, 30, tzinfo=LISBOA), "schedule") is False


def test_dispatch_envia_a_qualquer_hora():
    assert deve_enviar(datetime(2026, 1, 15, 15, 0, tzinfo=LISBOA), "workflow_dispatch") is True


def _dados(indice, dia):
    return lambda caminho, padrao=None: indice if caminho.name == "indice.json" else dia


AGORA = datetime(2026, 9, 29, 9, 40, tzinfo=LISBOA)
INDICE_BOM = {"hoje": "2026-09-29", "atualizado": "2026-09-29T09:32+01:00",
              "fontes": {"a": 10, "b": 20, "c": "erro HTTP 403"}}


def test_recolha_boa():
    with patch("enviar_email.ler", side_effect=_dados(INDICE_BOM, {"n_artigos": 120})):
        ok, _ = verificar_recolha(AGORA)
    assert ok is True


def test_recolha_antiga_falha():
    indice = dict(INDICE_BOM, atualizado="2026-09-29T07:05+01:00")
    with patch("enviar_email.ler", side_effect=_dados(indice, {"n_artigos": 120})):
        ok, msgs = verificar_recolha(AGORA)
    assert ok is False and any("minutos" in m for m in msgs)


def test_recolha_de_ontem_falha():
    indice = dict(INDICE_BOM, hoje="2026-09-28")
    with patch("enviar_email.ler", side_effect=_dados(indice, {"n_artigos": 120})):
        ok, _ = verificar_recolha(AGORA)
    assert ok is False


def test_poucas_fontes_falha():
    indice = dict(INDICE_BOM, fontes={"a": 10, "b": "erro HTTP 500", "c": "erro: Timeout"})
    with patch("enviar_email.ler", side_effect=_dados(indice, {"n_artigos": 120})):
        ok, _ = verificar_recolha(AGORA)
    assert ok is False


def test_sem_noticias_falha():
    with patch("enviar_email.ler", side_effect=_dados(INDICE_BOM, {"n_artigos": 0})):
        ok, _ = verificar_recolha(AGORA)
    assert ok is False
