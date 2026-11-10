from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from enviar_email import deve_enviar, verificar_recolha

LISBOA = ZoneInfo("Europe/Lisbon")


def test_automatico_so_a_partir_das_12h():
    for evento, agendado in (("schedule", ""), ("workflow_dispatch", "true")):   # GitHub ou cron-job.org
        assert deve_enviar(datetime(2026, 1, 15, 12, 30, tzinfo=LISBOA), evento, agendado, False) is True
        assert deve_enviar(datetime(2026, 1, 15, 11, 30, tzinfo=LISBOA), evento, agendado, False) is False
        assert deve_enviar(datetime(2026, 7, 15, 12, 30, tzinfo=LISBOA), evento, agendado, False) is True


def test_atraso_ja_nao_cancela_o_envio():
    # o GitHub atrasou a execução das 12:27 para as 13:40: antes não enviava, agora envia
    assert deve_enviar(datetime(2026, 1, 15, 13, 40, tzinfo=LISBOA), "schedule", "", False) is True


def test_so_um_envio_automatico_por_dia():
    assert deve_enviar(datetime(2026, 1, 15, 13, 27, tzinfo=LISBOA), "schedule", "", True) is False
    assert deve_enviar(datetime(2026, 1, 15, 12, 27, tzinfo=LISBOA), "workflow_dispatch", "true", True) is False


def test_run_workflow_a_mao_envia_sempre():
    assert deve_enviar(datetime(2026, 1, 15, 8, 0, tzinfo=LISBOA), "workflow_dispatch", "", True) is True
    assert deve_enviar(datetime(2026, 1, 15, 15, 0, tzinfo=LISBOA), "workflow_dispatch", "false", True) is True


def test_marcador_do_envio(tmp_path, monkeypatch):
    import enviar_email
    monkeypatch.setattr(enviar_email, "MARCADOR", tmp_path / "email_enviado.txt")
    hoje = datetime(2026, 1, 15, 12, 30, tzinfo=LISBOA)
    assert enviar_email.enviado_hoje(hoje) is False
    enviar_email.marcar_enviado(hoje)
    assert enviar_email.enviado_hoje(hoje) is True
    assert enviar_email.enviado_hoje(datetime(2026, 1, 16, 12, 30, tzinfo=LISBOA)) is False


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
