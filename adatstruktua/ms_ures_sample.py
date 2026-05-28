'''
A Mircroft Building adatbázisból szedem az épületeket amikhez lekérem a pontos címet
így innen tudom hol van épület amihez le kell kérdeznem a címet,
viszont az dataset néhány helyen hibás (felhők stb.) ezért ezekben a régiókban
nem tud épületet felismerni így itt nincsenek lekérdezendő kordináták.

A függvény ezt korigálja méghozzá két lépésben:
    1. detektálom azokat a városokat ahol nincsen MS (Microsoft) adat,
    ez mivel nincsen dokumenálva csak másidk datasettel összevetve tudom megtenni.

    2. azokat a városokat ahol nincsen MS adat azokba veszek fel véleteln kordinátákat,
    vagyis mintavételemezem és ezeket a leszúrt kordinátákat kérdezem le.
'''

import json
import os

import geopandas as gpd
import numpy as np
from shapely import make_valid
from shapely.geometry import Point, box
from shapely.ops import unary_union
from shapely.vectorized import contains as shp_contains


LAKOTT_PARQUET  = "../data/nyers_data/varos_lakottterulet_hatar.parquet"
ADMIN_PARQUET   = "../data/nyers_data/varos_kozigazgatasi_hatar.parquet"
MS_PARQUET      = "../data/work_data/ms_minden_epulet.parquet"
OUT_PARQUET     = "../data/work_data/ms_kihagyott_teruletek.parquet"
OUT_GPKG        = "../data/test_data/ms_kihagyott_teruletek.gpkg"
OUT_JSON        = "../data/out_data/lekerdezendo_kordinatak_kihagyott_teruletek.json"
OUT_GPKG_PONTOK = "../data/test_data/lekerdezendo_kordinatak_kihagyott_teruletek.gpkg"

EOV_CRS       = 23700    # EPSG:23700 — EOV, területszámítás és tengelyirányú vágások
VAGAS_KUSZOB  = 0.10     # 10% — ettől tekintünk egy oldalt vágottnak
BBOX_PUFFER_M = 1000.0   # félsík-kivágó téglalap puffere (méter, EOV-ban)
SURUSEG_KM2   = 500     # mintavételi sűrűség: pont / km² (változtatható)


def _safe_make_valid(g):
    """A `szburkolas/polygon_fuggvenyek.py:_safe_make_valid` lokális másolata —
    invalid geometriákat (önmetsző poligonok stb.) javít. A `vag_residential_city`
    mindenhol ezt használja a metszés előtt és után, ezért nálunk is kell, hogy
    a klippelés ne legyen érzékeny az OSM-ből jövő enyhén hibás geometriákra."""
    if g is None or g.is_empty:
        return g
    try:
        return make_valid(g)
    except Exception:
        try:
            return g.buffer(0)
        except Exception:
            return g


def _szelet(poly, irany, ms_minx, ms_maxx, ms_miny, ms_maxy):
    """A `poly` azon szelete, ami az MS-burkolódobozon KÍVÜL esik az adott irányban
    (tengelyirányú félsík-metszet egy puffereit téglalappal)."""
    pxmin, pymin, pxmax, pymax = poly.bounds
    P = BBOX_PUFFER_M
    if irany == "bal":
        cutter = box(pxmin - P, pymin - P, ms_minx,   pymax + P)
    elif irany == "jobb":
        cutter = box(ms_maxx,   pymin - P, pxmax + P, pymax + P)
    elif irany == "fent":
        cutter = box(pxmin - P, ms_maxy,   pxmax + P, pymax + P)
    elif irany == "lent":
        cutter = box(pxmin - P, pymin - P, pxmax + P, ms_miny)
    else:
        raise ValueError(f"Ismeretlen irany: {irany}")
    return poly.intersection(cutter)


def _vagott_arany(poly, irany, ms_minx, ms_maxx, ms_miny, ms_maxy):
    """arany = ures_szelet.area / poly.area; üres metszet → 0.0."""
    szelet = _szelet(poly, irany, ms_minx, ms_maxx, ms_miny, ms_maxy)
    if szelet.is_empty:
        return 0.0
    return szelet.area / poly.area


def varosok_detaktalasa():
    # 1) lakott területi poligonok betöltése — EGY SOR = EGY landuse=residential poligon.
    #    NEM dissolve-olunk telepules-szerint: ha egy városnak több külön lakott területe
    #    van (központ + tanyák, vagy több, OSM-ben különálló településrész), a dissolve
    #    egyetlen MultiPolygon-ná olvasztaná őket. Akkor a `total_bounds` az egész
    #    MultiPolygon-on átfekvő MS-centroidok burkolódoboza lenne, és egy üres résztérség,
    #    amit MS-rich részek vesznek körül, "belső lyukként" sosem esne ki egyetlen
    #    tengelyirányú vágási szeletbe sem (minden arány < 10% → Eset C → némán kiesik).
    #    Per-poligon iterációval minden üres poligon önállóan Eset A-ba kerül.
    lakott = gpd.read_parquet(LAKOTT_PARQUET).to_crs(EOV_CRS).reset_index(drop=True)
    print(f"Nyers lakott területi poligonok: {len(lakott)}")

    # 1b) Klippelés a hivatalos közigazgatási határral — pontosan ugyanúgy, mint a
    # `szburkolas/polygon_fuggvenyek.py::vag_residential_city` (make_valid → intersection
    # → make_valid → típus-szűrés). Az OSM `landuse=residential` poligonokat az
    # `osm_letoltes.py` városonként `features_from_place`-szel kérdezi, ami minden
    # poligont visszaad ami METSZI a város határát — egy adminhatáron átlógó lakóterület
    # a szomszéd város lekérdezésekor is visszajön. Klippelés nélkül a sjoin az átlógó
    # részen lévő MS-centroidokat is hozzárendelné a vizsgált településhez.
    admin = gpd.read_parquet(ADMIN_PARQUET).to_crs(EOV_CRS)
    admin_lut = dict(zip(admin["telepules"], admin["geometry"]))
    print(f"Admin határok: {len(admin)}")

    klippelt = []
    n_empty_g = n_no_admin = n_empty_inter = n_wrong_type = 0
    for _, row in lakott.iterrows():
        telepules = row["telepules"]
        g = _safe_make_valid(row["geometry"])
        if g is None or g.is_empty:
            n_empty_g += 1
            continue

        a = admin_lut.get(telepules)
        if a is None:
            # nincs admin entry erre a városra (pl. geocode_to_gdf elhasalt) — eredeti
            # geometriát tartjuk meg, hogy ne veszítsük el a várost az iterációból
            n_no_admin += 1
            inter = g
        else:
            a = _safe_make_valid(a)
            try:
                inter = g.intersection(a)
            except Exception:
                n_empty_inter += 1
                continue
            inter = _safe_make_valid(inter)
            if inter is None or inter.is_empty:
                n_empty_inter += 1
                continue
            if inter.geom_type not in ("Polygon", "MultiPolygon"):
                # GeometryCollection / LineString tangenciális találat — kihagyjuk
                n_wrong_type += 1
                continue

        klippelt.append({"telepules": telepules, "geometry": inter})

    # explicit GeoDataFrame újraépítés — biztosítja hogy az aktív geometry oszlop
    # és a CRS helyesen legyen beállítva a downstream sjoin-hoz
    lakott = gpd.GeoDataFrame(klippelt, geometry="geometry", crs=EOV_CRS).reset_index(drop=True)
    print(f"Klippelés után érvényes poligonok: {len(lakott)} "
          f"(üres bemenet: {n_empty_g}, nincs admin: {n_no_admin}, "
          f"üres metszet: {n_empty_inter}, rossz típus: {n_wrong_type})")

    # 2) MS épületek — csak a középpontokkal dolgozunk (gyors, ~2 GB poligon helyett pontok)
    ms_full = gpd.read_parquet(MS_PARQUET)
    ms = gpd.GeoDataFrame(
        ms_full[["id"]].copy(),
        geometry=ms_full["kozeppont"],
        crs=4326,
    ).to_crs(EOV_CRS)
    print(f"MS épület-középpontok száma: {len(ms)}")

    # 3) melyik MS-centroid melyik LAKOTT POLIGON-ban van — per-poligon attribúció
    joined = gpd.sjoin(
        ms[["geometry"]],
        lakott[["geometry"]],
        how="inner",
        predicate="within",
    )
    # index_right == lakott sor-indexe (reset_index után 0..N-1)
    ms_per_poly = joined.groupby("index_right")
    erintett_poly = set(ms_per_poly.groups.keys())

    # 4) iteráció poligononként — vágás-teszt 4 irányból
    rows = []
    for idx, row in lakott.iterrows():
        telepules = row["telepules"]
        poly = row.geometry
        if poly.is_empty or poly.area == 0:
            continue

        # === Eset A: nincs egyetlen MS-centroid sem ebben a poligonban ===
        if idx not in erintett_poly:
            rows.append({
                "geometry":   poly,
                "telepules":  telepules,
                "eset":       "A_nincs_ms",
                "arany_fent": 0.0, "arany_lent": 0.0,
                "arany_bal":  0.0, "arany_jobb": 0.0,
            })
            continue

        # === Eset B/C: van MS adat — 4 irányú vágás-teszt ===
        ms_pts = ms_per_poly.get_group(idx)
        minx, miny, maxx, maxy = ms_pts.total_bounds

        aranyok = {
            "bal":  _vagott_arany(poly, "bal",  minx, maxx, miny, maxy),
            "jobb": _vagott_arany(poly, "jobb", minx, maxx, miny, maxy),
            "fent": _vagott_arany(poly, "fent", minx, maxx, miny, maxy),
            "lent": _vagott_arany(poly, "lent", minx, maxx, miny, maxy),
        }

        # stabil sorrend a címke-konzisztencia érdekében (fent, lent, bal, jobb)
        vagott_iranyok = [ir for ir in ("fent", "lent", "bal", "jobb")
                          if aranyok[ir] >= VAGAS_KUSZOB]
        if not vagott_iranyok:
            continue   # Eset C: nem vágott egy irányból sem

        szeletek = [_szelet(poly, ir, minx, maxx, miny, maxy) for ir in vagott_iranyok]
        ures = unary_union(szeletek)

        rows.append({
            "geometry":   ures,
            "telepules":  telepules,
            "eset":       "B_vagott_" + "_".join(vagott_iranyok),
            "arany_fent": aranyok["fent"], "arany_lent": aranyok["lent"],
            "arany_bal":  aranyok["bal"],  "arany_jobb": aranyok["jobb"],
        })

    # 5) mentés — vissza WGS84-re (storage konvenció), parquet + GPKG
    out = gpd.GeoDataFrame(rows, crs=EOV_CRS, geometry="geometry").to_crs(4326)
    print(f"Kihagyott területek: {len(out)} sor "
          f"(A_nincs_ms: {(out['eset'] == 'A_nincs_ms').sum()}, "
          f"B_vagott_*: {(out['eset'].str.startswith('B_')).sum()})")

    os.makedirs(os.path.dirname(OUT_PARQUET), exist_ok=True)
    out.to_parquet(OUT_PARQUET)

    os.makedirs(os.path.dirname(OUT_GPKG), exist_ok=True)
    out.to_file(OUT_GPKG, driver="GPKG", layer="kihagyott")


def mintavetelezes():
    """Az összes kihagyott_teruletek poligonra (A_nincs_ms + B_vagott_*) véletlen
    pontokat dob le `SURUSEG_KM2` pont/km² sűrűséggel — ez adja a Google Maps
    geocoding bemenetét azokra a területekre, ahol nincs (vagy hiányos) az MS adat.

    Rejection sampling a poligon bbox-án belül: bbox-ban generálunk uniform mintát,
    `shapely.vectorized.contains` szűr a poligonra. A megengedettnél kb. kétszer annyi
    próbálkozást generálunk körönként, hogy ritkán kelljen újra próbálkozni; aki
    bbox-ra vetítve túl alacsony fedéssel rendelkezik (vékony szelet), az automatikusan
    iterál még egy kört."""
    teruletek = gpd.read_parquet(OUT_PARQUET).to_crs(EOV_CRS).reset_index(drop=True)
    print(f"Mintavételezendő poligonok: {len(teruletek)} "
          f"(össz. terület: {teruletek.area.sum() / 1e6:.2f} km²)")

    rng = np.random.default_rng()
    pontok = []
    for _, row in teruletek.iterrows():
        poly = row.geometry
        if poly is None or poly.is_empty or poly.area == 0:
            continue

        N = max(1, int(round(poly.area / 1_000_000 * SURUSEG_KM2)))
        minx, miny, maxx, maxy = poly.bounds
        bevett = []
        # rejection-loop: bbox→poly fedés alapján 2x annyit dobunk fel mint kell
        while len(bevett) < N:
            hianyzo = N - len(bevett)
            xs = rng.uniform(minx, maxx, hianyzo * 2)
            ys = rng.uniform(miny, maxy, hianyzo * 2)
            bent = shp_contains(poly, xs, ys)
            for x, y in zip(xs[bent], ys[bent]):
                bevett.append((x, y))
                if len(bevett) >= N:
                    break

        for x, y in bevett:
            pontok.append({
                "geometry":  Point(x, y),
                "telepules": row["telepules"],
                "eset":      row["eset"],
            })

    pgdf = gpd.GeoDataFrame(pontok, geometry="geometry", crs=EOV_CRS).to_crs(4326)
    print(f"Mintavett pontok: {len(pgdf)}")

    # JSON — Google Maps konvenció: [lat, lon] (ld. ms_building_feldolgozas.py:64-67)
    kordinatak = list(zip(pgdf.geometry.y, pgdf.geometry.x))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(kordinatak, f, ensure_ascii=False, indent=4)

    # QGIS vizualizáció — hex color az eset szerint (A=piros, B_*=zöld)
    pgdf["color"] = np.where(pgdf["eset"] == "A_nincs_ms", "#e41a1c", "#1b9e77")
    os.makedirs(os.path.dirname(OUT_GPKG_PONTOK), exist_ok=True)
    pgdf.to_file(OUT_GPKG_PONTOK, driver="GPKG", layer="pontok")



def ms_ures_sample():

    varosok_detaktalasa()

    mintavetelezes()


if __name__ == "__main__":
    ms_ures_sample()
