from atualizar import classificar, e_estrangeira, e_opiniao


def test_governo_pelo_titulo():
    assert classificar(
        "Governo aprova decreto-lei sobre habitação",
        "O Conselho de Ministros reuniu-se em São Bento.",
        [], "", "",
    ) == "governo"


def test_politica_por_sigla_e_parlamento():
    assert classificar("PS e PSD divergem no Parlamento", "", [], "", "") == "politica"


def test_desporto_pela_categoria_do_feed():
    assert classificar("Benfica vence o Sporting", "jogo da liga", ["desporto"], "", "") is None


def test_desporto_pelo_titulo():
    assert classificar("Benfica contratou avançado", "o clube anunciou a contratação", [], "", "") is None


def test_noticia_estrangeira_sem_portugal():
    titulo, resumo = "Trump anuncia novas tarifas", "A Casa Branca confirmou a medida."
    assert e_estrangeira(titulo, resumo) is True
    assert classificar(titulo, resumo, [], "", "") is None


def test_noticia_estrangeira_com_ligacao_a_portugal():
    assert e_estrangeira("Trump e Montenegro em Lisboa", "O primeiro-ministro português recebeu o presidente.") is False


def test_mundo_sem_portugal_e_descartado():
    assert classificar("Eleições em França", "Macron discursa em Paris.", ["mundo"], "", "") is None


def test_opiniao_pela_url():
    link = "https://www.publico.pt/opiniao/noticia/o-estado-da-nacao"
    assert e_opiniao(link, []) is True
    assert classificar("O estado da nação", "um texto de opinião", [], "", link) == "opiniao"


def test_categoria_forcada_do_feed():
    assert classificar("Título genérico qualquer", "sem palavras especiais", [], "economia", "") == "economia"


# --- classificar cada artigo uma vez só -------------------------------------------------

import pytest  # noqa: E402

import atualizar  # noqa: E402
from atualizar import VERSAO_REGRAS, categoria_atual  # noqa: E402


def _artigo(**extra):
    return dict({"titulo": "Governo aprova decreto-lei sobre habitação", "resumo": "", "cats_feed": [],
                 "categoria_forcada": "", "link": ""}, **extra)


def test_categoria_fica_guardada_e_nao_se_repete(monkeypatch):
    a = _artigo()
    assert categoria_atual(a) == "governo"
    assert a["categoria"] == "governo" and a["regras"] == VERSAO_REGRAS
    monkeypatch.setattr(atualizar, "classificar", lambda *x: pytest.fail("não devia voltar a classificar"))
    assert categoria_atual(a) == "governo"


def test_regras_novas_voltam_a_classificar():
    a = _artigo(categoria="economia", regras="versao-antiga")
    assert categoria_atual(a) == "governo"
    assert a["regras"] == VERSAO_REGRAS


def test_artigo_antigo_sem_categoria_forcada():
    a = {"titulo": "Título genérico qualquer", "resumo": "sem palavras especiais", "categoria": "economia"}
    assert categoria_atual(a) == "economia"
    assert a["categoria_forcada"] == "economia"


# --- regras afinadas em nov/2026 ---------------------------------------------------------

def test_governo_como_sujeito_ganha_a_seccao_do_feed():
    # vinha do feed de Economia e ficava em Economia
    assert classificar("Governo reduz retenção de IRS em 50% nos salários de novembro", "",
                       ["Economia"], "economia", "") == "governo"
    assert classificar("Ministra do Trabalho afasta salário mínimo superior a 970 euros", "",
                       ["Economia"], "economia", "") == "governo"


def test_partido_que_fala_do_governo_continua_politica():
    assert classificar("PS pede esclarecimentos ao Governo sobre Sé de Portalegre", "",
                       ["Política"], "politica", "") == "politica"


def test_governo_regional_nao_conta_como_governo():
    assert classificar("Governo Regional da Madeira investe 8,4 milhões em prevenção de incêndios", "",
                       ["Economia"], "economia", "") != "governo"


def test_mais_paises_estrangeiros():
    assert e_estrangeira("Atirador que matou seis pessoas em Cracóvia morre no hospital", "") is True
    assert e_estrangeira("Albânia. Polícia usa canhões de água para dispersar manifestantes", "") is True


def test_dia_nacional_de_outro_pais_nao_e_portugal():
    assert e_estrangeira("Pedro Sánchez apupado no desfile do Dia Nacional de Espanha", "") is True


def test_apelidos_portugueses_nao_sao_paises():
    # "Maduro" é também apelido de um político português (Poiares Maduro)
    assert e_estrangeira("Poiares Maduro discorda de estratégia política", "") is False


def test_ministra_estrangeira_nao_e_governo_portugues():
    assert classificar("Ministra japonesa alerta sobre impacto das taxas de juro no G7", "", [], "", "") is None


def test_cidade_portuguesa_conta_como_ligacao_a_portugal():
    assert e_estrangeira("Rizal Khadai, do mar da Indonésia à Póvoa de Varzim", "") is False


def test_empate_na_categoria_do_tema_fica_com_a_noticia_principal():
    from atualizar import _categoria_maioritaria
    membros = [{"categoria": "sociedade"}, {"categoria": "economia"}]
    assert _categoria_maioritaria(membros, preferida="sociedade") == "sociedade"
    assert _categoria_maioritaria(membros + [{"categoria": "economia"}], preferida="sociedade") == "economia"
