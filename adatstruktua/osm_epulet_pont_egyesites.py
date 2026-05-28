# a fájl feldolozza az overpass letöltött adatokat
#    1. összevonja a pontokat és az épületeket megkapok minden ponpontos címes kordinátát
#    2. kiválogatja a hiányzó házszámú épület-poligonokat

import os
import geopandas as gpd
import pandas as pd
import gc


def overpass_feldolgozas():
    path = '../data/nyers_data/overpass_download'


    # Pontok

    # beolvasom a több geojosn-fájlt mindegyiket egy külön df-be
    pontok_df = [
        gpd.read_file(os.path.join(path, 'pontok', f))
        for f in os.listdir(os.path.join(path, 'pontok'))
        if f.endswith('.geojson')
    ]

    # 1. egyesitem a sok kis df-et
    # 2. overpass letöltési hibákból származó duplikált elemek törtéle (csak az első elem marad meg)
    pontok = (
        gpd.GeoDataFrame(pd.concat(pontok_df, ignore_index=True), crs=pontok_df[0].crs)
        .drop_duplicates(subset='id')
        .reset_index(drop=True)
    )

    # memória felszabítás (kissebb df-ek törlése memóriából)
    del pontok_df
    gc.collect()

    print(f"Pontok beolvasva: {len(pontok)} db")

    megtartott_oszlopok = [
        'id',
        'geometry',
        'addr:city',
        'addr:postcode',
        'addr:street',
        'addr:housenumber',
    ]
    pontok = pontok[megtartott_oszlopok]
    pontok = pontok[pontok['addr:housenumber'].notna()].reset_index(drop=True)

    print(f"Pontok házszámmal: {len(pontok)} db")


    # Épületek

    megtartott_oszlopok = [
        'id',
        'geometry',
        'addr:city',
        'addr:postcode',
        'addr:street',
        'addr:housenumber',
        'addr:place',                # utca helyett kis falvakban
        'addr:conscriptionnumber',   # HRSZ
    ]

    epuletek_df = []
    epuletek_poligonok_df = []        # szűrt poligon-verzió (centroid előtt) későbbi felhasználásra
    epuletek_poligonok_osszes_df = [] # szűretlen — minden épület-poligon, csak vizualizációhoz
    for f in os.listdir(os.path.join(path, 'epuletek')):
        if not f.endswith('.geojson'):
            continue

        gdf = gpd.read_file(os.path.join(path, 'epuletek', f))

        # vizualizációs verzió: minden poligon, szűrés nélkül
        epuletek_poligonok_osszes_df.append(
            gdf[gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])][['id', 'geometry']].copy()
        )

        # sorok szűrése: legyen housenumber VAGY place VAGY conscriptionnumber
        gdf = gdf[
            gdf['addr:housenumber'].notna()
            | gdf['addr:place'].notna()
            | gdf['addr:conscriptionnumber'].notna()
        ]

        # oszlopok szűkítése
        gdf = gdf[[c for c in megtartott_oszlopok if c in gdf.columns]]

        # poligon verzió eltárolása (csak Polygon / MultiPolygon)
        epuletek_poligonok_df.append(
            gdf[gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
        )

        # poligon -> centroid (EOV-ban vetítve a pontosság miatt, vissza WGS84-be)
        eredeti_crs = gdf.crs
        gdf['geometry'] = gdf.geometry.to_crs(epsg=23700).centroid.to_crs(eredeti_crs)

        epuletek_df.append(gdf)

    # egyesítés és duplikáltak törlése (centroid)
    epuletek = (
        gpd.GeoDataFrame(pd.concat(epuletek_df, ignore_index=True), crs=epuletek_df[0].crs)
        .drop_duplicates(subset='id')
        .reset_index(drop=True)
    )

    # egyesítés és duplikáltak törlése (poligon)
    epuletek_poligonok = (
        gpd.GeoDataFrame(pd.concat(epuletek_poligonok_df, ignore_index=True), crs=epuletek_poligonok_df[0].crs)
        .drop_duplicates(subset='id')
        .reset_index(drop=True)
    )

    # szűretlen poligon-verzió (vizualizációhoz)
    epuletek_poligonok_osszes = (
        gpd.GeoDataFrame(
            pd.concat(epuletek_poligonok_osszes_df, ignore_index=True),
            crs=epuletek_poligonok_osszes_df[0].crs,
        )
        .drop_duplicates(subset='id')
        .reset_index(drop=True)
    )

    del epuletek_df, epuletek_poligonok_df, epuletek_poligonok_osszes_df
    gc.collect()

    print(f"Épületek beolvasva: {len(epuletek)} db")

    print("Csak place (housenumber és conscriptionnumber nélkül):",
          ((epuletek['addr:housenumber'].isna())
           & (epuletek['addr:place'].notna())
           & (epuletek['addr:conscriptionnumber'].isna())).sum())

    print("Csak conscriptionnumber (housenumber és place nélkül):",
          ((epuletek['addr:housenumber'].isna())
           & (epuletek['addr:place'].isna())
           & (epuletek['addr:conscriptionnumber'].notna())).sum())

    print("Place ÉS conscriptionnumber (housenumber nélkül):",
          ((epuletek['addr:housenumber'].isna())
           & (epuletek['addr:place'].notna())
           & (epuletek['addr:conscriptionnumber'].notna())).sum())

    # csak housenumber marad, place és conscriptionnumber oszlop törlése
    epuletek = epuletek.drop(columns=['addr:place', 'addr:conscriptionnumber'])

    # sorok szűrése: csak housenumber-esek maradnak
    epuletek = epuletek[epuletek['addr:housenumber'].notna()].reset_index(drop=True)

    print(f"Épületek házszámmal: {len(epuletek)} db")


    # Egyesítés

    osm = gpd.GeoDataFrame(
        pd.concat([pontok, epuletek], ignore_index=True),
        crs=pontok.crs
    )

    # törlés memóriából
    del pontok, epuletek
    gc.collect()

    print(f"Egyesítés előtt: {len(osm)} sor")

    # duplikátum-szűrés postcode + street + housenumber alapján
    # (ha ezek megegyeznek, ugyanaz a cím — akár pontként, akár épületként szerepelt az OSM-ben)
    osm = osm.drop_duplicates(
        subset=['addr:postcode', 'addr:street', 'addr:housenumber'],
        keep='first'
    ).reset_index(drop=True)

    print(f"Egyesítés után: {len(osm)} sor")

    osm.to_parquet('../data/work_data/osm_cim_kordinata.parquet')


    # Üres (cím nélküli) épület-poligonok

    # Üres = NINCS housenumber ÉS NINCS place ÉS NINCS conscriptionnumber.
    # Az `epuletek_poligonok` pont a (housenumber | place | conscriptionnumber)
    # valamelyikével rendelkező épületeket tartalmazza, így a teljes,
    # szűretlen halmazból (`epuletek_poligonok_osszes`) ezeket kivonva
    # maradnak a semmilyen címmel nem rendelkező poligonok.
    cimes_idk = set(epuletek_poligonok['id'])
    epuletek_hianyos = epuletek_poligonok_osszes[
        ~epuletek_poligonok_osszes['id'].isin(cimes_idk)
    ].reset_index(drop=True)

    print(f"Cím nélküli épület-poligonok: {len(epuletek_hianyos)} db")

    # csak azok a hiányos poligonok maradnak, amikbe nem esik bele egyetlen címes osm pont sem
    # (a `osm` GeoDataFrame minden eleme címes — pontok + épület-centroidok házszámmal)

    # CRS egyeztetés (biztos ami biztos)
    pontok_geom = osm[['geometry']].to_crs(epuletek_hianyos.crs)

    # spatial join: melyik hiányos poligon tartalmaz legalább egy címes pontot
    talalatok = gpd.sjoin(
        epuletek_hianyos,
        pontok_geom,
        how='inner',
        predicate='contains',
    )

    # ezeket a poligonokat dobjuk
    talalt_idx = talalatok.index.unique()
    epuletek_hianyos_szurt = epuletek_hianyos.drop(index=talalt_idx).reset_index(drop=True)

    print(f"Hiányos poligon összesen:                {len(epuletek_hianyos)} db")
    print(f"Tartalmaz legalább egy címes pontot:     {len(talalt_idx)} db")
    print(f"Marad (nincs benne egy pont sem):        {len(epuletek_hianyos_szurt)} db")

    epuletek_hianyos_szurt.to_parquet('../data/work_data/osm_ures_epulet.parquet')


    # Minden OSM épület-poligon (cím nélküliek is) — geopandas parquet

    print(f"Minden OSM épület-poligon: {len(epuletek_poligonok_osszes)} db")
    epuletek_poligonok_osszes.to_parquet('../data/work_data/osm_minden_epulet.parquet')


    # Vizualizáció - QGIS export (minden OSM épület-poligon, cím nélküliek is)

    poly_viz = epuletek_poligonok_osszes.copy()
    for col in poly_viz.columns:
        if col != 'geometry':
            poly_viz[col] = poly_viz[col].astype(str)

    print(f"QGIS export poligonok száma: {len(poly_viz)} db")

    poly_viz.to_file(
        '../data/test_data/osm_epulet_poligonok.gpkg',
        driver='GPKG',
        layer='poligonok',
    )


if __name__ == '__main__':
    overpass_feldolgozas()
