import traceback

import pandas as pd
import geopandas as gpd

from burkolas_v3 import generalas_pipeline



gdf = gpd.read_file('./adatok/osszekapcsolt_pontok_es_cimek.gpkg')

VAROSOK = gdf['telepules'].unique().tolist()
# VAROSOK = [
#     'Enying',
#     'Ibafa',
#     'Csebény',
# ]

OUTPUT = './adatok/osszesitett.gpkg'
TARGET_CRS = 'EPSG:23700'  # EOV — Magyarországra egységes metrikus vetület


poligonok = []
cimek = []
hibak = []            # (VAROS, exception_repr, traceback)
nincs_lakott = []     # VAROS-ok, ahol generalas_pipeline None-t adott

TOTAL = len(VAROSOK)

for i, VAROS in enumerate(VAROSOK, start=1):
    pct = i / TOTAL * 100
    print(f"\n=== {VAROS} ===  [{i}/{TOTAL} — {pct:.1f}%]")
    try:
        gdf_cimek, gdf_poly = generalas_pipeline(VAROS, debug=False, export=False)
    except Exception as e:
        tb = traceback.format_exc()
        hibak.append((VAROS, repr(e), tb))
        print(f"[{VAROS}] HIBA: {e!r} — kihagyom.")
        continue

    if gdf_cimek is None:
        nincs_lakott.append(VAROS)
        continue

    gdf_cimek = gdf_cimek.to_crs(TARGET_CRS)
    gdf_poly = gdf_poly.to_crs(TARGET_CRS)
    gdf_cimek['telepules'] = VAROS
    gdf_poly['telepules'] = VAROS

    cimek.append(gdf_cimek)
    poligonok.append(gdf_poly)

poly_all = gpd.GeoDataFrame(
    pd.concat(poligonok, ignore_index=True), crs=TARGET_CRS
)
cim_all = gpd.GeoDataFrame(
    pd.concat(cimek, ignore_index=True), crs=TARGET_CRS
)

poly_all.to_file(OUTPUT, layer='poligonok', driver='GPKG')
cim_all.to_file(OUTPUT, layer='cimek', driver='GPKG')

print(f"\nKész: {OUTPUT} ({len(poly_all)} poligon, {len(cim_all)} cím)")
print(f"Feldolgozva: {TOTAL} település — sikeres: {len(poligonok)}, kihagyott (nincs lakott terület): {len(nincs_lakott)}, hibás: {len(hibak)}")

if nincs_lakott:
    print("\n--- Nincs lakott területi poligon ---")
    for v in nincs_lakott:
        print(f"  - {v}")

if hibak:
    print("\n--- Hibák ---")
    for v, err, tb in hibak:
        print(f"\n[{v}] {err}")
        print(tb)
