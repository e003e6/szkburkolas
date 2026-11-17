"""
v3_topologia – az R. rutin (topológia-frissítés), az éltábla és a cellaszintű geometriai
segédműveletek (T-csomópontok, T-feldarabolás, cella vágása vonallal).

Leírás: R. fejezet, 1.3 (attribútum-visszaillesztés), 3.2 és 6. (T-feldarabolás).

Az éltáblát SOHA nem frissítjük darabonként: minden fázis végén nulláról újraszámoljuk
(`topologia_frissites`). Ez a tipikus hibaforrás kizárása; településenként néhány ezer él,
térbeli indexszel elhanyagolható költség.
"""

from collections import Counter

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import split as shapely_split

import v3_param as P

# Az éltábla oszlopai (R. rutin 4. pont)
ELTABLA_OSZLOPOK = ['el_id', 'tipus', 'rang', 'utca_id', 'cella_bal', 'cella_jobb', 'hossz', 'geometry']
# A cellatábla oszlopai
CELLA_OSZLOPOK = ['cella_id', 'cellatipus', 'tomb_id', 'utca_id', 'utca_vonal_idx',
                  'szkid', 'szabaly', 'jelzes', 'sliver', 'geometry']


# ──────────────────────────────────────────────────────────────────────────────
# Alap-segédfüggvények
# ──────────────────────────────────────────────────────────────────────────────

def polygonize_full_nev_szerint(vonalak):
    """`shapely.polygonize_full` NÉV szerint kicsomagolva (leírás 1.7, 14.): a felső szintű
    függvény sorrendje (polygons, cuts, dangles, invalid). Egyetlen helyen, hogy a
    sorrend-hiba kizárt legyen. Minden érték lista."""
    poligonok, cutok, logok, ervenytelenek = shapely.polygonize_full(list(vonalak))
    return {
        'poligonok': list(poligonok.geoms),
        'cut': list(cutok.geoms),
        'logo': list(logok.geoms),
        'ervenytelen': list(ervenytelenek.geoms),
    }


def vonalak_darabokra(geometria):
    """Egy (Multi)LineString / GeometryCollection → a benne lévő, nem üres LineString-ek listája."""
    return [g for g in shapely.get_parts(geometria)
            if g.geom_type == 'LineString' and not g.is_empty and g.length > 0]


def poligonok_darabokra(geometria):
    """Egy (Multi)Polygon / GeometryCollection → a benne lévő, nem üres Polygon-ok listája."""
    return [g for g in shapely.get_parts(geometria)
            if g.geom_type == 'Polygon' and not g.is_empty and g.area > 0]


def vegpontok(vonal):
    """Egy LineString két végpontja (x, y) tuple-ként – RÁCS után egzaktan összehasonlítható."""
    koordinatak = shapely.get_coordinates(vonal)
    return tuple(koordinatak[0]), tuple(koordinatak[-1])


def vegpont_fokszam(vonalak):
    """Csomópont-fokszám: hány vonaldarab végződik az adott koordinátán (leírás 14.: a darabok
    végpont-koordinátáinak számlálása, RÁCS után egzakt)."""
    szamlalo = Counter()
    for vonal in vonalak:
        eleje, vege = vegpontok(vonal)
        szamlalo[eleje] += 1
        szamlalo[vege] += 1
    return szamlalo


def felezopont(vonal):
    """A vonaldarab felezőpontja (1.3: csak egy eredeti vonalon ül, a végpontok nem)."""
    return vonal.interpolate(0.5, normalized=True)


def _irany_a_ponton(vonal, arany):
    """A vonal érintő-irányvektora (egységnyi) az `arany` (0..1) normalizált helyen lévő pontban:
    annak a szakasznak az iránya, amelyen a pont ül."""
    koordinatak = shapely.get_coordinates(vonal)
    szakasz_hosszak = np.hypot(np.diff(koordinatak[:, 0]), np.diff(koordinatak[:, 1]))
    kumulalt = np.concatenate([[0.0], np.cumsum(szakasz_hosszak)])
    cel = arany * kumulalt[-1]
    i = int(np.searchsorted(kumulalt, cel, side='right') - 1)
    i = min(max(i, 0), len(szakasz_hosszak) - 1)
    irany = koordinatak[i + 1] - koordinatak[i]
    hossz = np.hypot(*irany)
    return irany / hossz if hossz > 0 else np.array([1.0, 0.0])


def normalvektor(irany):
    """Az irányvektorra merőleges egységvektor (bal oldal felé: +90°)."""
    return np.array([-irany[1], irany[0]])


def hosszabbit(vonal, hossz):
    """Egy LineString mindkét végét `hossz` méterrel meghosszabbítja a végszakasz irányában
    (a vágóvonalak túlnyúlása, hogy a split biztosan záródjon – leírás 8.4)."""
    koordinatak = shapely.get_coordinates(vonal)
    eleje_irany = koordinatak[0] - koordinatak[1]
    eleje_irany = eleje_irany / np.hypot(*eleje_irany)
    vege_irany = koordinatak[-1] - koordinatak[-2]
    vege_irany = vege_irany / np.hypot(*vege_irany)
    uj = np.vstack([koordinatak[0] + eleje_irany * hossz, koordinatak, koordinatak[-1] + vege_irany * hossz])
    return LineString(uj)


# ──────────────────────────────────────────────────────────────────────────────
# 1.3 Attribútum-visszaillesztés
# ──────────────────────────────────────────────────────────────────────────────

def attributum_visszaillesztes(darabok, eredeti_tabla, hianyzo_tipus=None, hianyzo_rang=None):
    """A darabok (LineString-lista) attribútumai az eredeti vonaltáblából a felezőpont alapján.

    index ← STRtree(eredeti geometriák); minden darab felezőpontjához az 1 cm-en (RÁCS) belüli
    eredeti vonalak:
      1 találat  → a darab örökli annak típusát, rangját, utca_id-jét;
      több       → rang: a legmagasabb; típus: a legmagasabb rangú találaté; utca_id: bármelyik
                   utca-találaté (hogy a sáv csoportosítható maradjon);
      0 találat  → HIBA, ha `hianyzo_tipus` nincs megadva (1.3: nem fordulhat elő);
                   az R. rutinban a sehová nem illeszkedő darab frissen szerkesztett vonal:
                   típusa `hianyzo_tipus`, rangja `hianyzo_rang` (1).
    Visszatérés: DataFrame [tipus, rang, utca_id], a darabok sorrendjében.
    """
    n = len(darabok)
    tipusok = [None] * n
    rangok = [None] * n
    utca_idk = [None] * n
    if n == 0:
        return pd.DataFrame({'tipus': tipusok, 'rang': rangok, 'utca_id': utca_idk})

    eredeti_geom = np.asarray(eredeti_tabla.geometry.values, dtype=object)
    eredeti_tipus = eredeti_tabla['tipus'].tolist()
    eredeti_rang = eredeti_tabla['rang'].tolist()
    eredeti_utca = eredeti_tabla['utca_id'].tolist()

    talalat_lista = [[] for _ in range(n)]
    if len(eredeti_geom) > 0:
        fa = shapely.STRtree(eredeti_geom)
        felezok = np.array([felezopont(d) for d in darabok], dtype=object)
        darab_idx, eredeti_idx = fa.query(felezok, predicate='dwithin', distance=P.RACS)
        for d, e in zip(darab_idx, eredeti_idx):
            talalat_lista[d].append(e)

    for i, talalatok in enumerate(talalat_lista):
        if len(talalatok) == 0:
            if hianyzo_tipus is None:
                raise RuntimeError(
                    f'Attribútum-visszaillesztés: a darabnak nincs eredeti vonala '
                    f'(felezőpont {felezopont(darabok[i]).wkt}). A RÁCS kerekítés maradt ki?')
            tipusok[i], rangok[i], utca_idk[i] = hianyzo_tipus, hianyzo_rang, None
            continue
        # a legmagasabb rangú találat adja a rangot és a típust (a határ nyer az utca felett)
        legjobb = max(talalatok, key=lambda e: eredeti_rang[e])
        tipusok[i] = eredeti_tipus[legjobb]
        rangok[i] = eredeti_rang[legjobb]
        # utca_id: bármelyik utca-találaté, ha van
        utca_talalatok = [e for e in talalatok if eredeti_tipus[e] == P.TIPUS_UTCA and eredeti_utca[e]]
        utca_idk[i] = eredeti_utca[utca_talalatok[0]] if utca_talalatok else eredeti_utca[legjobb]
    return pd.DataFrame({'tipus': tipusok, 'rang': rangok, 'utca_id': utca_idk})


# ──────────────────────────────────────────────────────────────────────────────
# Éltábla
# ──────────────────────────────────────────────────────────────────────────────

def _leghosszabb_szakasz_kozepe(vonal):
    """A vonal leghosszabb szakaszának felezőpontja normalizált (0..1) helyként – mindig egy
    szakasz belsejében van, soha nem csúcson (a zárt gyűrűk negyedelő pontjai mind csúcsok lennének)."""
    koordinatak = shapely.get_coordinates(vonal)
    szakasz_hosszak = np.hypot(np.diff(koordinatak[:, 0]), np.diff(koordinatak[:, 1]))
    i = int(np.argmax(szakasz_hosszak))
    kumulalt = np.concatenate([[0.0], np.cumsum(szakasz_hosszak)])
    return (kumulalt[i] + szakasz_hosszak[i] / 2.0) / kumulalt[-1]


def _oldalpontok(vonal, tavolsag, arany):
    """A vonal `arany` helyén lévő pontból a normál mentén ±`tavolsag`-ra eltolt két pont
    (bal, jobb) a bal/jobb cella megkereséséhez."""
    pont = shapely.get_coordinates(vonal.interpolate(arany, normalized=True))[0]
    n = normalvektor(_irany_a_ponton(vonal, arany))
    return Point(pont + n * tavolsag), Point(pont - n * tavolsag)


def eltabla_epites(vonalak, attributumok, cellak):
    """Éltábla (R. rutin 4. pont): minden vonaldarabhoz a két cella, amit elválaszt.

    cella_bal  ← az a cella, amelyben (felezőpont + 1 cm · normál) van; nincs: külvilág (−1)
    cella_jobb ← az a cella, amelyben (felezőpont − 1 cm · normál) van
    Ha a felezőpont éppen csúcson ül (pl. zárt gyűrű negyedelő pontja) és egyik oldalon sincs
    cella, a leghosszabb szakasz felezőpontjánál mintavételezünk újra – vonal nem mozdul, csak a
    mintavételi hely más.
    `attributumok`: DataFrame [tipus, rang, utca_id] a darabok sorrendjében.
    """
    n = len(vonalak)
    cella_bal = np.full(n, P.KULVILAG, dtype=int)
    cella_jobb = np.full(n, P.KULVILAG, dtype=int)
    if n > 0 and len(cellak) > 0:
        fa = shapely.STRtree(np.asarray(cellak.geometry.values, dtype=object))
        cella_idk = cellak['cella_id'].to_numpy()
        # 1. kör: a felezőpontnál; 2. kör: a leghosszabb szakasz közepénél, csak ahol kell
        for kor in (1, 2):
            if kor == 1:
                hianyzo = np.arange(n)
                aranyok = [0.5] * n
            else:
                hianyzo = np.where((cella_bal == P.KULVILAG) & (cella_jobb == P.KULVILAG))[0]
                aranyok = [_leghosszabb_szakasz_kozepe(vonalak[i]) for i in hianyzo]
            if len(hianyzo) == 0:
                break
            balok, jobbok = [], []
            for i, arany in zip(hianyzo, aranyok):
                b, j = _oldalpontok(vonalak[i], P.RACS, arany)
                balok.append(b)
                jobbok.append(j)
            bal_idx, bal_cella = fa.query(np.array(balok, dtype=object), predicate='within')
            jobb_idx, jobb_cella = fa.query(np.array(jobbok, dtype=object), predicate='within')
            for d, c in zip(bal_idx, bal_cella):
                cella_bal[hianyzo[d]] = cella_idk[c]
            for d, c in zip(jobb_idx, jobb_cella):
                cella_jobb[hianyzo[d]] = cella_idk[c]
    eltabla = gpd.GeoDataFrame({
        'el_id': np.arange(n, dtype=int),
        'tipus': attributumok['tipus'].tolist(),
        'rang': attributumok['rang'].tolist(),
        'utca_id': attributumok['utca_id'].tolist(),
        'cella_bal': cella_bal,
        'cella_jobb': cella_jobb,
        'hossz': [v.length for v in vonalak],
    }, geometry=list(vonalak), crs=cellak.crs)
    return eltabla[ELTABLA_OSZLOPOK]


def cella_elei(eltabla, cella_id):
    """Az éltábla sorai, amelyek az adott cellát határolják (bal vagy jobb oldalon)."""
    return eltabla[(eltabla['cella_bal'] == cella_id) | (eltabla['cella_jobb'] == cella_id)]


def szomszed_id(el, cella_id):
    """Egy él túloldali cellája az adott cella felől nézve."""
    return el.cella_jobb if el.cella_bal == cella_id else el.cella_bal


def szomszedok_osszesitve(eltabla, cella_id, csak_cellak=None):
    """Egy cella szomszédai az éltáblából: {szomszéd_id: (a közös élek legkisebb rangja,
    a közös élek összhossza)}. A külvilág (−1) nem szomszéd. `csak_cellak`: ha megadott,
    csak az ebben szereplő szomszédok számítanak (pl. csak besoroltak, csak nem-sliverek)."""
    elek = cella_elei(eltabla, cella_id)
    osszesites = {}
    for el in elek.itertuples():
        masik = szomszed_id(el, cella_id)
        if masik == P.KULVILAG or masik == cella_id:
            continue
        if csak_cellak is not None and masik not in csak_cellak:
            continue
        rang, hossz = osszesites.get(masik, (99, 0.0))
        osszesites[masik] = (min(rang, el.rang), hossz + el.hossz)
    return osszesites


def nyertes_szomszed(osszesites):
    """A beolvasztás szabálya (0.4): a legkisebb rangú közös él; egyenlő rangnál a leghosszabb.
    Visszatérés: (szomszéd_id, rang, hossz) vagy None."""
    if not osszesites:
        return None
    szomszed, (rang, hossz) = min(osszesites.items(), key=lambda t: (t[1][0], -t[1][1], t[0]))
    return szomszed, rang, hossz


# ──────────────────────────────────────────────────────────────────────────────
# Címek a cellákban (R. rutin 5. pont)
# ──────────────────────────────────────────────────────────────────────────────

def cimek_cellaba(cimek, cellak):
    """Minden címpont a cellája `cella_id`-jét kapja (a cella BELSEJÉBEN lévő pontok; a határon
    ülők és a lakott területen kívüliek egyik cellához sem tartoznak → None)."""
    cimek = cimek.copy()
    cimek['cella_id'] = None
    if len(cellak) == 0 or len(cimek) == 0:
        return cimek
    fa = shapely.STRtree(np.asarray(cellak.geometry.values, dtype=object))
    cim_idx, cella_idx = fa.query(np.asarray(cimek.geometry.values, dtype=object), predicate='within')
    cella_idk = cellak['cella_id'].to_numpy()
    ertekek = [None] * len(cimek)
    for ci, ce in zip(cim_idx, cella_idx):
        ertekek[ci] = int(cella_idk[ce])
    cimek['cella_id'] = ertekek
    return cimek


# Címek, amelyek a besorolásban nem számítanak: a hibás, egycímes szigetek címei (10.1) és az
# azonos koordinátán eltérő kategóriával ülő kisebbségi címek (terv #7)
KIHAGYOTT_CIM_JELZESEK = (P.JELZES_HIBAS_SZIGET, P.JELZES_AZONOS_PONT)


def szamito_cimek(cimek):
    """A besorolásban számító címek: amelyeknek nincs kihagyó jelzése."""
    kihagy = cimek['jelzes'].fillna('').apply(lambda j: any(k in j for k in KIHAGYOTT_CIM_JELZESEK))
    return cimek[~kihagy]


def cella_kategoriak(cimek):
    """Cellánként a benne lévő, számító címek kategóriái: {cella_id: Counter(szkid → db)}."""
    eredmeny = {}
    ervenyes = szamito_cimek(cimek)
    ervenyes = ervenyes[ervenyes['cella_id'].notna()]
    for cella_id, csoport in ervenyes.groupby('cella_id'):
        eredmeny[int(cella_id)] = Counter(csoport['szkid'].tolist())
    return eredmeny


# ──────────────────────────────────────────────────────────────────────────────
# Cellatábla segédfüggvények
# ──────────────────────────────────────────────────────────────────────────────

def uj_cellatabla(sorok, crs):
    """Cellatábla a kötelező oszlopokkal; a hiányzó attribútumok alapértéket kapnak."""
    df = pd.DataFrame(sorok)
    for oszlop, alap in [('tomb_id', None), ('utca_id', None), ('utca_vonal_idx', None),
                         ('szkid', None), ('szabaly', None), ('jelzes', ''), ('sliver', False)]:
        if oszlop not in df.columns:
            df[oszlop] = alap
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs=crs)
    return gdf[CELLA_OSZLOPOK].reset_index(drop=True)


def kovetkezo_cella_id(cellak):
    """Az első szabad cella_id (az id-k monoton nőnek, törölt id nem kerül újra kiosztásra)."""
    return int(cellak['cella_id'].max()) + 1 if len(cellak) else 0


# ──────────────────────────────────────────────────────────────────────────────
# R. Topológia-frissítés
# ──────────────────────────────────────────────────────────────────────────────

def _vonalak_torese_csomopontoknal(vonalak, csomopontok):
    """A láncokat elvágja minden olyan BELSŐ csúcsuknál, amely az előző éltábla valamelyik
    élének végpontja. Ok: a cellahatárok uniója csak ott ad csomópontot, ahol ≥3 cella
    találkozik; egy utca-él és egy narancs-él 2-fokú találkozásánál a két él egy láncba olvadna,
    és a felezőpontos visszaillesztés az egyik attribútumát elveszítené. Vonal nem mozdul."""
    eredmeny = []
    for vonal in vonalak:
        koordinatak = shapely.get_coordinates(vonal)
        kezdet = 0
        for i in range(1, len(koordinatak) - 1):
            if tuple(koordinatak[i]) in csomopontok:
                eredmeny.append(LineString(koordinatak[kezdet:i + 1]))
                kezdet = i
        eredmeny.append(LineString(koordinatak[kezdet:]))
    return eredmeny


def topologia_frissites(cellak, eltabla_elozo, cimek, uj_vonal_tipus=P.TIPUS_VAGOVONAL):
    """R. rutin – minden fázis végén. Biztosítja, hogy a cellák közös élei bájtra pontosan
    egybeessenek, és az éltábla a teljes aktuális állapotot tükrözze.

    1. vonalak ← az összes cella határa egy lépésben összefésülve, RÁCS rácson
    2. új cellák ← polygonize(vonalak); a lakott terület lyukaiban keletkező lapokat eldobjuk
       (nincs régi cella, amelyben a reprezentatív pontjuk lenne)
    3. attribútumok ← annak a régi cellának az attribútumai, amelyben a reprezentatív pont van
    4. új éltábla: bal/jobb cella, attribútumok az előző éltáblából (1.3); a sehová nem
       illeszkedő darab ebben a fázisban szerkesztett vonal → `uj_vonal_tipus`, rang 1
    5. címek ← a cellák belsejében lévő címpontok, újraszámolva
    Visszatérés: (cellak, eltabla, cimek).
    """
    crs = cellak.crs

    # 1. összefésülés RÁCS rácson
    hatarok = shapely.set_precision(np.asarray(cellak.geometry.boundary.values, dtype=object), P.RACS)
    unio = shapely.unary_union(hatarok, grid_size=P.RACS)
    vonalak = vonalak_darabokra(unio)
    # élek törése az előző éltábla végpontjainál (attribútum-megőrzés, lásd a docstringet)
    regi_csomopontok = set()
    for geom in eltabla_elozo.geometry:
        eleje, vege = vegpontok(geom)
        regi_csomopontok.add(eleje)
        regi_csomopontok.add(vege)
    vonalak = _vonalak_torese_csomopontoknal(vonalak, regi_csomopontok)

    # 2. poligonizálás
    lapok = polygonize_full_nev_szerint(vonalak)['poligonok']

    # 3. attribútumok a régi cellából (reprezentatív pont)
    regi_fa = shapely.STRtree(np.asarray(cellak.geometry.values, dtype=object))
    rep_pontok = np.array([lap.representative_point() for lap in lapok], dtype=object)
    lap_idx, regi_idx = regi_fa.query(rep_pontok, predicate='within')
    lap_regi = {}
    for li, ri in zip(lap_idx, regi_idx):
        lap_regi.setdefault(li, ri)
    regi_sorok = cellak.to_dict('records')
    uj_sorok = []
    felhasznalt_id = set()
    kovetkezo_id = kovetkezo_cella_id(cellak)
    for li, lap in enumerate(lapok):
        if li not in lap_regi:
            continue  # lyuk a lakott területben: nincs régi cella → eldobjuk
        sor = dict(regi_sorok[lap_regi[li]])
        sor['geometry'] = lap
        if sor['cella_id'] in felhasznalt_id:
            # egy régi (több részből álló) cella több lapra esett: a többi lap új id-t kap
            sor['cella_id'] = kovetkezo_id
            kovetkezo_id += 1
        felhasznalt_id.add(sor['cella_id'])
        uj_sorok.append(sor)
    uj_cellak = uj_cellatabla(uj_sorok, crs).sort_values('cella_id').reset_index(drop=True)

    # 4. új éltábla
    attributumok = attributum_visszaillesztes(vonalak, eltabla_elozo,
                                             hianyzo_tipus=uj_vonal_tipus, hianyzo_rang=P.RANG_VAZVONAL)
    eltabla = eltabla_epites(vonalak, attributumok, uj_cellak)

    # 5. címek a cellákba
    cimek = cimek_cellaba(cimek, uj_cellak)
    return uj_cellak, eltabla, cimek


# ──────────────────────────────────────────────────────────────────────────────
# 3.2 / 6. T-csomópontok és T-feldarabolás
# ──────────────────────────────────────────────────────────────────────────────

def vegpont_index(eltabla):
    """{csúcs-koordináta: [(irány a csúcsból kifelé, cella_bal, cella_jobb), ...]} az éltábla
    minden élének mindkét végpontjára – a T-csomópontok kereséséhez."""
    index = {}
    for el in eltabla.itertuples(index=False):
        koordinatak = shapely.get_coordinates(el.geometry)
        for csucs, szomszed in ((koordinatak[0], koordinatak[1]), (koordinatak[-1], koordinatak[-2])):
            irany = szomszed - csucs
            hossz = np.hypot(*irany)
            if hossz == 0:
                continue
            index.setdefault(tuple(csucs), []).append((irany / hossz, el.cella_bal, el.cella_jobb))
    return index


# Egy idegen vonal akkor a cella saját élének folytatása, ha az iránya ennyi fokon belül egybeesik vele
KOLLINEARIS_SZOG_FOK = 5.0


def t_csomopontok(cella_id, cella_geom, index):
    """A cella határának azon csúcsai, amelyekben a cella határához nem tartozó vonaldarab is
    végződik vagy áthalad (éltáblából: ≥ 3 él a csúcsban). Az idegen él nem számít, ha a cella
    saját élének egyenes folytatása (a csúcsból kifelé mérve az iránya a saját él irányának
    ellentettje; pl. a cella sarkában egyenesen továbbfutó utca): ott a
    határra merőleges vágás csak egy pár méteres sarokháromszöget vágna le, információ nélkül.
    Visszatérés: [(csúcs, előző csúcs, következő csúcs)] a vágás irányához."""
    talalatok = []
    sin_tures = np.sin(np.radians(KOLLINEARIS_SZOG_FOK))
    gyuruk = [cella_geom.exterior] + list(cella_geom.interiors)
    for gyuru in gyuruk:
        koordinatak = shapely.get_coordinates(gyuru)[:-1]  # a záró pont ismétlés, nem kell
        n = len(koordinatak)
        for i in range(n):
            csucs = koordinatak[i]
            elek = index.get(tuple(csucs), [])
            if len(elek) < 3:
                continue
            elozo, kovetkezo = koordinatak[i - 1], koordinatak[(i + 1) % n]
            sajat_iranyok = []
            for szomszed in (elozo, kovetkezo):
                v = szomszed - csucs
                sajat_iranyok.append(v / np.hypot(*v))
            van_idegen = False
            for irany, bal, jobb in elek:
                if bal == cella_id or jobb == cella_id:
                    continue  # a cella saját éle
                kollinearis = any(np.dot(irany, s) < 0 and abs(irany[0] * s[1] - irany[1] * s[0]) < sin_tures
                                  for s in sajat_iranyok)
                if not kollinearis:
                    van_idegen = True
                    break
            if van_idegen:
                talalatok.append((csucs, elozo, kovetkezo))
    return talalatok


def t_vagovonal(cella_geom, csucs, elozo, kovetkezo):
    """Vágóvonal egy T-csomópontból: a határra merőlegesen a cella belseje felé, az első
    kilépésig a cellából (a szemközti élig). A határ érintője a két szomszédos határszakasz
    átlagolt iránya; a belső irányt a csúcs melletti próbapont dönti el. Visszatérés: a
    vágóvonal mindkét végén VAGOVONAL_TULNYULAS-sal meghosszabbítva, vagy None."""
    csucs = np.asarray(csucs, dtype=float)
    be = csucs - np.asarray(elozo, dtype=float)
    ki = np.asarray(kovetkezo, dtype=float) - csucs
    be_h, ki_h = np.hypot(*be), np.hypot(*ki)
    if be_h == 0 or ki_h == 0:
        return None
    erinto = be / be_h + ki / ki_h
    if np.hypot(*erinto) < 1e-9:
        erinto = be / be_h   # visszaforduló határ: a bejövő irány
    erinto = erinto / np.hypot(*erinto)
    n = normalvektor(erinto)
    # a belső irány kiválasztása
    probatav = 5 * P.RACS
    if cella_geom.contains(Point(csucs + n * probatav)):
        irany = n
    elif cella_geom.contains(Point(csucs - n * probatav)):
        irany = -n
    else:
        return None
    atlo = np.hypot(cella_geom.bounds[2] - cella_geom.bounds[0], cella_geom.bounds[3] - cella_geom.bounds[1])
    sugar = LineString([csucs, csucs + irany * (atlo + 1.0)])
    metszet = sugar.intersection(cella_geom)
    # a csúcsból induló komponens = az első kilépésig tartó szakasz
    jelolt = None
    for darab in vonalak_darabokra(metszet):
        if darab.distance(Point(csucs)) <= P.RACS:
            if jelolt is None or darab.length < jelolt.length:
                jelolt = darab
    if jelolt is None or jelolt.length <= P.RACS:
        return None
    return hosszabbit(jelolt, P.VAGOVONAL_TULNYULAS)


def cella_felosztas(cella_geom, vagovonalak):
    """Egy cella belső felosztása vágóvonalakkal (`shapely.ops.split`). A vágóvonalak egyszerre
    hatnak. Visszatérés: a darabok (Polygon-lista); ha a vágás nem változtat, [cella_geom]."""
    vagovonalak = [v for v in vagovonalak if v is not None and not v.is_empty]
    if not vagovonalak:
        return [cella_geom]
    vago = shapely.unary_union(vagovonalak)
    try:
        darabok = poligonok_darabokra(shapely_split(cella_geom, vago))
    except Exception:
        return [cella_geom]
    if len(darabok) <= 1:
        return [cella_geom]
    return darabok


def t_feldarabolas(cellak, eltabla, cella_idk, debug=None, lepes='3.2'):
    """T-feldarabolás (3.2, 6.): a megadott cellák mindegyikét elvágjuk minden T-csomópontjánál.
    Visszatérés: (új cellatábla, T-csomópontok listája, vágóvonalak listája) – az utóbbi kettő
    a debug rétegekhez."""
    index = vegpont_index(eltabla)
    t_pontok, t_vonalak = [], []
    uj_sorok = []
    kovetkezo_id = kovetkezo_cella_id(cellak)
    for sor in cellak.itertuples(index=False):
        if sor.cella_id not in cella_idk:
            uj_sorok.append(sor._asdict())
            continue
        vagovonalak = []
        for csucs, elozo, kovetkezo in t_csomopontok(sor.cella_id, sor.geometry, index):
            t_pontok.append(Point(csucs))
            vago = t_vagovonal(sor.geometry, csucs, elozo, kovetkezo)
            if vago is not None:
                vagovonalak.append(vago)
                t_vonalak.append(vago)
        darabok = cella_felosztas(sor.geometry, vagovonalak)
        if len(darabok) == 1:
            uj_sorok.append(sor._asdict())
            continue
        # az első darab megtartja a cella id-jét, a többi darab sorban új id-t kap
        for i, darab in enumerate(darabok):
            uj = sor._asdict()
            uj['geometry'] = darab
            if i > 0:
                uj['cella_id'] = kovetkezo_id
                kovetkezo_id += 1
            uj_sorok.append(uj)
    uj_cellak = uj_cellatabla(uj_sorok, cellak.crs)
    return uj_cellak, t_pontok, t_vonalak
