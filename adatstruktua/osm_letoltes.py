'''
Letölti minden városra (data/nyers_data/varosok.txt) az OSM adatokat WGS84-ben:
  - varos_kozigazgatasi_hatar.parquet   (hivatalos közig. határ, geocode_to_gdf)
  - varos_lakottterulet_hatar.parquet   (lakott terület, landuse=residential)
  - utcak_nodes.parquet
  - utcak_edges.parquet
  V3 burkoláshoz (osm_letoltes_v3, külön parquet-ek, a fentiek érintetlenek):
  - utcak_all_edges.parquet              (network_type='all': minden járható út, gyalogút is)
  - vasut_folyo.parquet                  (railway=rail, waterway=river/stream/canal vonalak)

Futtatási módok:
  - osm_letoltes()                      → teljes varosok.txt, felülírás (régi viselkedés),
                                          a végén a Budapest-ág is lefut.
  - osm_letoltes(varosok, append=True)  → csak a megadott települések, a meglévő parquet-okba
                                          fésülve (idempotens csere: az újratöltött települések
                                          régi sorai cserélődnek, minden más változatlan).
  - hianyzo_varosok()                   → varosok.txt mínusz a határ-parquetben már meglévők.

BUDAPEST KÜLÖN ÁGON megy (budapest_keruletek_letoltes): a Nominatim a kerületnevekre
0 találatot ad ({'city': 'Budapest III. kerület'} → hiba), ezért egész Budapestet töltjük
le egyszer (határ + lakott terület + útgráf), és a kerület-közigazgatási határok
(admin_level=9) mentén vágjuk kerületekre. A "Budapest" egész-városi sor a határ- és
lakott-parquet-ba is bekerül (az MS-szűrésnek egyben is kell), az útgráf csak
kerületenként tárolódik.

Hiba esetén printel és továbblép.

Ez használjuk a burkolas_v3-ban

Valaint a Microsoft adatok szűrésére, is ennek a lakott területi határait használjuk,
hogy tudjuk, hogy hol nincsenek adatok.
'''

import json
import re
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd
import pyarrow.parquet as pq
from shapely.validation import make_valid

from szkid_cim_kapcsolas import BP_KERULET_NEV, kerulet_from_telepules_nev


DATA = Path(__file__).resolve().parent.parent / 'data' / 'nyers_data'

HATAR_PARQUET = DATA / 'varos_kozigazgatasi_hatar.parquet'
LAKOTT_PARQUET = DATA / 'varos_lakottterulet_hatar.parquet'
NODES_PARQUET = DATA / 'utcak_nodes.parquet'
EDGES_PARQUET = DATA / 'utcak_edges.parquet'
EDGES_ALL_PARQUET = DATA / 'utcak_all_edges.parquet'   # V3: 'all' hálózat élei
VASUT_FOLYO_PARQUET = DATA / 'vasut_folyo.parquet'     # V3: vasút + folyó vonalak
HIBAK_TXT = DATA / 'osm_letoltes_hibak.txt'

# V3 rétegnevek a hibafájlban (a régi 'határ' / 'lakott' / 'utcahálózat' mellett)
HIBA_RETEG_ALL = 'utcahálózat_all'
HIBA_RETEG_VASUT = 'vasut_folyo'

# A V3 burkolásnak ezek az oszlopok kellenek az 'all' élekből (a többi osmnx-oszlop fölösleges)
ALL_EDGE_OSZLOPOK = ['u', 'v', 'key', 'osmid', 'highway', 'name', 'service', 'geometry']
VASUT_FOLYO_TAGS = {'railway': 'rail', 'waterway': ['river', 'stream', 'canal']}

ox.settings.use_cache = True
ox.settings.requests_timeout = 300

BUDAPEST_QUERY = {'city': 'Budapest', 'country': 'Hungary'}  # város-szint: ez működik a Nominatimon


def _nan(v):
    return v is None or (isinstance(v, float) and v != v)


def to_json_strings(df):
    # Az osmnx néhány oszlopában (osmid, highway, name, ...) ugyanaz a mező
    # egyik sorban scalar, másikban lista — pyarrow ezt nem tudja parquetbe írni.
    # Ahol legalább egy cellában container van, ott MINDEN nem-None értéket
    # JSON-stringgé alakítunk (scalar int → "123" is, hogy az oszlop egyértékű
    # string legyen). A beolvasó visszafejti. A NaN (merge-nél oszlop-unióból
    # keletkezik) változatlanul megy át — json.dumps "NaN" szemetet csinálna belőle.
    for col in df.columns:
        if col in ('geometry', 'telepules') or df[col].dtype != object:
            continue
        if any(isinstance(v, (list, tuple, dict, set)) for v in df[col]):
            df[col] = df[col].apply(
                lambda v: v if _nan(v) or isinstance(v, str)
                else json.dumps(list(v) if isinstance(v, (set, tuple)) else v, default=str)
            )
    return df


def _stringesit_kevert_oszlopok(df):
    """Merge-drift javítás: ha egy object oszlopban string (régi, JSON-osított) ÉS nyers
    skalár (friss letöltés: bool/int/float) is van, a nem-string értékeket is JSON-stringgé
    alakítjuk — container híján a to_json_strings nem triggerel, a pyarrow írás viszont
    elhasalna a kevert oszlopon. A polygon_io._restore_mixed_object_cols mindkét alakot
    visszafejti (json.loads("false") → False, "123" → 123)."""
    for col in df.columns:
        if col in ('geometry', 'telepules') or df[col].dtype != object:
            continue
        van_str = van_egyeb = False
        for v in df[col]:
            if _nan(v):
                continue
            if isinstance(v, str):
                van_str = True
            else:
                van_egyeb = True
            if van_str and van_egyeb:
                break
        if van_str and van_egyeb:
            df[col] = df[col].apply(
                lambda v: v if _nan(v) or isinstance(v, str)
                else json.dumps(list(v) if isinstance(v, (set, tuple)) else v, default=str)
            )
    return df


def _parquet_merge(path, uj, csere_telepulesek, append):
    """Az új sorokat a meglévő parquet-ba fésüli: a csere_telepulesek régi sorait eldobja
    (idempotens újrafuttatás), a többit érintetlenül hagyja. append=False → sima felülírás."""
    uj = uj.reset_index(drop=True)
    if append and Path(path).exists():
        regi = gpd.read_parquet(path)
        regi = regi[~regi['telepules'].isin(set(csere_telepulesek))]
        df = pd.concat([regi, uj], ignore_index=True)  # oszlop-unió, hiányzó cella → NaN
    else:
        df = uj
    df = to_json_strings(df)
    df = _stringesit_kevert_oszlopok(df)
    gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326').to_parquet(path)


def _mentes(hatarok, lakott, nodes, edges, csere_telepulesek, append):
    """A négy adatréteg kiírása/merge-elése. Üres réteget nem ír (append-nél nincs mit
    cserélni, felülírásnál pedig nem nullázzuk ki a fájlt egy hibás kör miatt)."""
    if hatarok:
        _parquet_merge(HATAR_PARQUET,
                       gpd.GeoDataFrame(hatarok, geometry='geometry', crs='EPSG:4326'),
                       csere_telepulesek, append)
    if lakott:
        _parquet_merge(LAKOTT_PARQUET,
                       gpd.GeoDataFrame(lakott, geometry='geometry', crs='EPSG:4326'),
                       csere_telepulesek, append)
    if nodes:
        _parquet_merge(NODES_PARQUET, pd.concat(nodes, ignore_index=True),
                       csere_telepulesek, append)
    if edges:
        _parquet_merge(EDGES_PARQUET, pd.concat(edges, ignore_index=True),
                       csere_telepulesek, append)


def _hibak_mentes(hibak, csere_telepulesek, append):
    """Hibalog írása; append-nél a régi sorok megmaradnak, kivéve az újratöltött
    települések sorait (azok cserélődnek az aktuális eredményre)."""
    if append and HIBAK_TXT.exists():
        regi = [h for h in HIBAK_TXT.read_text(encoding='utf-8').splitlines()
                if h.strip() and h.split('\t')[0] not in csere_telepulesek]
        hibak = regi + hibak
    HIBAK_TXT.write_text('\n'.join(hibak) + ('\n' if hibak else ''), encoding='utf-8')


def hianyzo_varosok():
    """varosok.txt mínusz a határ-parquetben már meglévő telepules-értékek (sorrendtartó)."""
    varosok = [v.strip() for v in (DATA / 'varosok.txt').read_text(encoding='utf-8').splitlines() if v.strip()]
    if not HATAR_PARQUET.exists():
        return varosok
    megvan = set(pq.read_table(HATAR_PARQUET, columns=['telepules'])['telepules'].to_pylist())
    return [v for v in varosok if v not in megvan]


def osm_letoltes(varosok=None, append=None):
    """varosok=None → teljes varosok.txt és felülírás (régi viselkedés, 0_pipeline-kompatibilis);
    megadott lista → csak azok, alapból append-merge. A Budapest-neveket ("Budapest" és
    "... kerület") a geokódos ág kihagyja — ha szerepeltek, a végén a Budapest-ág fut le."""
    if append is None:
        append = varosok is not None
    if varosok is None:
        varosok = [v.strip() for v in (DATA / 'varosok.txt').read_text(encoding='utf-8').splitlines() if v.strip()]

    # Budapest + kerületek: külön ág (a Nominatim a kerületnevekre 0 találatot ad)
    bp_nevek = [v for v in varosok if v == 'Budapest' or 'kerület' in v]
    varosok = [v for v in varosok if v not in set(bp_nevek)]

    hatarok, lakott, nodes, edges = [], [], [], []
    hibak = []  # 'varos\tretek\túzenet' soronként

    def hiba(varos, retek, uzenet):
        sor = f'{varos}\t{retek}\t{uzenet}'
        print(f'  {retek} hiba ({varos}): {uzenet}')
        hibak.append(sor)

    for i, varos in enumerate(varosok, 1):
        print(f'[{i}/{len(varosok)}] {varos}')
        query = {'city': varos, 'country': 'Hungary'}

        try:
            place = ox.geocode_to_gdf(query)
            hatarok.append({'telepules': varos, 'geometry': place.geometry.iloc[0]})
        except Exception as ex:
            hiba(varos, 'határ', str(ex))

        try:
            res = ox.features_from_place(query, tags={'landuse': 'residential'})
            res = res[res.geometry.type.isin(['Polygon', 'MultiPolygon'])]
            if res.empty:
                hiba(varos, 'lakott', 'nincs landuse=residential')
            else:
                for g in res.geometry:
                    lakott.append({'telepules': varos, 'geometry': g})
        except Exception as ex:
            hiba(varos, 'lakott', str(ex))

        try:
            G = ox.graph_from_place(query, network_type='drive')
            n, e = ox.graph_to_gdfs(G, nodes=True, edges=True)
            n = n.reset_index()
            e = e.reset_index()
            n['telepules'] = varos
            e['telepules'] = varos
            nodes.append(n)
            edges.append(e)
        except Exception as ex:
            hiba(varos, 'utcahálózat', str(ex))

    if varosok:
        _mentes(hatarok, lakott, nodes, edges, csere_telepulesek=set(varosok), append=append)
        _hibak_mentes(hibak, csere_telepulesek=set(varosok), append=append)

    # Budapest-ág a végén, mindig append-merge-dzsel (felülíró futásnál is a friss
    # parquet-okhoz adódik hozzá — enélkül a teljes futás kitörölné a kerület-sorokat)
    if bp_nevek:
        budapest_keruletek_letoltes(append=True)


# ----------------------------------------------------------------------------------------
# Budapest-ág: egész-városi letöltés + kerülethatár-vágás
# ----------------------------------------------------------------------------------------

def budapest_keruletek_hatarok():
    """A 23 kerület közigazgatási határa admin_level=9 relációkból, EGY egész-Budapest
    lekérdezéssel. Kimenet: GeoDataFrame [kerulet (1..23), telepules (kanonikus név,
    varosok.txt-alak), geometry], kerületszám szerint rendezve. Assert-kapu: pontosan
    23 kerület, mind értelmes területtel — ez véd a bulk-letöltés előtt."""
    adm = ox.features_from_place(BUDAPEST_QUERY,
                                 tags={'boundary': 'administrative', 'admin_level': '9'})
    adm = adm[adm.geometry.type.isin(['Polygon', 'MultiPolygon'])]
    if isinstance(adm.index, pd.MultiIndex):  # osmnx 2.x: (element, id) index
        adm = adm[adm.index.get_level_values('element') == 'relation']

    sorok = []
    for _, row in adm.iterrows():
        n = None
        for kulcs in ('name', 'short_name', 'official_name', 'alt_name'):
            v = row.get(kulcs)
            if isinstance(v, str):
                # "… kerülete" birtokos alak levágása, hogy a közös parser megegye
                n = kerulet_from_telepules_nev(re.sub(r'kerülete\s*$', 'kerület', v))
                if n:
                    break
        if n:
            sorok.append({'kerulet': n, 'telepules': BP_KERULET_NEV[n], 'geometry': row.geometry})

    ker = gpd.GeoDataFrame(sorok, geometry='geometry', crs='EPSG:4326')
    talalt = sorted(ker['kerulet']) if len(ker) else []
    assert talalt == list(range(1, 24)), f'Nem pontosan a 23 kerület jött össze: {talalt}'
    km2 = ker.to_crs('EPSG:23700').area / 1e6
    gyanus = ker.loc[~km2.between(1, 80), 'telepules'].tolist()
    assert not gyanus, f'Gyanús kerület-terület(ek): {gyanus}'
    return ker.sort_values('kerulet').reset_index(drop=True)


def budapest_keruletek_letoltes(append=True):
    """Budapest külön ága — a kerületnevek nem geokódolhatók, ezért egész-városi letöltés
    egyszer, majd kerülethatár szerinti vágás:
      - határ: 'Budapest' egész-városi sor + 23 kerület-sor,
      - lakott: egész-városi residential foltok 'Budapest' címkével (az MS-szűrésnek egyben
        kell) ÉS kerülethatárra vágott darabok kerület-címkével (ezt olvassa a burkolás),
      - utcák: egész-városi drive-gráf EGY letöltéssel, kerületenként truncate-elve.
        Egész-városi gráf-sorokat nem tárolunk (nincs fogyasztója, csak duplázna).
    A gráfot gráf-szinten vágjuk (truncate_graph_polygon), nem GDF-szinten: az olvas_varos
    gráf-újraépítése megköveteli, hogy minden él végponti node-ja megvan a kerület soraiban."""
    print('Budapest-ág: kerülethatárok (admin_level=9)...')
    ker = budapest_keruletek_hatarok()
    print(f'  23 kerület OK, összterület: {(ker.to_crs("EPSG:23700").area.sum() / 1e6):.0f} km²')

    hibak = []

    def hiba(varos, retek, uzenet):
        sor = f'{varos}\t{retek}\t{uzenet}'
        print(f'  {retek} hiba ({varos}): {uzenet}')
        hibak.append(sor)

    # egész-városi közigazgatási határ ('Budapest' sor) + kerülethatárok
    bp_hatar = ox.geocode_to_gdf(BUDAPEST_QUERY).geometry.iloc[0]
    hatarok = [{'telepules': 'Budapest', 'geometry': bp_hatar}]
    hatarok += [{'telepules': k.telepules, 'geometry': k.geometry} for k in ker.itertuples()]

    print('Budapest-ág: lakott terület (landuse=residential, egész város)...')
    res = ox.features_from_place(BUDAPEST_QUERY, tags={'landuse': 'residential'})
    res = res[res.geometry.type.isin(['Polygon', 'MultiPolygon'])]
    lakott = [{'telepules': 'Budapest', 'geometry': g} for g in res.geometry]
    res_valid = res.geometry.apply(make_valid)
    for k in ker.itertuples():
        kpoly = make_valid(k.geometry)
        darab = 0
        for g in res_valid.intersection(kpoly):
            if g is None or g.is_empty:
                continue
            for p in getattr(g, 'geoms', [g]):  # GeometryCollection szétszedése
                if p.geom_type in ('Polygon', 'MultiPolygon') and not p.is_empty:
                    lakott.append({'telepules': k.telepules, 'geometry': p})
                    darab += 1
        if darab == 0:
            hiba(k.telepules, 'lakott', 'nincs residential-metszet a kerülethatárral')

    print('Budapest-ág: drive-gráf (egész város, ez sokáig tarthat)...')
    G = ox.graph_from_place(BUDAPEST_QUERY, network_type='drive')
    print(f'  egész-városi gráf: {len(G.nodes)} node, {len(G.edges)} él. Kerület-vágás...')
    nodes, edges = [], []
    for k in ker.itertuples():
        try:
            Gd = ox.truncate.truncate_graph_polygon(G.copy(), k.geometry, truncate_by_edge=True)
            if len(Gd.edges) == 0:
                raise ValueError('üres kerületi gráf')
            n, e = ox.graph_to_gdfs(Gd, nodes=True, edges=True)
            n = n.reset_index()
            e = e.reset_index()
            n['telepules'] = k.telepules
            e['telepules'] = k.telepules
            nodes.append(n)
            edges.append(e)
            print(f'  {k.telepules}: {len(n)} node, {len(e)} él')
        except Exception as ex:
            hiba(k.telepules, 'utcahálózat', str(ex))

    csere = {'Budapest'} | set(ker['telepules'])
    _mentes(hatarok, lakott, nodes, edges, csere_telepulesek=csere, append=append)
    _hibak_mentes(hibak, csere_telepulesek=csere, append=append)
    print('Budapest-ág kész.')

# ----------------------------------------------------------------------------------------
# V3-ág: 'all' hálózat + vasút/folyó (a V3 burkolás bemenete; a régi 4 parquet érintetlen)
# ----------------------------------------------------------------------------------------

def _all_edges_gdf(G, telepules):
    """Egy 'all' gráf éleit a V3-nak kellő oszlopokra szűkíti és telepules-címkével látja el.
    Hiányzó oszlop (pl. 'service' egy kis faluban) None-nal pótolva, hogy a parquet sémája
    településenként azonos legyen."""
    e = ox.graph_to_gdfs(G, nodes=False, edges=True).reset_index()
    for oszlop in ALL_EDGE_OSZLOPOK:
        if oszlop not in e.columns:
            e[oszlop] = None
    e = e[ALL_EDGE_OSZLOPOK].copy()
    e['telepules'] = telepules
    return e


def _vasut_folyo_sorok(feat, telepules, vago_poligon=None):
    """A features_from_place eredményéből a vasút/folyó VONALAKAT szedi ki soronként:
    [{'tipus': 'vasút'|'folyó', 'geometry', 'telepules'}]. Poligon-geometriát (pl. folyó-
    terület) eldob. vago_poligon megadásával (Budapest-ág) a vonalakat a kerülethatárra vágja
    és a MultiLineString-eket darabolja."""
    sorok = []
    if feat is None or feat.empty:
        return sorok
    feat = feat[feat.geometry.type.isin(['LineString', 'MultiLineString'])]
    for _, sor in feat.iterrows():
        # típus: a railway címke ad vasutat, a waterway folyót
        tipus = 'vasút' if isinstance(sor.get('railway'), str) and sor.get('railway') == 'rail' else 'folyó'
        geom = sor.geometry
        if vago_poligon is not None:
            geom = geom.intersection(vago_poligon)
        if geom is None or geom.is_empty:
            continue
        # MultiLineString → darabok; GeometryCollection → csak a vonalak
        for darab in getattr(geom, 'geoms', [geom]):
            if darab.geom_type == 'LineString' and not darab.is_empty:
                sorok.append({'tipus': tipus, 'geometry': darab, 'telepules': telepules})
    return sorok


def _mentes_v3(edges_all, vasut_folyo, csere_telepulesek, append):
    """A két V3-réteg kiírása/merge-elése (ugyanazzal a _parquet_merge logikával)."""
    if edges_all:
        _parquet_merge(EDGES_ALL_PARQUET, pd.concat(edges_all, ignore_index=True),
                       csere_telepulesek, append)
    if vasut_folyo:
        _parquet_merge(VASUT_FOLYO_PARQUET,
                       gpd.GeoDataFrame(vasut_folyo, geometry='geometry', crs='EPSG:4326'),
                       csere_telepulesek, append)


def _ujraprobalva(hivas, probak=4, varakozas=30):
    """Az Overpass nyilvános szervere túlterhelésnél 502-t ad: ilyenkor exponenciális várakozással
    újrapróbáljuk (30, 60, 120 s). Más hibát (pl. Nominatim nem talál) azonnal továbbadunk."""
    import time
    for i in range(probak):
        try:
            return hivas()
        except Exception as ex:
            tulterhelt = '502' in str(ex) or '504' in str(ex) or '429' in str(ex) or 'timed out' in str(ex).lower()
            if not tulterhelt or i == probak - 1:
                raise
            print(f'    Overpass túlterhelt, újrapróbálás {varakozas} s múlva ({i + 1}/{probak - 1})')
            time.sleep(varakozas)
            varakozas *= 2


def osm_letoltes_v3_adagokban(varosok, adag=50):
    """Országos futáshoz: a településeket `adag` méretű csoportokban tölti és MENTI (append-merge),
    így egy megszakadás legfeljebb egy adagnyi munkát visz el. A Budapest-neveket kihagyja, ha a
    23 kerület már a cache-ben van."""
    megvan = set()
    if EDGES_ALL_PARQUET.exists():
        megvan = set(pq.read_table(EDGES_ALL_PARQUET, columns=['telepules'])['telepules'].to_pylist())
    if all(nev in megvan for nev in BP_KERULET_NEV.values()):
        varosok = [v for v in varosok if not (v == 'Budapest' or 'kerület' in v)]
    for i in range(0, len(varosok), adag):
        resz = varosok[i:i + adag]
        print(f'=== adag {i // adag + 1}/{(len(varosok) + adag - 1) // adag}: {resz[0]} … {resz[-1]} ===', flush=True)
        osm_letoltes_v3(resz, append=True)


def hianyzo_varosok_v3():
    """varosok.txt mínusz az utcak_all_edges.parquet-ben már meglévők, mínusz azok, amelyeknél
    a hibafájl 'utcahálózat_all' hibát rögzített (hálózat nélküli aprófalvak – ezeket nem
    próbáljuk újra minden futásnál). Sorrendtartó."""
    varosok = [v.strip() for v in (DATA / 'varosok.txt').read_text(encoding='utf-8').splitlines() if v.strip()]
    megvan = set()
    if EDGES_ALL_PARQUET.exists():
        megvan = set(pq.read_table(EDGES_ALL_PARQUET, columns=['telepules'])['telepules'].to_pylist())
    hibas = set()
    if HIBAK_TXT.exists():
        for sor in HIBAK_TXT.read_text(encoding='utf-8').splitlines():
            mezok = sor.split('\t')
            if len(mezok) >= 2 and mezok[1] == HIBA_RETEG_ALL:
                hibas.add(mezok[0])
    return [v for v in varosok if v not in megvan and v not in hibas]


def osm_letoltes_v3(varosok=None, append=None):
    """V3 bemenetek letöltése: 'all' utcahálózat élei + vasút/folyó vonalak, településenként.
    varosok=None → teljes varosok.txt és felülírás; megadott lista → csak azok, append-merge.
    A Budapest-neveket a geokódos ág kihagyja, a végén a Budapest-ág fut le rájuk.
    Ha egy településen nincs vasút/folyó, az NEM hiba (üres réteg normális)."""
    if append is None:
        append = varosok is not None
    if varosok is None:
        varosok = [v.strip() for v in (DATA / 'varosok.txt').read_text(encoding='utf-8').splitlines() if v.strip()]

    bp_nevek = [v for v in varosok if v == 'Budapest' or 'kerület' in v]
    varosok = [v for v in varosok if v not in set(bp_nevek)]

    edges_all, vasut_folyo = [], []
    hibak = []

    def hiba(varos, retek, uzenet):
        sor = f'{varos}\t{retek}\t{uzenet}'
        print(f'  {retek} hiba ({varos}): {uzenet}')
        hibak.append(sor)

    for i, varos in enumerate(varosok, 1):
        print(f'[V3 {i}/{len(varosok)}] {varos}')
        query = {'city': varos, 'country': 'Hungary'}

        # 'all' hálózat: minden gépjárművel és gyalogosan járható út (leírás: Bemenetek)
        try:
            G = _ujraprobalva(lambda: ox.graph_from_place(query, network_type='all'))
            edges_all.append(_all_edges_gdf(G, varos))
        except Exception as ex:
            hiba(varos, HIBA_RETEG_ALL, str(ex))

        # vasút + folyó (leírás: rang 6 vonalak)
        try:
            feat = _ujraprobalva(lambda: ox.features_from_place(query, tags=VASUT_FOLYO_TAGS))
            vasut_folyo.extend(_vasut_folyo_sorok(feat, varos))
        except ox._errors.InsufficientResponseError:
            pass  # nincs vasút/folyó a településen – normális
        except Exception as ex:
            hiba(varos, HIBA_RETEG_VASUT, str(ex))

    if varosok:
        _mentes_v3(edges_all, vasut_folyo, csere_telepulesek=set(varosok), append=append)
        _hibak_mentes(hibak, csere_telepulesek=set(varosok), append=append)

    if bp_nevek:
        budapest_keruletek_letoltes_v3(append=True)


def budapest_vasut_folyo(ker, hiba):
    """Vasút + folyó egész Budapestre egy lekérdezéssel, kerülethatárra vágva. Külön függvény,
    hogy Overpass-hiba (502) után a gráf újratöltése nélkül megismételhető legyen."""
    print('Budapest V3-ág: vasút + folyó (egész város)...')
    vasut_folyo = []
    try:
        feat = ox.features_from_place(BUDAPEST_QUERY, tags=VASUT_FOLYO_TAGS)
        feat = feat[feat.geometry.type.isin(['LineString', 'MultiLineString'])]
        for k in ker.itertuples():
            kpoly = make_valid(k.geometry)
            vasut_folyo.extend(_vasut_folyo_sorok(feat, k.telepules, vago_poligon=kpoly))
    except ox._errors.InsufficientResponseError:
        pass
    except Exception as ex:
        hiba('Budapest', HIBA_RETEG_VASUT, str(ex))
    return vasut_folyo


def budapest_vasut_folyo_potlas():
    """Csak a budapesti vasút/folyó réteg (újra)letöltése és merge-elése a parquet-be."""
    ker = budapest_keruletek_hatarok()
    hibak = []

    def hiba(varos, retek, uzenet):
        print(f'  {retek} hiba ({varos}): {uzenet}')
        hibak.append(f'{varos}\t{retek}\t{uzenet}')

    vasut_folyo = budapest_vasut_folyo(ker, hiba)
    if vasut_folyo:
        _parquet_merge(VASUT_FOLYO_PARQUET,
                       gpd.GeoDataFrame(vasut_folyo, geometry='geometry', crs='EPSG:4326'),
                       csere_telepulesek=set(ker['telepules']), append=True)
    print(f'Budapest vasút/folyó: {len(vasut_folyo)} vonal')
    return hibak


def budapest_keruletek_letoltes_v3(append=True):
    """Budapest V3-ága: egész-városi 'all' gráf EGY letöltéssel, kerületenként truncate-elve;
    vasút/folyó egész városra lekérdezve, kerülethatárra vágva. Kerület-címkés sorok kerülnek a
    parquet-ekbe (egész-városi 'Budapest' sor nincs: a burkolás kerületenként dolgozik)."""
    print('Budapest V3-ág: kerülethatárok (admin_level=9)...')
    ker = budapest_keruletek_hatarok()

    hibak = []

    def hiba(varos, retek, uzenet):
        sor = f'{varos}\t{retek}\t{uzenet}'
        print(f'  {retek} hiba ({varos}): {uzenet}')
        hibak.append(sor)

    print("Budapest V3-ág: 'all' gráf (egész város, ez sokáig tarthat)...")
    G = ox.graph_from_place(BUDAPEST_QUERY, network_type='all')
    print(f'  egész-városi all-gráf: {len(G.nodes)} node, {len(G.edges)} él. Kerület-vágás...')
    edges_all = []
    for k in ker.itertuples():
        try:
            Gd = ox.truncate.truncate_graph_polygon(G.copy(), k.geometry, truncate_by_edge=True)
            if len(Gd.edges) == 0:
                raise ValueError('üres kerületi gráf')
            e = _all_edges_gdf(Gd, k.telepules)
            edges_all.append(e)
            print(f'  {k.telepules}: {len(e)} él')
        except Exception as ex:
            hiba(k.telepules, HIBA_RETEG_ALL, str(ex))

    vasut_folyo = budapest_vasut_folyo(ker, hiba)

    csere = set(ker['telepules'])
    _mentes_v3(edges_all, vasut_folyo, csere_telepulesek=csere, append=append)
    _hibak_mentes(hibak, csere_telepulesek=csere, append=append)
    print('Budapest V3-ág kész.')


if __name__ == '__main__':
    # Inkrementális pótlás: csak a hiányzó települések (+ Budapest-ág, ha érintett).
    # Teljes újratöltéshez: osm_letoltes()
    osm_letoltes(hianyzo_varosok(), append=True)
    # V3 bemenetek ('all' hálózat + vasút/folyó) inkrementális pótlása
    osm_letoltes_v3_adagokban(hianyzo_varosok_v3())
