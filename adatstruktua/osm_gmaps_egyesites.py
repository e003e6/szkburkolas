# az osm és a google maps címeket egyesíti egy közös DataFrame-be
#    1. közös oszlopnévre hozza a két datasetet
#    2. egyező címek (city + postcode + street + housenumber) kiszűrése a gmaps oldalról
#    3. egyesített parquet export + színezett gpkg vizualizációhoz

import pandas as pd
import geopandas as gpd


def osm_gmaps_egyesites():

    osm = gpd.read_parquet('../data/work_data/osm_cim_kordinata.parquet')
    gmaps = gpd.read_parquet('../data/work_data/teljes_ms_google_tisztott.parquet')

    print("egyedi értékek száma:", gmaps["orszag"].nunique(dropna=False))
    print(gmaps["orszag"].value_counts(dropna=False))

    # gmaps ország cella kidobása de előtte minden nem Hungary sor törlése
    gmaps = gmaps[gmaps["orszag"] == "Hungary"].copy()
    gmaps = gmaps.drop(columns=["orszag"])
    print("gmaps Hungary szűrés után:", len(gmaps))

    # id oszlopok törlése (egy közös id lesz majd)
    osm = osm.drop(columns=["id"])
    gmaps = gmaps.drop(columns=["gid"])

    # oszlopok közös névre hozása
    gmaps = gmaps.rename(columns={
        "telepules": "city",
        "iszam": "postcode",
        "utca": "street",
        "cim": "housenumber",
    })

    osm = osm.rename(columns={
        "addr:city": "city",
        "addr:postcode": "postcode",
        "addr:street": "street",
        "addr:housenumber": "housenumber",
    })

    # rögzítés hogy melyik datasetből jön a sor
    osm["source"] = "osm"
    gmaps["source"] = "gmaps"

    match_cols = ["city", "postcode", "street", "housenumber"]

    for df in (osm, gmaps):
        df["_key"] = (
            df[match_cols]
            .astype(str)
            .apply(lambda s: s.str.lower().str.replace(r"[\s/\-.]+", "", regex=True))
            .agg("|".join, axis=1)
        )

    # gmaps-ból ki dobjuk azokat amik már osm-ben vannak
    osm_keys = set(osm["_key"])
    gmaps_new = gmaps[~gmaps["_key"].isin(osm_keys)].copy()
    print("gmaps eredeti:", len(gmaps), "| duplikátumok OSM-mel:", len(gmaps) - len(gmaps_new))

    # Egyesítés és takarítás
    merged = pd.concat([osm, gmaps_new], ignore_index=True)
    merged = merged.drop(columns=["_key"])
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=osm.crs)

    print("egyesített:", len(merged))

    # minden oszlop értékének konvertálása azonos típusra export előtt
    for col in ["city", "postcode", "street", "housenumber"]:
        merged[col] = merged[col].astype(str)

    # qgis export

    # színek megkülönböztetésre
    merged["color"] = merged["source"].map({"osm": "#d95f02", "gmaps": "#1b9e77"})

    # export
    merged.to_file("../data/test_data/osm_gmaps_merged.gpkg", driver="GPKG", layer="points")

    merged.to_parquet("../data/work_data/osm_gmaps_merged.parquet")


if __name__ == '__main__':
    osm_gmaps_egyesites()
