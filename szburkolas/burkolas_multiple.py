"""
burkolas_multiple – a V3 burkolás kötegelt, többmagos futtatása minden településre.

Felosztás:
  - WORKER processzek: csak számolnak (burkol_telepules + reprojekció EOV-ba). A GPKG-hez nem
    nyúlnak (az sqlite nem bírja a párhuzamos írást).
  - FŐ processz: az EGYETLEN író – ahogy egy település elkészül, azonnal hozzáfűzi a gyűjtő
    GPKG-hez (QGIS-ben folyamatosan követhető), és egy sort ír a napló CSV-be (12. fejezet).

Folytatás: a gyűjtő `poligonok` rétegében már meglévő településeket kihagyjuk; a hozzáfűzés
településenként tranzakciós, megszakításnál nem marad részleges település.

Kimenet (leírás 11.): gyűjtő GPKG EOV vetületben
  poligonok: szkid, telepules, jelzes, szin, geometry
  cimek:     szkid, utca, hazszam, telepules, irsz, szin, geometry
  + <gyűjtő>_naplo.csv (szerkezeti mutatók településenként) + <gyűjtő>_log.txt
"""

import contextlib
import csv
import io
import logging
import multiprocessing
import os
import traceback
from pathlib import Path

import geopandas as gpd

import v3_io

logger = logging.getLogger('burkolas_multiple')

# Ennyi település után a worker-processz újraindul (lapos memória a többnapos futásnál).
# multiprocessing.Pool-t használunk, NEM ProcessPoolExecutor-t: az executor a maxtasksperchild
# miatt kilépő workereket nem pótolja megbízhatóan, ha minden task előre be van küldve.
MAX_TASKS_PER_CHILD = 25


def _setup_logging(log_file):
    """Időbélyeges logger: a FÁJL mindent kap (INFO+), a KONZOL csak a rendellenességeket (WARNING+)."""
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
    """A GDF-et a megadott fix sémára igazítja (hozzáfűzés-konzisztencia)."""
    g = gdf_in.copy()
    for col in cols:
        if col != 'geometry' and col not in g.columns:
            g[col] = None
    return g[cols]


def append_layer(gdf_in, path, layer, cols):
    """A sorokat a gyűjtő GPKG adott rétegéhez fűzi (új fájlnál létrehozza)."""
    g = _ensure_cols(gdf_in, cols)
    path = Path(path)
    if path.exists():
        g.to_file(path, layer=layer, driver='GPKG', mode='a')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        g.to_file(path, layer=layer, driver='GPKG')


def _kesz_telepulesek(out_gpkg):
    """A gyűjtő GPKG-ben MÁR meglévő településnevek (a folytatás alapja)."""
    out_gpkg = Path(out_gpkg)
    if not out_gpkg.exists():
        return set()
    try:
        df = gpd.read_file(out_gpkg, layer='poligonok', ignore_geometry=True)
    except Exception as e:
        logger.warning(f"A gyűjtő ({out_gpkg.name}) 'poligonok' rétege nem olvasható ({e!r}) – üres kész-listával indulok.")
        return set()
    return set(df['telepules'].dropna().unique())


def _naplo_sor(naplo_csv, varos, status, naplo):
    """Egy település mutatói a napló CSV-be (fejléc az első sornál)."""
    sor = {'telepules': varos, 'status': status, **naplo}
    uj = not Path(naplo_csv).exists()
    with open(naplo_csv, 'a', newline='', encoding='utf-8') as f:
        mezok = ['telepules', 'status', 'n_cella', 'n_korzet', 'n_kategoria', 'n_vegyes', 'vegyes_arany', 'ures_terulet_arany',
                 'vagas_T', 'vagas_kozep', 'vagas_kozos', 'vagas_parhuzamos', 'vagas_voronoi',
                 'jelzes_folytonossag', 'jelzes_haromszog', 'jelzes_validacio', 'jelzes_besorolatlan',
                 'n_rang3_torolt', 'n_narancs', 'qa_cimek_kivul', 'qa_foutak_kivul', 'lakott_gyanus',
                 'n_azonos_pont', 'n_hibas_sziget', 'egykategorias', 'nincs_utcahalozat', 'n_cim_hataron_kivul']
        iro = csv.DictWriter(f, fieldnames=mezok, extrasaction='ignore')
        if uj:
            iro.writeheader()
        iro.writerow(sor)


def _worker(task):
    """Egy település teljes burkolása (worker-oldal): számolás + reprojekció EOV-ba. Fájlba NEM ír.
    task = (varos, cimek_sub); visszatérés: (varos, status, korzetek, cimek, naplo, log_text)."""
    varos, cimek_sub = task
    from burkolas_v3 import burkol_telepules

    print(f'  ... dolgozom: {varos} ({len(cimek_sub)} cím) [pid={os.getpid()}]', flush=True)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            eredmeny = burkol_telepules(varos, cimek_sub, debug=False)
            if eredmeny.status != 'ok':
                return varos, eredmeny.status, None, None, {}, out.getvalue() + '\n' + eredmeny.uzenet
            korzetek = eredmeny.korzetek.to_crs(v3_io.TARGET_CRS)
            korzetek['telepules'] = varos
            cimek = eredmeny.cimek.to_crs(v3_io.TARGET_CRS)
            cimek['telepules'] = varos
            log_text = out.getvalue()
            if eredmeny.validacio_hibak:
                log_text += '\nVALIDÁCIÓ: ' + ' | '.join(eredmeny.validacio_hibak)
        return varos, 'ok', korzetek, cimek, eredmeny.naplo, log_text
    except Exception:
        return varos, 'hiba', None, None, {}, out.getvalue() + '\n' + traceback.format_exc()


def burkolas_multiple(cimek_gpkg, out_gpkg, varosok=None, n_workers=None):
    """Végigmegy a településeken TÖBB MAGON, a gyűjtő GPKG-t és a napló CSV-t a fő processz írja.

    cimek_gpkg  az A egység összekapcsolt gpkg-je (bemenet)
    out_gpkg    a gyűjtő GPKG (kimenet; a log és a napló CSV mellé kerül)
    varosok     településlista; None → varosok.txt
    n_workers   worker-processzek száma; None → cpu_count − 2
    """
    out_gpkg = Path(out_gpkg)
    out_gpkg.parent.mkdir(parents=True, exist_ok=True)
    _setup_logging(out_gpkg.with_name(out_gpkg.stem + '_log.txt'))
    naplo_csv = out_gpkg.with_name(out_gpkg.stem + '_naplo.csv')

    cimek_gpkg = Path(cimek_gpkg)
    if not cimek_gpkg.exists():
        raise FileNotFoundError(f'A bemeneti címek gpkg nem található: {cimek_gpkg}')
    if varosok is None:
        varosok = v3_io.olvas_varosok_txt()
    if n_workers is None:
        n_workers = max(1, (os.cpu_count() or 2) - 2)

    total = len(varosok)
    print(f'burkolas_multiple indul – {total} település, {n_workers} worker.\n  cimek_gpkg: {cimek_gpkg}\n  gyűjtő: {out_gpkg}')
    logger.info(f'burkolas_multiple indul – {total} település, {n_workers} worker. cimek_gpkg={cimek_gpkg}, gyujto={out_gpkg}')

    # a bemeneti pontokat EGYSZER olvassuk be, településenként előszűrve adjuk át a workereknek
    cimek_gdf = gpd.read_file(cimek_gpkg)
    cimek_by_varos = {t: sub for t, sub in cimek_gdf.groupby('telepules')}
    ures_sub = cimek_gdf.iloc[0:0]

    kesz = _kesz_telepulesek(out_gpkg)
    if kesz:
        print(f'  már kész (a gyűjtőben): {len(kesz)} település')
        logger.info(f'gyűjtőben már kész: {len(kesz)} település')

    szamlalo = {'ok': 0, 'skip': 0, 'nincs': 0, 'ures': 0, 'hiba': 0}
    fuggo = []
    for varos in varosok:
        if varos in kesz:
            szamlalo['skip'] += 1
        else:
            fuggo.append(varos)
    # a varosok.txt eredeti sorrendjében (kicsik-nagyok vegyesen), hogy folyamatosan szülessen kimenet
    tasks = [(varos, cimek_by_varos.get(varos, ures_sub)) for varos in fuggo]

    ctx = multiprocessing.get_context('spawn')
    with ctx.Pool(processes=n_workers, maxtasksperchild=MAX_TASKS_PER_CHILD) as pool:
        done = szamlalo['skip']
        for varos, status, korzetek, cimek, naplo, log_text in pool.imap_unordered(_worker, tasks, chunksize=1):
            done += 1
            print(f'=== {varos} ===  [{done}/{total} – {done / total:.1%}] – {status}', flush=True)
            logger.info(f'[{done}/{total}] {varos} – {status}')
            if log_text.strip():
                logger.info(f'[{varos}] worker-kimenet:\n{log_text.strip()}')
            if status != 'ok':
                szamlalo[status] += 1
                (logger.error if status == 'hiba' else logger.warning)(f'[{varos}] {status} – kihagyom.')
                _naplo_sor(naplo_csv, varos, status, {})
                continue
            try:
                append_layer(cimek, out_gpkg, 'cimek', v3_io.CIM_OSZLOPOK)
                append_layer(korzetek, out_gpkg, 'poligonok', v3_io.POLIGON_OSZLOPOK)
            except Exception as e:
                logger.error(f'[{varos}] gyűjtő-hozzáfűzés sikertelen: {e!r} – kihagyom.')
                szamlalo['hiba'] += 1
                continue
            _naplo_sor(naplo_csv, varos, status, naplo)
            logger.info(f'[{varos}] napló: {naplo}')
            szamlalo['ok'] += 1

    osszegzes = (f"KÉSZ – feldolgozva: {total} | sikeres: {szamlalo['ok']} | kihagyott (kész): {szamlalo['skip']} | "
                 f"nincs lakott/cím: {szamlalo['nincs']} | üres: {szamlalo['ures']} | hibás: {szamlalo['hiba']}")
    print(f'\n{osszegzes}')
    logger.info(osszegzes)
    return szamlalo


if __name__ == '__main__':
    CIMEK_GPKG = v3_io.ROOT / 'data' / 'work_data' / 'osszekapcsolt_pontok_es_cimek_2026.gpkg'
    OUT_GPKG = v3_io.ROOT / 'data' / 'kesz_data' / '2026_v3.gpkg'
    burkolas_multiple(CIMEK_GPKG, OUT_GPKG)
