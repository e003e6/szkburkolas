"""
v3_param – a V3 burkolás paraméterei, él-típusai, rangjai, színei.

A leírás (B-burkolas-v3-mukodes.md) 0.4, 0.5 és 11.1 fejezete. Minden szám, ami a
folyamatban szerepel, itt van egy helyen, névvel és indoklással; a lépés-modulok innen
importálnak, saját számot egyik sem tartalmaz.
"""

import colorsys

# ──────────────────────────────────────────────────────────────────────────────
# 0.5 Nevesített paraméterek – a módszer NÉGY paramétere, mind levezetett érték
# ──────────────────────────────────────────────────────────────────────────────

# TELEKSZÉLESSÉG: a HÉSZ-ek övezetenként 8–25 m telekszélességet írnak elő; a 6 m
# konzervatív alsó érték, ami alatt sorház sem állhat. Ez alatt a cím nélküli cella sliver (3.).
TELEKSZELESSEG = 6.0

# SAROKMÉRET: a saroktelek egy telekszélességnyi (14–20 m), a sarokház a sarok 20–25 m-én
# belül áll; a 30 m ezt ráhagyással fedi (5.).
SAROKMERET = 30.0

# VÁZFELBONTÁS: a vázvonalat 2 m-enként vett határpontokból számoljuk; a legnagyobb fogazat
# a pontköz fele (1 m), ami a TELEKSZÉLESSÉG alatt marad, így a vázvonal nem csinál slivert (4.).
VAZFELBONTAS = 2.0

# RÁCS: 1 cm-es koordináta-rács a lebegőpontos zaj ellen; a geodéziai pontosság alatt (1.2, R.).
RACS = 0.01

# ──────────────────────────────────────────────────────────────────────────────
# A leírásban szereplő, nem paraméternek számító technikai konstansok
# (a leírás adott pontja mondja ki az értéket; itt csak névvel látjuk el)
# ──────────────────────────────────────────────────────────────────────────────

# 1.5: a narancs sugár 0,25 m-en belüli találata a kiindulópont maga, nem számít
NARANCS_SAJAT_TAVOLSAG = 0.25

# 8.4: a cellahatárt érintő vágóvonal-végek 1 m-rel meghosszabbítva, hogy a split záródjon;
# ugyanezt a túlnyúlást használja minden szerkesztett vágóvonal (3.2, 5., 6., 8.)
VAGOVONAL_TULNYULAS = 1.0

# 8.4: a Douglas–Peucker tűrés bináris keresésének lépésszáma
VORONOI_KERESES_LEPES = 18

# 8.1: az érintő irányát a vetítési pont ± ennyi méteres szomszédjából vesszük (görbe utcánál is egyértelmű)
ERINTO_FELHOSSZ = 0.5

# 10.3 validáció küszöbei
ATFEDES_TURES_M2 = 0.001           # átfedés > 0,001 m² → hiba
LEFEDES_TURES_ABS_M2 = 5.0         # lefedés eltérése ≤ max(5 m², 0,01 %)
LEFEDES_TURES_REL = 0.0001

# 0.4 QA-küszöbök (nem az eljárás paraméterei: a kimenetet nem változtatják, csak jeleznek)
QA_CIMEK_KIVUL_KUSZOB = 0.10       # (a) a lakott területen kívüli címek aránya > 10 %
QA_FOUTAK_KIVUL_KUSZOB = 0.30      # (b) a levágott rang 4–5 úthossz aránya > 30 %

# ──────────────────────────────────────────────────────────────────────────────
# 0.4 Él-típusok és rangok
# ──────────────────────────────────────────────────────────────────────────────

TIPUS_LAKOTT_HATAR = 'lakott határ'
TIPUS_UTCA = 'utca'
TIPUS_VASUT = 'vasút'
TIPUS_FOLYO = 'folyó'
TIPUS_SEGEDVONAL = 'segédvonal'     # narancs
TIPUS_VAZVONAL = 'vázvonal'
TIPUS_VAGOVONAL = 'vágóvonal'

RANG_VAZVONAL = 1        # vázvonal, vágóvonal – a rendszer szerkeszti (4., 5., 8.)
RANG_SEGEDVONAL = 2      # narancs segédvonal (1.5)
RANG_GYALOGUT = 3        # gyalogút, ösvény, szervizút, lépcső, kerékpárút
RANG_LAKOUTCA = 4        # lakóutca
RANG_FOUT = 5            # főút
RANG_VASUT_FOLYO = 6     # vasút, folyó
RANG_LAKOTT_HATAR = 7    # lakott terület határa – ezen soha nem olvad át semmi

# OSM highway címke → rang (0.4 táblázat). A listában nem szereplő címkék kezelése lent.
HIGHWAY_RANG = {
    # rang 3
    'footway': RANG_GYALOGUT, 'path': RANG_GYALOGUT, 'pedestrian': RANG_GYALOGUT,
    'steps': RANG_GYALOGUT, 'service': RANG_GYALOGUT, 'cycleway': RANG_GYALOGUT,
    'track': RANG_GYALOGUT,
    # rang 4
    'residential': RANG_LAKOUTCA, 'living_street': RANG_LAKOUTCA, 'unclassified': RANG_LAKOUTCA,
    'tertiary': RANG_LAKOUTCA, 'tertiary_link': RANG_LAKOUTCA,
    # rang 5
    'primary': RANG_FOUT, 'secondary': RANG_FOUT, 'trunk': RANG_FOUT, 'motorway': RANG_FOUT,
    'primary_link': RANG_FOUT, 'secondary_link': RANG_FOUT, 'trunk_link': RANG_FOUT,
    'motorway_link': RANG_FOUT,
}

# Bemeneti eltérés (terv #5): az 'all' hálózatban a táblán kívüli highway-értékek is vannak.
# 'road' (ismeretlen besorolású, de gépjárműút) → rang 4; minden más ismeretlen → rang 3,
# vagyis lógó végként törlődik, ha nem kapcsolódik mindkét végén (1.4).
HIGHWAY_RANG_ROAD = RANG_LAKOUTCA
RANG_ISMERETLEN_HIGHWAY = RANG_GYALOGUT

# ──────────────────────────────────────────────────────────────────────────────
# Cella-attribútumok értékkészlete
# ──────────────────────────────────────────────────────────────────────────────

CELLA_TOMB = 'tömb'
CELLA_FOLT = 'folt'
CELLA_SAV = 'sáv'
CELLA_SAROK = 'sarok'

KULVILAG = -1            # éltábla: a lakott határ túloldala, nincs cella

SZABALY_CIMES = 'címes'
SZABALY_FOLYTONOSSAG = 'folytonosság'
SZABALY_RANG = 'rang'
SZABALY_LEGKOZELEBBI = 'legközelebbi'

# 7. lépés állapotai
ALLAPOT_EGYKATEGORIAS = 'egykategóriás'
ALLAPOT_VEGYES = 'vegyes'
ALLAPOT_URES = 'üres'

# 8. lépés vágástípusai (12. fejezet: szerkezeti mutatók)
VAGAS_T = 'merőleges-T'
VAGAS_KOZEP = 'merőleges-közép'
VAGAS_KOZOS = 'közös-vonal'        # 8.2 egyeztetett vágás
VAGAS_PARHUZAMOS = 'párhuzamos'
VAGAS_VORONOI = 'Voronoi'

# Jelzések (11. kimenet `jelzes` oszlopa; több jelzés ';'-vel fűzve)
JELZES_FOLYTONOSSAG = 'eltérő-folytonosság'
JELZES_HAROMSZOG = 'egycímes-háromszög'
JELZES_VALIDACIO = 'validáció-hiba'
JELZES_AZONOS_PONT = 'azonos-pont'          # terv #7: egy koordinátán több kategória
JELZES_HIBAS_SZIGET = 'hibás-sziget'        # 10.1: egycímes sziget címe
JELZES_BESOROLATLAN = 'besorolatlan'        # 9.3: elszigetelt, cím nélküli folt
JELZES_LAKOTT_GYANUS = 'lakott terület gyanús'  # 0.4 QA

# ──────────────────────────────────────────────────────────────────────────────
# 11.1 Színrendszer a debug GPKG-hez (vonal színe = eredet, cella kitöltése = állapot)
# ──────────────────────────────────────────────────────────────────────────────

SZIN = {
    'lakott határ': '#000000',
    'közigazgatási határ': '#555555',
    'rang 3': '#AAAAAA',
    'rang 4': '#1F77B4',
    'rang 5': '#08306B',
    'vasút': '#8C564B',
    'folyó': '#17BECF',
    'törölt vonal': '#D62728',
    'narancs': '#FF7F0E',
    'vázvonal': '#9467BD',
    'sarok-merőleges': '#E377C2',
    'vágóvonal T': '#2CA02C',
    'vágóvonal közép': '#98DF8A',
    'vágóvonal párhuzamos': '#17BECF',
    'vágóvonal Voronoi': '#D62728',
    'Voronoi nyers sejt': '#F7B6B6',
    'T-vágás': '#BCBD22',
    'cella vegyes': '#FFBB78',
    'cella üres': '#C7C7C7',
    'cella sliver': '#F7B6D2',
    'cella sarok': '#F2C6E6',
    'jelzés': '#D62728',
    'határpont': '#7F7F7F',
    'nyíl': '#333333',
    'segéd': '#999999',
}


def rang_szin(rang, tipus=None):
    """Egy vonaldarab színe a rangja (és típusa) szerint a 11.1 táblázat alapján."""
    if tipus == TIPUS_VASUT:
        return SZIN['vasút']
    if tipus == TIPUS_FOLYO:
        return SZIN['folyó']
    if tipus == TIPUS_SEGEDVONAL or rang == RANG_SEGEDVONAL:
        return SZIN['narancs']
    if tipus == TIPUS_VAZVONAL:
        return SZIN['vázvonal']
    if tipus == TIPUS_VAGOVONAL:
        return SZIN['vágóvonal T']
    if rang == RANG_LAKOTT_HATAR:
        return SZIN['lakott határ']
    if rang == RANG_GYALOGUT:
        return SZIN['rang 3']
    if rang == RANG_LAKOUTCA:
        return SZIN['rang 4']
    if rang == RANG_FOUT:
        return SZIN['rang 5']
    return SZIN['segéd']


def kategoria_paletta(szkid_ertekek):
    """A `szkid` szerinti paletta: minden kategória egy jól elkülönülő, determinisztikus színt kap.
    Aranymetszés-léptetés a színkörön, a kategóriák rendezett sorrendjében (így ugyanaz a
    település mindig ugyanazt a palettát kapja)."""
    paletta = {}
    for i, szkid in enumerate(sorted(set(szkid_ertekek))):
        hue = (i * 0.618033988749895) % 1.0
        ertek = 0.92 if i % 2 == 0 else 0.78
        r, g, b = colorsys.hsv_to_rgb(hue, 0.62, ertek)
        paletta[szkid] = '#%02x%02x%02x' % (int(r * 255), int(g * 255), int(b * 255))
    return paletta
