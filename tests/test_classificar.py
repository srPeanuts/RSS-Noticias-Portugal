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


# --- rubricas diárias dos mercados e motivos de descarte ----------------------------------

def test_rubricas_dos_mercados():
    from atualizar import e_rubrica
    for t in ["Wall Street fecha em alta perante apetite renovado pela IA",
              "Euribor sobe a três e a seis meses e desce a 12 meses",
              "PSI segue no ‘vermelho’, penalizado pelo BCP",
              "Bolsa de Lisboa fecha em queda com Galp a perder mais de 2%",
              "Cotação do Brent para entrega em dezembro sobe 2,40% para 117,64 dólares",
              "Gasóleo sobe quatro cêntimos por litro na próxima semana",
              "Juros da dívida portuguesa descem a dois, cinco e 10 anos"]:
        assert e_rubrica(t), t
    for t in ["Governo aprova descida do IRS", "Lucro do BCP sobe 12,6% para 873,7 ME até setembro",
              "Taxa de inflação sobe para 3,8% na zona euro"]:
        assert not e_rubrica(t), t


def test_rubricas_ficam_fora_da_semana():
    from atualizar import temas_do_periodo
    base = {"categoria": "economia", "n_fontes": 4, "n_artigos": 4, "pontuacao": 16, "palavras": [], "fontes": []}
    dias = {"2026-11-0%d" % d: [dict(base, titulo="Wall Street fecha em alta", rubrica=True,
                                      artigos=[{"titulo": "Wall Street fecha em alta", "link": f"w{d}", "data": "x"}])]
            for d in range(1, 5)}
    assert temas_do_periodo(dias) == []


def test_motivo_do_descarte():
    from atualizar import classificar_com_motivo
    assert classificar_com_motivo("Benfica vence o Sporting", "", ["Desporto"], "", "")[1] == "secção do jornal: desporto"
    assert classificar_com_motivo("Trump anuncia tarifas", "A Casa Branca confirmou.", [], "", "")[1] == "outro país: trump"
    assert classificar_com_motivo("Um dia qualquer", "", [], "", "") == (None, "sem palavras de nenhuma categoria")


# --- notícias que se perdiam por falta de palavras (nov/2026) ----------------------------

def test_crime_sem_outras_palavras_vai_para_sociedade():
    assert classificar("Matou o colega de casa com agressões na cabeça, após o ter manietado", "", [], "", "") == "sociedade"
    assert classificar("Quando os processos começam com uma grande (falsa) suspeita – agora é a operação Picoas",
                       "", [], "", "") == "sociedade"


def test_terra_portuguesa_sem_outras_palavras_vai_para_sociedade():
    assert classificar("Com o São Martinho, no Alentejo é tempo de abrir as talhas", "", [], "", "") == "sociedade"


def test_palavras_de_ultimo_recurso_nao_mudam_as_outras_categorias():
    # tem palavras de política: o "violência" não a leva para Sociedade
    assert classificar("IL culpa PCP e BE pela violência nas manifestações", "", [], "", "") == "politica"


def test_seccao_tecnologia_conta_nos_feeds_de_economia():
    titulo = "Tecnologias digitais nas empresas são questão de sobrevivência, diz administrador"
    assert classificar(titulo, "", ["Tecnologia"], "economia", "") == "economia"
    assert classificar(titulo, "", ["Tecnologia"], "", "") is None


def test_terra_portuguesa_conta_como_ligacao_a_portugal():
    assert e_estrangeira("Brasileiro detido em Albufeira por tráfico", "") is False


def test_mau_tempo_entra_em_sociedade():
    assert classificar("Chuva, vento e ondas até cinco metros: todo o continente sob aviso amarelo", "", [], "", "") == "sociedade"
    assert classificar("Proteção civil registou 73 ocorrências entre as 00:00 e as 07:00", "", [], "", "") == "sociedade"
    assert classificar("Furacão Melissa atinge a Jamaica com ventos de 250 km/h", "", [], "", "") is None


def test_economia_de_ultimo_recurso():
    assert classificar("Telecomunicações voltam a ficar mais caras. Há uma subida de 10€", "", [], "", "") == "economia"


# --- histórias da Semana/Mês: não juntar o mesmo "molde" sobre empresas diferentes ---------

def test_lucros_de_empresas_diferentes_nao_sao_a_mesma_historia():
    from atualizar import temas_do_periodo
    def tema(titulo, dia):
        return {"titulo": titulo, "categoria": "economia", "n_fontes": 3, "n_artigos": 3, "pontuacao": 12,
                "palavras": ["lucro", "setembro"], "fontes": ["A", "B", "C"],
                "artigos": [{"titulo": titulo, "link": titulo + str(i), "fonte": f, "data": dia}
                            for i, f in enumerate("ABC")]}
    outros = [tema(f"Empresa {x} contrata pessoas no Norte do país", "2026-11-01") for x in ("BCP", "BPI", "EDP")]
    dias = {"2026-11-02": [tema("Lucro do BCP sobe 12% para 873 milhões até setembro", "2026-11-02")],
            "2026-11-03": [tema("Lucro do BPI cai 7% para 363 milhões até setembro", "2026-11-03")],
            "2026-11-01": outros}
    historias = temas_do_periodo(dias)
    juntas = [h for h in historias if len(h["cronologia"]) > 1 and all("Lucro" in c["titulo"] for c in h["cronologia"])]
    assert juntas == []
