"""
burkolas_multiple — szavazóköri poligonok batch-generálása minden településre.

Több NAPOS futásra tervezve, ezért a kód idővel NEM lassul:
  - városonként KÜLÖN exportál → folytatható, leállás-biztos (nincs memóriába-gyűjtés),
  - a gyűjtőfájlt append-pel frissíti → körönként konstans költség (nincs O(n²) újraírás).

Kimenet:
  - data/kesz_data/<telepulesnev>.gpkg — városonként külön (ékezet/szóköz nélküli fájlnév),
    `poligonok` (+ `cimek`) layerrel. A fájl LÉTEZÉSE a folytatás alapja: ha megvan, kihagyjuk.
  - data/test_data/telepulesnev_szavazokor_poligonok.gpkg — egyetlen gyűjtő GPKG, az ÖSSZES
    város poligonjával, append-pel frissítve. Induláskor egyszer újraépül a kesz_data-ból.
  - data/burkolas_multiple.log — minden rendellenesség (ami nem sima siker) logja.

Futtatás a `szburkolas/` könyvtárból (a generalas_pipeline `../data/...` relatív utat használ).
"""

import gc
import logging
import re
import unicodedata
from pathlib import Path

import geopandas as gpd

from burkolas_v3 import generalas_pipeline


ROOT = Path(__file__).resolve().parent.parent
VAROSOK_TXT = ROOT / 'data' / 'nyers_data' / 'varosok.txt'
KESZ_DATA_DIR = ROOT / 'data' / 'kesz_data'
TEST_DATA_DIR = ROOT / 'data' / 'test_data'
TEST_DATA_FILE = TEST_DATA_DIR / 'telepulesnev_szavazokor_poligonok.gpkg'
LOG_FILE = ROOT / 'data' / 'burkolas_multiple.log'

TARGET_CRS = 'EPSG:23700'  # EOV — Magyarországra egységes metrikus vetület
POLY_COLS = ['szavazokorid', 'color', 'telepules', 'geometry']  # a poligon-layer fix sémája

GC_EVERY = 50  # ennyi városonként egy gc.collect() (lapos memória a hosszú futásnál)

logger = logging.getLogger('burkolas_multiple')


def _setup_logging(log_file=LOG_FILE):
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


def ekezet_nelkul_filenev(nev):
    """Településnévből biztonságos fájlnév-törzs: ékezet és szóköz nélkül, tiszta ASCII.

    NFKD-bontás leválasztja a kombináló jeleket (az `ő`/`ű` dupla-acute-ot is), ezeket
    eldobjuk; a szóközöket aláhúzásra cseréljük, a maradék nem-alfanumerikust töröljük.
    Pl. 'Pásztó' -> 'Paszto', 'Tököl' -> 'Tokol'.
    """
    nfkd = unicodedata.normalize('NFKD', nev)
    s = ''.join(c for c in nfkd if not unicodedata.combining(c))
    s = s.encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+', '_', s.strip())
    s = re.sub(r'[^A-Za-z0-9_]', '', s)
    return s


def _ensure_poly_cols(gdf_poly):
    """A poligon-GDF-et a POLY_COLS fix sémára igazítja (append-konzisztencia)."""
    g = gdf_poly.copy()
    for col in POLY_COLS:
        if col != 'geometry' and col not in g.columns:
            g[col] = None
    return g[POLY_COLS]


def append_poligonok(gdf_poly, path, layer='poligonok'):
    """A poligonokat a gyűjtő GPKG layeréhez fűzi. Ha a fájl még nincs → létrehozó írás,
    különben append (mode='a'). Az explicit létezés-check teszi driver-függetlenné."""
    g = _ensure_poly_cols(gdf_poly)
    path = Path(path)
    if path.exists():
        g.to_file(path, layer=layer, driver='GPKG', mode='a')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        g.to_file(path, layer=layer, driver='GPKG')


def _rebuild_test_data(output_dir, test_file):
    """A gyűjtőfájlt induláskor EGYSZER újraépíti a meglévő kesz_data poligonokból, hogy
    folytatott futásnál is teljes legyen. Egyszeri O(kész) költség, NEM körönkénti."""
    if test_file.exists():
        test_file.unlink()
    keszek = sorted(output_dir.glob('*.gpkg'))
    n = 0
    for f in keszek:
        try:
            gpoly = gpd.read_file(f, layer='poligonok')
        except Exception as e:
            logger.warning(f"test_data újraépítés: '{f.name}' poligonok layer nem olvasható ({e!r}) — kihagyom.")
            continue
        if gpoly.empty:
            continue
        try:
            append_poligonok(gpoly, test_file)
            n += 1
        except Exception as e:
            logger.error(f"test_data újraépítés: '{f.name}' hozzáfűzése sikertelen ({e!r}).")
    logger.info(f"test_data újraépítve {n} meglévő kesz_data fájlból: {test_file}")
    return n


def burkolas_multiple(output_dir=KESZ_DATA_DIR, test_file=TEST_DATA_FILE,
                      varosok=None, target_crs=TARGET_CRS, rebuild_test_data=True):
    """Végigmegy a településeken, városonként külön exportál, a gyűjtőt append-eli, mindent logol.

    output_dir       — városonkénti kesz GPKG-k könyvtára (alap: data/kesz_data).
    test_file        — egyetlen gyűjtő GPKG az összes város poligonjával.
    varosok          — településnevek listája; None esetén a varosok.txt.
    target_crs       — közös kimeneti vetület (alap: EOV, EPSG:23700).
    rebuild_test_data— induláskor újraépítse-e a gyűjtőt a kesz_data-ból (folytatás-biztos).
    """
    _setup_logging()
    output_dir = Path(output_dir)
    test_file = Path(test_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    test_file.parent.mkdir(parents=True, exist_ok=True)

    if varosok is None:
        varosok = [v.strip() for v in VAROSOK_TXT.read_text(encoding='utf-8').splitlines() if v.strip()]

    total = len(varosok)
    print(f"burkolas_multiple indul — {total} település.\n  kesz_data: {output_dir}\n  test_data: {test_file}")
    logger.info(f"burkolas_multiple indul — {total} település. kesz_data={output_dir}, test_data={test_file}")

    if rebuild_test_data:
        _rebuild_test_data(output_dir, test_file)

    n_ok = n_skip = n_nincs = n_hiba = n_ures = 0
    safe_map = {}  # safe-fájlnév -> VAROS, névütközés-figyeléshez

    for i, VAROS in enumerate(varosok, start=1):
        pct = i / total * 100
        print(f"\n=== {VAROS} ===  [{i}/{total} — {pct:.1f}%]")
        logger.info(f"[{i}/{total} — {pct:.1f}%] {VAROS}")

        safe = ekezet_nelkul_filenev(VAROS)
        if safe in safe_map and safe_map[safe] != VAROS:
            logger.warning(f"Fájlnév-ütközés: '{VAROS}' és '{safe_map[safe]}' is '{safe}.gpkg'-re képződik.")
        safe_map[safe] = VAROS

        out_path = output_dir / f"{safe}.gpkg"
        if out_path.exists():
            logger.info(f"[{VAROS}] kihagyom — már kész ({out_path.name}).")
            n_skip += 1
            continue

        try:
            gdf, merged = generalas_pipeline(VAROS, debug=False, export=False)
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
        except Exception as e:
            logger.error(f"[{VAROS}] reprojekció ({target_crs}) sikertelen: {e!r} — kihagyom.")
            n_hiba += 1
            continue

        merged['telepules'] = VAROS

        # városonkénti kesz_data: EGYETLEN 'poligonok' layer (szkid + szín). Ez a HITELES,
        # folytatás-alapú kimenet. Címpontot szándékosan NEM írunk ide.
        try:
            _ensure_poly_cols(merged).to_file(out_path, layer='poligonok', driver='GPKG')
        except Exception as e:
            logger.error(f"[{VAROS}] kesz_data írás sikertelen ({out_path.name}): {e!r} — kihagyom.")
            if out_path.exists():  # részleges fájl törlése, hogy a folytatás újrapróbálja
                try:
                    out_path.unlink()
                except OSError:
                    pass
            n_hiba += 1
            continue

        # gyűjtő test_data: append. NEM hitelességi forrás → hibára nem buktatjuk a várost.
        try:
            append_poligonok(merged, test_file)
        except Exception as e:
            logger.error(f"[{VAROS}] test_data hozzáfűzés sikertelen: {e!r} (a kesz_data megvan).")

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
    burkolas_multiple()
