"""
burkolas_multiple — szavazóköri poligonok batch-generálása minden településre.

Több NAPOS futásra tervezve, ezért a kód idővel NEM lassul:
  - a gyűjtőfájlt append-pel frissíti → körönként konstans költség (nincs O(n²) újraírás),
  - a folytatás alapja a gyűjtő GPKG TARTALMA: induláskor kiolvassuk, mely `telepules`
    értékek vannak már benne, és azokat kihagyjuk → leállás-biztos, memóriába-gyűjtés nélkül.

Bemenet:
  - cimek_gpkg — az összekapcsolt pontok gpkg KONKRÉT elérési útja (az adatstruktua/
    szkid_cim_kapcsolas.py kimenete, egységes séma). Egyszer olvassuk be, és minden
    településhez ezt szűrjük.

Kimenet:
  - out_gpkg — egyetlen gyűjtő GPKG (konkrét elérési út: mappa + fájlnév), append-pel
    frissítve, KÉT réteggel (a mappát létrehozzuk, ha nincs):
      'poligonok' — szavazóköri poligonok (szkid + szín) az ÖSSZES városra — a hiteles
                    kimenet, egyben a folytatás-marker,
      'cimek'     — a besorolt címpontok a poligonjukkal AZONOS színnel (debug/ellenőrzés).
  - <out_gpkg>_log.txt — a log ugyanabba a mappába, a gpkg nevével + '_log' végződéssel
    (pl. 2026.gpkg → 2026_log.txt).

Futtatás a `szburkolas/` könyvtárból (a generalas_pipeline `../data/...` relatív utat használ).
"""

import gc
import logging
from pathlib import Path

import geopandas as gpd

from burkolas_v3 import generalas_pipeline


ROOT = Path(__file__).resolve().parent.parent
VAROSOK_TXT = ROOT / 'data' / 'nyers_data' / 'varosok.txt'

TARGET_CRS = 'EPSG:23700'  # EOV — Magyarországra egységes metrikus vetület
POLY_COLS = ['szavazokorid', 'color', 'telepules', 'geometry']  # a 'poligonok' layer fix sémája
CIM_COLS = ['szavazokorid', 'color', 'utca', 'hazszam', 'telepules', 'irsz', 'geometry']  # a 'cimek' layer fix sémája

GC_EVERY = 50  # ennyi városonként egy gc.collect() (lapos memória a hosszú futásnál)

logger = logging.getLogger('burkolas_multiple')


def _setup_logging(log_file):
    """Időbélyeges logger: a FÁJL mindent kap (INFO+), a KONZOL csak a rendellenességeket
    (WARNING+). A %-os haladást külön print() írja a konzolra. Idempotens."""
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S')
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _ensure_cols(gdf_in, cols):
    """A GDF-et a megadott fix sémára igazítja (append-konzisztencia)."""
    g = gdf_in.copy()
    for col in cols:
        if col != 'geometry' and col not in g.columns:
            g[col] = None
    return g[cols]


def append_layer(gdf_in, path, layer, cols):
    """A sorokat a gyűjtő GPKG adott layeréhez fűzi. Ha a fájl még nincs → létrehozó írás,
    különben append (mode='a' meglévő fájlban új layert is létrehoz)."""
    g = _ensure_cols(gdf_in, cols)
    path = Path(path)
    if path.exists():
        g.to_file(path, layer=layer, driver='GPKG', mode='a')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        g.to_file(path, layer=layer, driver='GPKG')


def _kesz_telepulesek(out_gpkg):
    """A gyűjtő GPKG-ben MÁR meglévő településnevek halmaza (a folytatás alapja).
    Geometria nélkül, csak az attribútumokat olvassa — induláskor egyszeri költség."""
    out_gpkg = Path(out_gpkg)
    if not out_gpkg.exists():
        return set()
    try:
        df = gpd.read_file(out_gpkg, layer='poligonok', ignore_geometry=True)
    except Exception as e:
        logger.warning(f"A gyűjtő ({out_gpkg.name}) 'poligonok' layere nem olvasható ({e!r}) "
                       f"— üres kész-listával indulok.")
        return set()
    return set(df['telepules'].dropna().unique())


def burkolas_multiple(cimek_gpkg, out_gpkg,
                      varosok=None, target_crs=TARGET_CRS):
    """Végigmegy a településeken, a gyűjtő GPKG-t append-eli, mindent logol.

    A folytatás alapja a gyűjtő TARTALMA: induláskor kiolvassuk, mely `telepules`
    értékek vannak már benne, azokat kihagyjuk — nem generáljuk újra.

    cimek_gpkg — a BEMENETI összekapcsolt pontok gpkg konkrét elérési útja
                 (szavazokorid + cím + koordináta). Egyszer olvassuk be.
    out_gpkg   — a KIMENETI gyűjtő gpkg konkrét elérési útja (mappa + fájlnév);
                 a mappát létrehozzuk, ha nincs. Két réteg: 'poligonok' (szkid-poligonok)
                 és 'cimek' (besorolt címpontok a poligonjuk színével — debug).
                 A log ugyanoda kerül, a gpkg nevével + '_log' végződéssel
                 (pl. 2026.gpkg → 2026_log.txt).
    varosok    — településnevek listája; None esetén a varosok.txt.
    target_crs — közös kimeneti vetület (alap: EOV, EPSG:23700).
    """
    out_gpkg = Path(out_gpkg)
    out_gpkg.parent.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_gpkg.with_name(out_gpkg.stem + '_log.txt'))

    cimek_gpkg = Path(cimek_gpkg)
    if not cimek_gpkg.exists():
        raise FileNotFoundError(f"A bemeneti címek gpkg nem található: {cimek_gpkg}")

    if varosok is None:
        varosok = [v.strip() for v in VAROSOK_TXT.read_text(encoding='utf-8').splitlines() if v.strip()]

    total = len(varosok)
    print(f"burkolas_multiple indul — {total} település.\n  cimek_gpkg: {cimek_gpkg}"
          f"\n  gyűjtő: {out_gpkg}")
    logger.info(f"burkolas_multiple indul — {total} település. cimek_gpkg={cimek_gpkg}, "
                f"gyujto={out_gpkg}")

    # a bemeneti pontokat EGYSZER olvassuk be, és településenként ezt szűrjük (nem fájlonként újra)
    cimek_gdf = gpd.read_file(cimek_gpkg)

    # folytatás: ami már benne van a gyűjtőben, azt nem generáljuk újra
    kesz_telepulesek = _kesz_telepulesek(out_gpkg)
    if kesz_telepulesek:
        print(f"  már kész (a gyűjtőben): {len(kesz_telepulesek)} település")
        logger.info(f"gyűjtőben már kész: {len(kesz_telepulesek)} település")

    n_ok = n_skip = n_nincs = n_hiba = n_ures = 0

    for i, VAROS in enumerate(varosok, start=1):
        pct = i / total * 100
        print(f"\n=== {VAROS} ===  [{i}/{total} — {pct:.1f}%]")
        logger.info(f"[{i}/{total} — {pct:.1f}%] {VAROS}")

        if VAROS in kesz_telepulesek:
            logger.info(f"[{VAROS}] kihagyom — már benne van a gyűjtőben.")
            n_skip += 1
            continue

        try:
            gdf, merged = generalas_pipeline(VAROS, debug=False, export=False, cimek_gdf=cimek_gdf)
        except Exception as e:
            logger.exception(f"[{VAROS}] HIBA a generálás közben: {e!r} — kihagyom.")
            n_hiba += 1
            continue

        if gdf is None or merged is None:
            logger.warning(f"[{VAROS}] nincs lakott terület / nincs szavazóköri cím — kihagyom.")
            n_nincs += 1
            continue

        if merged.empty:
            logger.warning(f"[{VAROS}] üres poligon-eredmény — kihagyom.")
            n_ures += 1
            continue

        try:
            merged = merged.to_crs(target_crs)
            gdf = gdf.to_crs(target_crs)
        except Exception as e:
            logger.error(f"[{VAROS}] reprojekció ({target_crs}) sikertelen: {e!r} — kihagyom.")
            n_hiba += 1
            continue

        merged['telepules'] = VAROS

        # gyűjtő: append két rétegbe. Előbb a 'cimek' (besorolt címpontok a poligonjuk
        # színével — debug/ellenőrzés), utána a 'poligonok' — ez a folytatás-marker,
        # csak akkor kerül be, ha a város teljesen kész. A GDAL hívásonként tranzakcióban
        # ír, így hibánál nem marad részleges append; a település kimarad a kész-listából,
        # a következő futás újrapróbálja.
        try:
            append_layer(gdf, out_gpkg, 'cimek', CIM_COLS)
            append_layer(merged, out_gpkg, 'poligonok', POLY_COLS)
        except Exception as e:
            logger.error(f"[{VAROS}] gyűjtő-hozzáfűzés sikertelen: {e!r} — kihagyom.")
            n_hiba += 1
            continue

        n_ok += 1

        del gdf, merged
        if i % GC_EVERY == 0:
            gc.collect()

    osszegzes = (f"KÉSZ — feldolgozva: {total} | sikeres: {n_ok} | kihagyott (kész): {n_skip} | "
                 f"nincs lakott: {n_nincs} | üres: {n_ures} | hibás: {n_hiba}")
    print(f"\n{osszegzes}")
    logger.info(osszegzes)
    return {'total': total, 'ok': n_ok, 'skip': n_skip,
            'nincs_lakott': n_nincs, 'ures': n_ures, 'hiba': n_hiba}


if __name__ == '__main__':
    # Bemenet: az összekapcsolt gpkg konkrét elérési útja.
    CIMEK_GPKG = ROOT / 'data' / 'work_data' / 'osszekapcsolt_pontok_es_cimek_2026.gpkg'
    # Kimenet: a gyűjtő gpkg konkrét elérési útja (a log mellé kerül: 2026_log.txt).
    OUT_GPKG = ROOT / 'data' / 'kesz_data' / '2026_uj.gpkg'

    # Burkolás minden településre — a gyűjtőben már meglévőket kihagyja.
    burkolas_multiple(CIMEK_GPKG, OUT_GPKG)
