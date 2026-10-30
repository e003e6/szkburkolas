"""
burkolas_multiple_tobbmag — a burkolas_multiple TÖBBMAGOS változata.

Működése azonos a burkolas_multiple-lel (bemenet, kimenet, log, folytatás-logika),
de a városok generálását (generalas_pipeline — Voronoi, dekompozíció, a CPU-igényes
rész) párhuzamosan, több processzen futtatja. A GIL miatt threading nem gyorsítana,
ezért multiprocessing (ProcessPoolExecutor).

Felosztás:
  - WORKER processzek: csak számolnak — generalas_pipeline + reprojekció. A GPKG-hez
    nem nyúlnak (sqlite nem bírja a párhuzamos írást).
  - FŐ processz: az EGYETLEN író — ahogy egy város elkészül (as_completed), azonnal
    append-eli a gyűjtő GPKG-be, így QGIS-ban folyamatosan követhető a haladás.

A folytatás alapja változatlanul a gyűjtő TARTALMA: a 'poligonok' layerben már
meglévő településeket kihagyjuk; az append városonként tranzakciós, megszakításnál
nem marad részleges város.

Futtatás a `szburkolas/` könyvtárból (a generalas_pipeline `../data/...` relatív utat használ).
"""

import contextlib
import io
import multiprocessing
import os
import traceback
from pathlib import Path

import geopandas as gpd

from burkolas_multiple import (
    CIM_COLS, POLY_COLS, TARGET_CRS, VAROSOK_TXT,
    _kesz_telepulesek, _setup_logging, append_layer, logger,
)

ROOT = Path(__file__).resolve().parent.parent

# Ennyi város után a worker-processz újraindul (lapos memória a többnapos futásnál).
# FONTOS: multiprocessing.Pool-t használunk, NEM concurrent.futures.ProcessPoolExecutor-t —
# az executor a max_tasks_per_child miatt kilépő workereket nem pótolja megbízhatóan, ha
# minden task előre be van küldve (a pool 1 processzre sorvad, empirikusan reprodukálva
# Python 3.13.1-en). A mp.Pool külön worker-karbantartó szála mindig visszapótol.
MAX_TASKS_PER_CHILD = 25


def _worker_init():
    """Worker-processz indulásakor fut, MIELŐTT a burkolas_v3 (és vele a matplotlib)
    importálódna — headless backend, hogy spawn alatt ne próbáljon GUI-t nyitni."""
    os.environ['MPLBACKEND'] = 'Agg'


def _worker(task):
    """Egy város teljes generálása. A soros burkolas_multiple ciklustörzsének
    worker-oldali fele: pipeline + reprojekció + telepules oszlop. Fájlba NEM ír.

    task = (VAROS, cimek_sub, target_crs) — egyetlen argumentum, mert a
    Pool.imap_unordered így adja át.

    A generalas_pipeline print-kimenetét elkapjuk és visszaadjuk, hogy a fő processz
    városonként egyben logolhassa (ne keveredjen össze a párhuzamos kimenet).

    Visszatérés: (VAROS, status, gdf, merged, log_text)
      status: 'ok' | 'nincs' | 'ures' | 'hiba' (hibánál a log_text a traceback).
    """
    VAROS, cimek_sub, target_crs = task
    # az import itt történik, hogy a spawn-olt worker a _worker_init utáni
    # környezettel töltse be a burkolas_v3-at (matplotlib backend)
    from burkolas_v3 import generalas_pipeline

    # élő státusz a terminálra (a worker örökli a konzolt): látszik, éppen melyik
    # településen dolgoznak a magok — nagyvárosnál sokáig ez az egyetlen életjel
    print(f"  ... dolgozom: {VAROS} ({len(cimek_sub)} cím) [pid={os.getpid()}]", flush=True)

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            gdf, merged = generalas_pipeline(VAROS, debug=False, export=False,
                                             cimek_gdf=cimek_sub)

            if gdf is None or merged is None:
                return VAROS, 'nincs', None, None, out.getvalue()
            if merged.empty:
                return VAROS, 'ures', None, None, out.getvalue()

            # reprojekció is a workerben (CPU) — a fő processz csak ír
            merged = merged.to_crs(target_crs)
            gdf = gdf.to_crs(target_crs)
            merged['telepules'] = VAROS
        return VAROS, 'ok', gdf, merged, out.getvalue()
    except Exception:
        return VAROS, 'hiba', None, None, out.getvalue() + '\n' + traceback.format_exc()


def burkolas_multiple_tobbmag(cimek_gpkg, out_gpkg,
                              varosok=None, target_crs=TARGET_CRS, n_workers=None):
    """Végigmegy a településeken TÖBB MAGON, a gyűjtő GPKG-t a fő processz append-eli.

    Paraméterek a burkolas_multiple-lel azonosak, plusz:
    n_workers — worker-processzek száma; None esetén cpu_count - 2
                (marad mag az OS-nek és a fő író processznek).
    """
    out_gpkg = Path(out_gpkg)
    out_gpkg.parent.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_gpkg.with_name(out_gpkg.stem + '_log.txt'))

    cimek_gpkg = Path(cimek_gpkg)
    if not cimek_gpkg.exists():
        raise FileNotFoundError(f"A bemeneti címek gpkg nem található: {cimek_gpkg}")

    if varosok is None:
        varosok = [v.strip() for v in VAROSOK_TXT.read_text(encoding='utf-8').splitlines() if v.strip()]

    if n_workers is None:
        n_workers = max(1, (os.cpu_count() or 2) - 2)

    total = len(varosok)
    print(f"burkolas_multiple_tobbmag indul — {total} település, {n_workers} worker."
          f"\n  cimek_gpkg: {cimek_gpkg}\n  gyűjtő: {out_gpkg}")
    logger.info(f"burkolas_multiple_tobbmag indul — {total} település, {n_workers} worker. "
                f"cimek_gpkg={cimek_gpkg}, gyujto={out_gpkg}")

    # a bemeneti pontokat EGYSZER olvassuk be, és városonként előszűrve adjuk át a
    # workereknek — így taskonként csak a saját város pontjai pickle-öződnek át
    cimek_gdf = gpd.read_file(cimek_gpkg)
    cimek_by_varos = {telepules: sub for telepules, sub in cimek_gdf.groupby('telepules')}
    ures_sub = cimek_gdf.iloc[0:0]  # város, amihez nincs egyetlen cím sem

    # folytatás: ami már benne van a gyűjtőben, azt nem generáljuk újra
    kesz_telepulesek = _kesz_telepulesek(out_gpkg)
    if kesz_telepulesek:
        print(f"  már kész (a gyűjtőben): {len(kesz_telepulesek)} település")
        logger.info(f"gyűjtőben már kész: {len(kesz_telepulesek)} település")

    n_ok = n_skip = n_nincs = n_hiba = n_ures = 0

    fuggo = []
    for VAROS in varosok:
        if VAROS in kesz_telepulesek:
            logger.info(f"[{VAROS}] kihagyom — már benne van a gyűjtőben.")
            n_skip += 1
        else:
            fuggo.append(VAROS)

    # SZÁNDÉKOSAN a varosok.txt eredeti sorrendjében megyünk (kicsik-nagyok vegyesen):
    # ha méret szerint rendeznénk (nagyok előre), az összes worker órákig egy-egy
    # óriásvároson ülne, és addig SEMMI kimenet nem születne — se terminál, se gpkg.
    # Vegyes sorrendnél folyamatosan készülnek el települések, látszik a haladás.
    tasks = [(VAROS, cimek_by_varos.get(VAROS, ures_sub), target_crs) for VAROS in fuggo]

    ctx = multiprocessing.get_context('spawn')
    with ctx.Pool(processes=n_workers, initializer=_worker_init,
                  maxtasksperchild=MAX_TASKS_PER_CHILD) as pool:
        done = n_skip
        for VAROS, status, gdf, merged, log_text in pool.imap_unordered(_worker, tasks, chunksize=1):
            done += 1
            pct = done / total * 100
            print(f"=== {VAROS} ===  [{done}/{total} — {pct:.1f}%] — {status}", flush=True)
            logger.info(f"[{done}/{total} — {pct:.1f}%] {VAROS} — {status}")
            if log_text.strip():
                logger.info(f"[{VAROS}] worker-kimenet:\n{log_text.strip()}")

            if status == 'hiba':
                logger.error(f"[{VAROS}] HIBA a generálás közben — kihagyom.")
                n_hiba += 1
                continue
            if status == 'nincs':
                logger.warning(f"[{VAROS}] nincs lakott terület / nincs szavazóköri cím — kihagyom.")
                n_nincs += 1
                continue
            if status == 'ures':
                logger.warning(f"[{VAROS}] üres poligon-eredmény — kihagyom.")
                n_ures += 1
                continue

            # gyűjtő: append két rétegbe — CSAK a fő processz ír. Előbb a 'cimek',
            # utána a 'poligonok' (folytatás-marker: csak teljes városnál kerül be).
            try:
                append_layer(gdf, out_gpkg, 'cimek', CIM_COLS)
                append_layer(merged, out_gpkg, 'poligonok', POLY_COLS)
            except Exception as e:
                logger.error(f"[{VAROS}] gyűjtő-hozzáfűzés sikertelen: {e!r} — kihagyom.")
                n_hiba += 1
                continue

            n_ok += 1

    osszegzes = (f"KÉSZ — feldolgozva: {total} | sikeres: {n_ok} | kihagyott (kész): {n_skip} | "
                 f"nincs lakott: {n_nincs} | üres: {n_ures} | hibás: {n_hiba}")
    print(f"\n{osszegzes}")
    logger.info(osszegzes)
    return {'total': total, 'ok': n_ok, 'skip': n_skip,
            'nincs_lakott': n_nincs, 'ures': n_ures, 'hiba': n_hiba}


if __name__ == '__main__':
    # Bemenet: az összekapcsolt gpkg konkrét elérési útja.
    CIMEK_GPKG = ROOT / 'data' / 'work_data' / 'osszekapcsolt_pontok_es_cimek_2014.gpkg'
    # Kimenet: a gyűjtő gpkg konkrét elérési útja (a log mellé kerül: 2026_uj_log.txt).
    OUT_GPKG = ROOT / 'data' / 'kesz_data' / '2014_tobbmag.gpkg'

    # Burkolás minden településre több magon — a gyűjtőben már meglévőket kihagyja.
    burkolas_multiple_tobbmag(CIMEK_GPKG, OUT_GPKG)
