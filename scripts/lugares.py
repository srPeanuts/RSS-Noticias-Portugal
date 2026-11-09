"""
Terras, concelhos e regiões de Portugal (sem acentos, em minúsculas, como fica o texto depois de normalizado).

Servem para duas coisas:
- uma notícia que fala de outro país mas menciona uma terra portuguesa não é descartada;
- uma notícia sem palavras de nenhuma categoria, mas passada numa terra portuguesa, vai para Sociedade.

Ficam de fora, de propósito, nomes que também são palavras comuns ou apelidos
(Moura, Lagos, Chaves, Maia, Guarda, Horta, Lagoa, Paredes, Batalha, Gouveia, Espinho, Vagos, Cuba, Terceira,
Tomar, Caminha, Fátima, Resende, Entroncamento, Constância...).
"""

LUGARES_PT = """
alentejo algarve ribatejo minho douro tras-os-montes beira interior beira baixa beira alta estremadura
grande lisboa area metropolitana de lisboa area metropolitana do porto
lisboa porto coimbra braga faro aveiro setubal leiria evora viseu santarem beja portalegre braganca
vila real castelo branco viana do castelo funchal ponta delgada angra do heroismo
sintra cascais oeiras amadora odivelas loures mafra vila franca de xira almada seixal barreiro moita montijo
alcochete palmela sesimbra alcacer do sal grandola santiago do cacem sines odemira azambuja alenquer
torres vedras lourinha cadaval bombarral obidos peniche caldas da rainha alcobaca nazare marinha grande pombal
ourem torres novas abrantes golega chamusca almeirim alpiarca cartaxo
rio maior coruche benavente salvaterra de magos
matosinhos vila nova de gaia gondomar valongo penafiel amarante felgueiras lousada pacos de ferreira
santo tirso trofa famalicao vila nova de famalicao barcelos esposende povoa de varzim vila do conde guimaraes
fafe vizela ponte de lima valenca moncao melgaco arcos de valdevez ponte da barca vila nova de cerveira
paredes de coura povoa de lanhoso vieira do minho cabeceiras de basto celorico de basto marco de canaveses
baiao cinfaes castelo de paiva arouca sao joao da madeira oliveira de azemeis vale de cambra
santa maria da feira estarreja murtosa ovar ilhavo albergaria-a-velha agueda anadia mealhada
figueira da foz cantanhede montemor-o-velho condeixa lousa miranda do corvo penacova oliveira do hospital
tondela mangualde nelas lamego peso da regua regua tabuaco armamar sao pedro do sul castro daire
mirandela macedo de cavaleiros vinhais vimioso mogadouro miranda do douro torre de moncorvo alijo sabrosa
montalegre boticas valpacos murca
covilha fundao belmonte sabugal idanha-a-nova oleiros serta pedrogao grande figueiro dos vinhos
castanheira de pera proenca-a-nova vila de rei trancoso pinhel meda manteigas seia
elvas estremoz vila vicosa borba reguengos de monsaraz montemor-o-novo vendas novas ponte de sor
mertola serpa castro verde ourique vidigueira alvito marvao castelo de vide nisa
aljezur vila do bispo portimao monchique silves albufeira loule quarteira vilamoura olhao tavira
vila real de santo antonio castro marim alcoutim sao bras de alportel
camara de lobos machico porto santo ribeira grande vila franca do campo
""".strip().split("\n")

# cada linha tem vários nomes separados por espaços, mas há nomes compostos: estes são os compostos conhecidos
_COMPOSTOS = [
    "tras-os-montes", "beira interior", "beira baixa", "beira alta", "grande lisboa", "area metropolitana de lisboa",
    "area metropolitana do porto", "vila real", "castelo branco", "viana do castelo", "ponta delgada",
    "angra do heroismo", "vila franca de xira", "alcacer do sal", "santiago do cacem", "torres vedras",
    "caldas da rainha", "marinha grande", "torres novas", "rio maior", "salvaterra de magos", "vila nova de gaia",
    "pacos de ferreira", "santo tirso", "vila nova de famalicao", "povoa de varzim", "vila do conde",
    "ponte de lima", "arcos de valdevez", "ponte da barca", "vila nova de cerveira", "paredes de coura",
    "povoa de lanhoso", "vieira do minho", "cabeceiras de basto", "celorico de basto", "marco de canaveses",
    "castelo de paiva", "sao joao da madeira", "oliveira de azemeis", "vale de cambra", "santa maria da feira",
    "figueira da foz", "montemor-o-velho", "miranda do corvo", "oliveira do hospital", "peso da regua",
    "sao pedro do sul", "castro daire", "macedo de cavaleiros", "miranda do douro", "torre de moncorvo",
    "idanha-a-nova", "pedrogao grande", "figueiro dos vinhos", "castanheira de pera", "proenca-a-nova",
    "vila de rei", "vila vicosa", "reguengos de monsaraz", "montemor-o-novo", "vendas novas", "ponte de sor",
    "castro verde", "castelo de vide", "vila do bispo", "vila real de santo antonio", "castro marim",
    "sao bras de alportel", "camara de lobos", "porto santo", "ribeira grande", "vila franca do campo",
    "albergaria-a-velha",
]


def _lista() -> list:
    texto = " " + " ".join(LUGARES_PT) + " "
    nomes = []
    for c in sorted(_COMPOSTOS, key=len, reverse=True):
        if f" {c} " in texto:
            nomes.append(c)
            texto = texto.replace(f" {c} ", " ")
    nomes += texto.split()
    return sorted(set(nomes))


LUGARES = _lista()
