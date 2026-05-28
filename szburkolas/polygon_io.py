'''
Beolvasó-rekonstruáló az OSM cache-ből. A `adatstruktua/osm_letoltes.py` által
WGS84-ben elmentett parquet-ekből szűr egy városra, UTM-re vetít, és visszaadja
a régi `letoltes()` / `letoltes_csak_res()` szemantikájával ekvivalens tuple-t —
így a downstream pipeline (`poly_gen_pipeline`, `generalas_pipeline`) változatlan
maradhat.

Tárolás: szándékosan WGS84 a parquet-ekben (egységes minden városra, egy fájl);
a város-specifikus UTM-zóna választása itt, futáskor történik az `ox.projection`
auto-zóna-választójával.

A `osm_letoltes._stringify_mixed_object_cols` által JSON-szerializált
mixed-típusú object oszlopokat (osmid, highway, name, …) itt fejtjük vissza
list/dict formára, mielőtt `ox.graph_from_gdfs`-nek átadnánk őket.
'''

import json
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd

from polygon_fuggvenyek import NincsResidentialError, _safe_make_valid


# Path-ok __file__-ből, hogy a `szburkolas/` és `adatstruktua/` kívüli CWD-ből
# is dolgozzon. Repo gyökér: szburkolas/-ből egy szint feljebb.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data' / 'nyers_data'
HATAROK = DATA_DIR / 'varos_kozigazgatasi_hatar.parquet'
LAKOTT = DATA_DIR / 'varos_lakottterulet_hatar.parquet'
NODES = DATA_DIR / 'utcak_nodes.parquet'
EDGES = DATA_DIR / 'utcak_edges.parquet'


def _restore_mixed_object_cols(df: pd.DataFrame) -> pd.DataFrame:
    '''Visszafejti a JSON-string oszlopokat (osm_letoltes.to_json_strings inverzia).
    Minden stringet megpróbál `json.loads`-szal felbontani; ha nem érvényes JSON,
    érintetlenül hagyja (pl. utcanevek mint "Petőfi utca").'''
    for col in df.columns:
        if col in ('geometry', 'telepules'):
            continue
        if df[col].dtype != object:
            continue

        def _dec(v):
            if not isinstance(v, str) or not v:
                return v
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return v

        df[col] = df[col].apply(_dec)
    return df


def olvas_varos(VAROS: str, csak_lakott: bool = False, data_dir: Path | None = None):
    '''Egy város parquet-cache-ének beolvasása + UTM-re vetítés.

    Visszatérési érték a régi `polygon_fuggvenyek.letoltes` / `letoltes_csak_res`
    szemantikájával egyenértékű:
        csak_lakott=False → (Gp, nodes, edges, res_p, city_boundary)
        csak_lakott=True  → (res_p, city_boundary)

    Args:
        VAROS: pontos településnév (a `telepules` oszlop értéke).
        csak_lakott: True esetén csak a residential + városhatárt adja vissza,
            úthálózat-rekonstrukció nélkül — az egy-szavazóköri shortcut ág
            számára (`generalas_pipeline`).
        data_dir: az `osm_letoltes` parquet könyvtár (alapból a repo
            data/nyers_data/-ja, ami a `__file__`-ből származik).

    Raises:
        NincsResidentialError: ha a városhoz nincs `landuse=residential` a
            parquetben (a CLAUDE.md kötelező invariánsa szerint: SOHA nem
            esünk vissza puszta településhatárra).
        FileNotFoundError: ha a szükséges parquet hiányzik.
        RuntimeError: ha a városhatár nem található, vagy ha csak_lakott=False
            esetén nincs úthálózat a városhoz (Ibafa-jellegű aprófalu — ekkor
            a hívó térjen át a shortcut ágra vagy hagyja ki a várost).
    '''
    dd = Path(data_dir) if data_dir else DATA_DIR
    hatarok_path = dd / HATAROK.name
    lakott_path = dd / LAKOTT.name
    nodes_path = dd / NODES.name
    edges_path = dd / EDGES.name

    # ── 1. Lakott terület (kötelező) ────────────────────────────────────────
    if not lakott_path.exists():
        raise FileNotFoundError(f'Hiányzik: {lakott_path}. Futtasd: python adatstruktua/osm_letoltes.py')
    res = gpd.read_parquet(lakott_path)
    res = res[res['telepules'] == VAROS].copy()
    if res.empty:
        raise NincsResidentialError(
            f"Nincs landuse=residential a parquet cache-ben: {VAROS!r}. "
            f"Ellenőrizd a data/nyers_data/osm_letoltes_hibak.txt-t."
        )

    # ── 2. Városhatár (kötelező) ────────────────────────────────────────────
    if not hatarok_path.exists():
        raise FileNotFoundError(f'Hiányzik: {hatarok_path}.')
    hatarok = gpd.read_parquet(hatarok_path)
    h = hatarok[hatarok['telepules'] == VAROS]
    if h.empty:
        raise RuntimeError(f"Nincs városhatár a parquet cache-ben: {VAROS!r}")
    city_geom_wgs = h.geometry.iloc[0]

    # ── 3. Utcahálózat (csak ha kell) ───────────────────────────────────────
    nodes = edges = None
    if not csak_lakott:
        if not (nodes_path.exists() and edges_path.exists()):
            raise FileNotFoundError(f'Hiányzik az utcahálózat parquet: {nodes_path} / {edges_path}')
        ndf = gpd.read_parquet(nodes_path)
        edf = gpd.read_parquet(edges_path)
        nodes = ndf[ndf['telepules'] == VAROS].copy()
        edges = edf[edf['telepules'] == VAROS].copy()
        if nodes.empty or edges.empty:
            # Aprófalu-eset (lásd CLAUDE.md): a letöltő `Graph contains no edges`-szel
            # elhasalt. Egy-szavazóköri shortcut ágon a hívó `csak_lakott=True`-val
            # próbálkozhat; teljes pipeline-on viszont a város kihagyandó.
            raise RuntimeError(
                f"Nincs úthálózat a parquet cache-ben: {VAROS!r}. "
                f"Próbáld a csak_lakott=True ágat, vagy ellenőrizd az osm_letoltes_hibak.txt-t."
            )

    # ── 4. UTM-zóna választás ──────────────────────────────────────────────
    # csak_lakott=False ágon a NODES-bounds-szal projektálunk (ekvivalens az
    # eredeti `ox.project_graph(G).graph['crs']`-szel). csak_lakott=True ágon a
    # residential bounds-szal (ekvivalens az eredeti `letoltes_csak_res`-szel).
    if csak_lakott:
        res_p = ox.projection.project_gdf(res, to_crs=None)
        target_crs = res_p.crs
    else:
        nodes = ox.projection.project_gdf(nodes, to_crs=None)
        target_crs = nodes.crs
        res_p = res.to_crs(target_crs)

    # ── 5. Városhatár vetítés + make_valid ─────────────────────────────────
    city_boundary = (
        gpd.GeoSeries([city_geom_wgs], crs='EPSG:4326')
        .to_crs(target_crs)
        .iloc[0]
    )
    city_boundary = _safe_make_valid(city_boundary)
    if city_boundary is None or city_boundary.is_empty:
        raise RuntimeError(f"Üres városhatár vetítés után: {VAROS!r}")

    if csak_lakott:
        return res_p, city_boundary

    # ── 6. Edges UTM-re ────────────────────────────────────────────────────
    edges = edges.to_crs(target_crs)

    # ── 7. x/y oszlopok újraszámítása a projektált geometriából ────────────
    # KRITIKUS: a parquet-ben az `x`/`y` a WGS84 koordináták voltak — to_crs
    # után a geometry már UTM-en van, de a sima float oszlopokat nem érinti.
    # Az osmnx `graph_from_gdfs` ezek alapján számol — ha nem frissítjük, a
    # gráfban a node-koordináták WGS-en lennének, az élek UTM-en → káosz.
    nodes['x'] = nodes.geometry.x
    nodes['y'] = nodes.geometry.y

    # ── 8. Mixed object oszlopok visszafejtése (JSON → list/dict) ──────────
    nodes = _restore_mixed_object_cols(nodes)
    edges = _restore_mixed_object_cols(edges)

    # ── 9. Indexek visszaállítása az osmnx-natív formára ───────────────────
    # `telepules` oszlopra már nincs szükség (átkerült a kontextusba).
    nodes = nodes.drop(columns=['telepules']).set_index('osmid')
    edges = edges.drop(columns=['telepules']).set_index(['u', 'v', 'key'])

    # ── 10. Graph rekonstrukció ────────────────────────────────────────────
    Gp = ox.graph_from_gdfs(nodes, edges)

    return Gp, nodes, edges, res_p, city_boundary
