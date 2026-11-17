"""
v3_io – a V3 burkolás bemeneteinek betöltése, a debug GPKG írója és a kimenetek írása.

Leírás: „Bemenetek” fejezet, 1.1 (utca_id), 11. (kimenetek), 11.1 (debug GPKG).

Bemenetek településenként, az A-fázis parquet-cache-éből (data/nyers_data/):
  - varos_lakottterulet_hatar.parquet   lakott foltok (landuse=residential)
  - varos_kozigazgatasi_hatar.parquet   közigazgatási határ
  - utcak_all_edges.parquet             'all' utcahálózat élei (osm_letoltes_v3)
  - vasut_folyo.parquet                 vasút és folyó vonalak (osm_letoltes_v3)
és a címpontok az összekapcsolt gpkg-ből (osszekapcsolt_pontok_es_cimek_<év>.gpkg).

Minden geometria a településre választott helyi UTM-zónába kerül (méteres vetület).
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import LineString

import v3_param as P

# ──────────────────────────────────────────────────────────────────────────────
# Elérési utak (a repo gyökeréhez képest, hogy bármely munkakönyvtárból fusson)
# ──────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data' / 'nyers_data'
LAKOTT_PARQUET = DATA_DIR / 'varos_lakottterulet_hatar.parquet'
KOZIG_PARQUET = DATA_DIR / 'varos_kozigazgatasi_hatar.parquet'
UTCAK_ALL_PARQUET = DATA_DIR / 'utcak_all_edges.parquet'
VASUT_FOLYO_PARQUET = DATA_DIR / 'vasut_folyo.parquet'
VAROSOK_TXT = DATA_DIR / 'varosok.txt'
ADATOK_DIR = ROOT / 'szburkolas' / 'adatok'      # egy település kézi futtatásának kimenetei

# Az utcanév-ujjlenyomat az A-fázis függvénye (1.1: ugyanazzal a normalizálással, mint a címek)
sys.path.insert(0, str(ROOT / 'adatstruktua'))
from szkid_cim_kapcsolas import utca_fp_series  # noqa: E402


class NincsLakottTerulet(RuntimeError):
    """A településnek nincs lakott foltja (vagy a közigazgatási határra vágva nem marad belőle):
    a település kimarad – a határral SOHA nem helyettesítjük (0.1)."""


# ──────────────────────────────────────────────────────────────────────────────
# Segédfüggvények az OSM-attribútumokhoz
# ──────────────────────────────────────────────────────────────────────────────

def _json_visszafejtes(df):
    """A letöltő JSON-stringgé alakított lista-oszlopait (osmid, highway, name) visszafejti.
    Ami nem érvényes JSON (pl. a 'Petőfi utca' név), érintetlen marad."""
    for oszlop in df.columns:
        if oszlop in ('geometry', 'telepules') or df[oszlop].dtype != object:
            continue

        def egy_ertek(v):
            if not isinstance(v, str) or not v:
                return v
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return v

        df[oszlop] = df[oszlop].apply(egy_ertek)
    return df


def _elso_elem(v):
    """Lista-értékű OSM-attribútum (pl. több név egy vonalon) → az első elem (terv #4)."""
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    if isinstance(v, float) and v != v:   # NaN
        return None
    return v


def highway_rang(highway):
    """OSM highway címke → rang (0.4 táblázat). Lista esetén a LEGMAGASABB rang nyer (terv #4);
    'road' → rang 4; a táblán kívüli ismeretlen címke → rang 3 (terv #5)."""
    ertekek = highway if isinstance(highway, (list, tuple)) else [highway]
    rangok = []
    for ertek in ertekek:
        if ertek == 'road':
            rangok.append(P.HIGHWAY_RANG_ROAD)
        else:
            rangok.append(P.HIGHWAY_RANG.get(ertek, P.RANG_ISMERETLEN_HIGHWAY))
    return max(rangok)


def utca_id_kepzes(nevek, osmidk):
    """1.1 utca_id: az OSM `name` ujjlenyomata az A-fázis függvényével (rövidítés-feloldás,
    ékezettelenítés, kisbetű, írásjelek törlése). Név nélkül az osmid az azonosító."""
    nevek = pd.Series([_elso_elem(n) for n in nevek], dtype=object)
    osmidk = pd.Series([_elso_elem(o) for o in osmidk], dtype=object)
    ujjlenyomat = utca_fp_series(nevek)
    nincs_nev = (ujjlenyomat == '') | nevek.isna()
    ujjlenyomat = ujjlenyomat.where(~nincs_nev, 'osm_' + osmidk.astype(str))
    return ujjlenyomat.tolist()


# ──────────────────────────────────────────────────────────────────────────────
# Betöltés
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Bemenet:
    """Egy település összes bemenete, helyi UTM-vetületben."""
    varos: str
    crs: object
    cimek: gpd.GeoDataFrame            # szkid, utca, hazszam, irsz, jelzes, geometry (Point)
    lakott_nyers: gpd.GeoSeries        # OSM lakott foltok a határra vágás ELŐTT
    kozig_hatar: object                # közigazgatási határ (Polygon/MultiPolygon)
    vonalak_nyers: gpd.GeoDataFrame    # tipus, rang, utca_id, highway, name, geometry (LineString)
    n_cim_hataron_kivul: int = 0       # a közigazgatási határon kívül eső (hibás koordinátájú), eldobott címek


def _parquet_telepules(utvonal, varos):
    """Egy település sorai a parquet-ből, pyarrow-szűréssel (nem olvassuk be az egész országot)."""
    if not utvonal.exists():
        raise FileNotFoundError(f'Hiányzik: {utvonal}. Futtasd: python adatstruktua/osm_letoltes.py')
    return gpd.read_parquet(utvonal, filters=[('telepules', '==', varos)])


def cimek_elokeszites(cimek_wgs, varos, crs):
    """A címpontok a településre szűrve, UTM-re vetítve, a leírás oszlopneveivel.
    Bemeneti eltérés (terv #2): az A-fázis oszlopa `szavazokorid`, a leírás `szkid`-et ír."""
    cimek = cimek_wgs
    if 'telepules' in cimek.columns and cimek['telepules'].nunique() > 1:
        cimek = cimek[cimek['telepules'] == varos]
    cimek = cimek.rename(columns={'szavazokorid': 'szkid'})
    oszlopok = [o for o in ['szkid', 'utca', 'hazszam', 'irsz'] if o in cimek.columns]
    cimek = cimek[oszlopok + ['geometry']].copy()
    for hianyzo in ['utca', 'hazszam', 'irsz']:
        if hianyzo not in cimek.columns:
            cimek[hianyzo] = None
    cimek['jelzes'] = ''
    cimek = cimek.to_crs(crs).reset_index(drop=True)
    cimek['szkid'] = cimek['szkid'].astype('int64')
    return cimek


def betolt_telepules(varos, cimek_wgs):
    """A település minden bemenetének betöltése és UTM-re vetítése (leírás: Bemenetek).

    A vetület a lakott foltokból választott UTM-zóna (estimate_utm_crs). Az utcákból a
    V3-nak kellő attribútumok: tipus 'utca', rang (highway → 0.4 tábla), utca_id (1.1).
    A kétirányú gráf-élek (u,v,key)/(v,u,key) párját egyszer tartjuk meg.
    Vasút/folyó: tipus a parquet 'tipus' oszlopából, rang 6.
    Ha a településnek nincs utcahálózata, a vonaltábla üres (0.4: minden folt utca nélküli).
    """
    # Lakott foltok (kötelező)
    lakott = _parquet_telepules(LAKOTT_PARQUET, varos)
    if lakott.empty:
        raise NincsLakottTerulet(f'Nincs landuse=residential a cache-ben: {varos!r}')
    crs = lakott.estimate_utm_crs()
    lakott = lakott.to_crs(crs)
    lakott_nyers = gpd.GeoSeries(
        [g for geom in lakott.geometry for g in getattr(geom, 'geoms', [geom])
         if g.geom_type == 'Polygon' and not g.is_empty],
        crs=crs)

    # Közigazgatási határ (kötelező)
    kozig = _parquet_telepules(KOZIG_PARQUET, varos)
    if kozig.empty:
        raise RuntimeError(f'Nincs közigazgatási határ a cache-ben: {varos!r}')
    kozig_hatar = shapely.make_valid(kozig.to_crs(crs).geometry.iloc[0])

    # Utcák ('all' hálózat)
    utcak = _parquet_telepules(UTCAK_ALL_PARQUET, varos)
    utcak = _json_visszafejtes(utcak)
    vonal_sorok = []
    if not utcak.empty:
        utcak = utcak.to_crs(crs)
        # kétirányú élek dedupe-ja: rendezetlen (u, v, key)
        kulcs = utcak.apply(lambda s: (min(s['u'], s['v']), max(s['u'], s['v']), s['key']), axis=1)
        utcak = utcak[~kulcs.duplicated()].copy()
        utcak['utca_id'] = utca_id_kepzes(utcak['name'].tolist(), utcak['osmid'].tolist())
        for sor in utcak.itertuples():
            vonal_sorok.append({
                'tipus': P.TIPUS_UTCA,
                'rang': highway_rang(sor.highway),
                'utca_id': sor.utca_id,
                'highway': str(_elso_elem(sor.highway)),
                'name': _elso_elem(sor.name),
                'geometry': sor.geometry,
            })

    # Vasút, folyó (rang 6) – nem kötelező, sok településen nincs
    if VASUT_FOLYO_PARQUET.exists():
        vasut_folyo = _parquet_telepules(VASUT_FOLYO_PARQUET, varos)
        if not vasut_folyo.empty:
            vasut_folyo = vasut_folyo.to_crs(crs)
            for sor in vasut_folyo.itertuples():
                vonal_sorok.append({
                    'tipus': P.TIPUS_VASUT if sor.tipus == 'vasút' else P.TIPUS_FOLYO,
                    'rang': P.RANG_VASUT_FOLYO,
                    'utca_id': None,
                    'highway': None,
                    'name': None,
                    'geometry': sor.geometry,
                })

    vonalak_nyers = gpd.GeoDataFrame(
        vonal_sorok, columns=['tipus', 'rang', 'utca_id', 'highway', 'name', 'geometry'],
        geometry='geometry', crs=crs)
    # MultiLineString → darabok, hogy minden sor egy LineString legyen
    vonalak_nyers = vonalak_nyers.explode(index_parts=False).reset_index(drop=True)
    vonalak_nyers = vonalak_nyers[vonalak_nyers.geometry.geom_type == 'LineString'].reset_index(drop=True)

    cimek = cimek_elokeszites(cimek_wgs, varos, crs)

    # Hibás geokódolás: a címpont koordinátája másik településre esik. Az ilyen cím a saját
    # településének közigazgatási határán kívül van → eldobjuk (a határ a hivatalos szél, 0.2).
    # A számát a napló rögzíti.
    hataron_belul = cimek.geometry.intersects(kozig_hatar)
    n_cim_hataron_kivul = int((~hataron_belul).sum())
    if n_cim_hataron_kivul:
        print(f'  [bemenet] {n_cim_hataron_kivul} címpont a közigazgatási határon kívül (hibás koordináta) – eldobva')
    cimek = cimek[hataron_belul].reset_index(drop=True)

    return Bemenet(varos=varos, crs=crs, cimek=cimek, lakott_nyers=lakott_nyers,
                   kozig_hatar=kozig_hatar, vonalak_nyers=vonalak_nyers,
                   n_cim_hataron_kivul=n_cim_hataron_kivul)


def olvas_varosok_txt():
    """A feldolgozandó települések listája (varosok.txt, soronként egy név, sorrendtartó)."""
    return [v.strip() for v in VAROSOK_TXT.read_text(encoding='utf-8').splitlines() if v.strip()]


# ──────────────────────────────────────────────────────────────────────────────
# 11.1 Debug GPKG – minden lépés, minden vonal, minden pont
# ──────────────────────────────────────────────────────────────────────────────

def nyil(geom_tol, geom_ig):
    """Debug-nyíl: szakasz az egyik geometria reprezentatív pontjától a másikéig (3.3, 9.)."""
    return LineString([geom_tol.representative_point(), geom_ig.representative_point()])


class DebugIro:
    """A `<település>_debug.gpkg` írója. Minden réteg közös oszlopai: `lepes`, `tipus`, `szin`
    (hex), plusz a réteg saját attribútumai. A rétegneveket a leírás 11.1 táblázata adja.
    Induláskor a korábbi fájlt törli, hogy régi futás rétegei ne maradjanak benne.
    `utvonal=None` → inaktív író: minden hívás azonnal visszatér (kötegelt futás)."""

    def __init__(self, utvonal, crs):
        self.utvonal = Path(utvonal) if utvonal else None
        self.crs = crs
        self.paletta = {}
        if self.utvonal is not None:
            self.utvonal.parent.mkdir(parents=True, exist_ok=True)
            if self.utvonal.exists():
                self.utvonal.unlink()

    @property
    def aktiv(self):
        return self.utvonal is not None

    def kategoria_szin(self, szkid):
        """A kategória színe a palettából; besorolatlan (None) → üres-cella szürke."""
        if szkid is None or (isinstance(szkid, float) and szkid != szkid):
            return P.SZIN['cella üres']
        return self.paletta.get(int(szkid), P.SZIN['segéd'])

    def reteg(self, nev, lepes, tipus, geometriak, szin, **attributumok):
        """Egy réteg kiírása. `geometriak`: lista/GeoSeries; `szin`: egy hex kód vagy soronkénti
        lista; `attributumok`: a réteg saját oszlopai, skalár vagy soronkénti lista."""
        if not self.aktiv:
            return
        geometriak = list(geometriak)
        if len(geometriak) == 0:
            print(f'  [debug] {nev}: üres réteg, nem íródik')
            return
        n = len(geometriak)
        adatok = {
            'lepes': [lepes] * n,
            'tipus': [tipus] * n,
            'szin': list(szin) if isinstance(szin, (list, tuple, pd.Series)) else [szin] * n,
        }
        for oszlop, ertek in attributumok.items():
            if isinstance(ertek, (list, tuple, pd.Series)):
                adatok[oszlop] = [None if (isinstance(v, float) and v != v) else v for v in ertek]
            else:
                adatok[oszlop] = [ertek] * n
        gdf = gpd.GeoDataFrame(adatok, geometry=geometriak, crs=self.crs)
        gdf.to_file(self.utvonal, layer=nev, driver='GPKG')

    def cellak(self, nev, lepes, cellak, allapot=None, **attributumok):
        """Cella-réteg a 11.1 színszabállyal: sliver rózsaszín, sarok (besorolatlan) halványmagenta,
        vegyes narancs, egykategóriás a kategória színe, üres szürke."""
        if not self.aktiv:
            return
        szinek = []
        for i, sor in enumerate(cellak.itertuples()):
            if getattr(sor, 'sliver', False):
                szinek.append(P.SZIN['cella sliver'])
            elif allapot is not None and allapot[i] == P.ALLAPOT_VEGYES:
                szinek.append(P.SZIN['cella vegyes'])
            elif sor.szkid is not None and not pd.isna(sor.szkid):
                szinek.append(self.kategoria_szin(sor.szkid))
            elif sor.cellatipus == P.CELLA_SAROK:
                szinek.append(P.SZIN['cella sarok'])
            else:
                szinek.append(P.SZIN['cella üres'])
        self.reteg(nev, lepes, 'cella', cellak.geometry, szinek,
                   cella_id=cellak['cella_id'].tolist(),
                   cellatipus=cellak['cellatipus'].tolist(),
                   tomb_id=cellak['tomb_id'].tolist(),
                   utca_id=cellak['utca_id'].tolist(),
                   szkid=cellak['szkid'].tolist(),
                   **attributumok)

    def eltabla(self, nev, lepes, eltabla):
        """Éltábla-réteg: szín a rang/típus szerint, saját attribútumok a 11.1 szerint."""
        if not self.aktiv:
            return
        szinek = [P.rang_szin(r, t) for r, t in zip(eltabla['rang'], eltabla['tipus'])]
        self.reteg(nev, lepes, 'éltábla', eltabla.geometry, szinek,
                   el_id=eltabla['el_id'].tolist(), rang=eltabla['rang'].tolist(),
                   tipus_el=eltabla['tipus'].tolist(), utca_id=eltabla['utca_id'].tolist(),
                   cella_bal=eltabla['cella_bal'].tolist(), cella_jobb=eltabla['cella_jobb'].tolist(),
                   hossz=eltabla['hossz'].tolist())


# ──────────────────────────────────────────────────────────────────────────────
# 11. Kimenetek
# ──────────────────────────────────────────────────────────────────────────────

# A `szin` oszlop a kategória (szkid) színe ugyanabból a palettából a körzeten és a címein: QGIS-ben
# adatvezérelt kitöltéssel (szín ← szin) a körzetek jól elkülönülnek, és látszik, melyik cím melyik
# körzetbe tartozik.
POLIGON_OSZLOPOK = ['szkid', 'telepules', 'jelzes', 'szin', 'geometry']
CIM_OSZLOPOK = ['szkid', 'utca', 'hazszam', 'telepules', 'irsz', 'szin', 'geometry']
TARGET_CRS = 'EPSG:23700'   # EOV: a gyűjtő GPKG egységes vetülete


def ir_eredmeny(utvonal, korzetek, cimek, varos):
    """Egy település eredménye külön GPKG-be (kézi futtatás): `poligonok` és `cimek` réteg,
    a gyűjtő sémájával és vetületével (EOV)."""
    utvonal = Path(utvonal)
    utvonal.parent.mkdir(parents=True, exist_ok=True)
    if utvonal.exists():
        utvonal.unlink()
    k = korzetek.to_crs(TARGET_CRS).copy()
    k['telepules'] = varos
    k[POLIGON_OSZLOPOK].to_file(utvonal, layer='poligonok', driver='GPKG')
    c = cimek.to_crs(TARGET_CRS).copy()
    c['telepules'] = varos
    c[CIM_OSZLOPOK].to_file(utvonal, layer='cimek', driver='GPKG')
