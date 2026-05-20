import os
import glob
import json
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape


INPUT_GLOB = "../data/nyers_data/microsoft_download/*.csv"
RAW_PARQUET = "../data/work_data/ms_minden_epulet.parquet"
TEST_GPKG = "../data/test_data/ms_minden_epulet.gpkg"
OUT_JSON = "../data/out_data/lekerdezendo_kordinatak_ms.json"

METRIKUS_CRS = 32633
ALAPTERULET_KUSZOB_M2 = 30


def _chunkok_beolvasasa(glob_pattern):
    """Az MS geojsonl chunkokat egyetlen EPSG:4326-os GeoDataFrame-be egyesíti."""
    files = glob.glob(glob_pattern)
    assert len(files) > 0, f"Nincs feldolgozható chunk: {glob_pattern}"

    chunks = []
    for f in files:
        df = pd.read_json(f, lines=True)
        df["geometry"] = df["geometry"].apply(shape)
        chunks.append(gpd.GeoDataFrame(df, crs=4326))

    return gpd.GeoDataFrame(pd.concat(chunks, ignore_index=True), crs=4326)


def ms_building_feldolgozas():
    # 1) MS poligonok beolvasása és egyesítése
    tgdf = _chunkok_beolvasasa(INPUT_GLOB)
    print(f"Microsoft poligonok száma: {len(tgdf)}")

    # 2) középpontok EPSG:4326-ban (lat/lon — Google-geokódolás bemenete)
    tgdf["kozeppont"] = tgdf.geometry.centroid

    # 3) nyers parquet archiválás (geometry + kozeppont, id oszloppal)
    os.makedirs(os.path.dirname(RAW_PARQUET), exist_ok=True)
    tgdf[["geometry", "kozeppont"]] \
        .reset_index() \
        .rename(columns={"index": "id"}) \
        .to_parquet(RAW_PARQUET)

    # 4) metrikus vetület alapterület-számításhoz — a `kozeppont` másodlagos
    #    geometry oszlop EPSG:4326-ban MARAD (to_crs csak az aktív geom-et viszi)
    mgdf = tgdf.to_crs(epsg=METRIKUS_CRS)
    mgdf["alapterulet_m2"] = mgdf.geometry.area

    # 5) szűrési maszk + színezés QGIS-vizualizációhoz (zöld = marad, piros = kiszűrt)
    megmaradt_mask = mgdf["alapterulet_m2"] > ALAPTERULET_KUSZOB_M2
    mgdf["color"] = megmaradt_mask.map({True: "#1b9e77", False: "#e41a1c"})

    # 6) GPKG export — a `kozeppont` másodlagos geom-et ki kell hagyni (GPKG egy geom-et kezel)
    os.makedirs(os.path.dirname(TEST_GPKG), exist_ok=True)
    mgdf.drop(columns=["kozeppont"]).to_file(TEST_GPKG, driver="GPKG", layer="epuletek")

    # 7) szűrés és középpont-export geokódoláshoz
    mgdf = mgdf[megmaradt_mask]
    print(f"Lekérdezendő poligonok száma: {len(mgdf)}")

    kozeppont_lista = list(zip(mgdf["kozeppont"].y, mgdf["kozeppont"].x))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(kozeppont_lista, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    ms_building_feldolgozas()
