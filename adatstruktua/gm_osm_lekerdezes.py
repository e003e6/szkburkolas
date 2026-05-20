import json
import geopandas as gpd


def gm_osm_lekerdezes():

    # üres osm épület poligonok és az ismert címes pontok beolvasása
    epuletek = gpd.read_parquet('../data/work_data/osm_ures_epulet.parquet').to_crs(epsg=4326)
    merged = gpd.read_parquet('../data/work_data/osm_gmaps_merged.parquet').to_crs(epsg=4326)

    print(f"Üres OSM épület-poligonok: {len(epuletek)} db")
    print(f"Ismert címes pontok (osm+gmaps): {len(merged)} db")

    # spatial join: melyik üres poligonba esik bele legalább egy ismert címes pont
    pontok_geom = merged[['geometry']]
    talalatok = gpd.sjoin(
        epuletek,
        pontok_geom,
        how='inner',
        predicate='contains',
    )

    # ezeket a poligonokat dobjuk — már le vannak fedve ismert címmel
    talalt_idx = talalatok.index.unique()
    maradek = epuletek.drop(index=talalt_idx).reset_index(drop=True)

    print(f"Tartalmaz már ismert címes pontot:       {len(talalt_idx)} db")
    print(f"Még lekérdezendő épület-poligon:         {len(maradek)} db")

    # centroid pontos számítása EOV-ban, vissza WGS84-be
    centroidok = maradek.geometry.to_crs(epsg=23700).centroid.to_crs(epsg=4326)

    # [lat, lon] lista — ugyanaz a formátum mint ms_building_feldolgozas
    kozeppont_lista = list(zip(centroidok.y, centroidok.x))

    with open("../data/out_data/lekerdezendo_kordinatak_maradek.json", "w", encoding="utf-8") as f:
        json.dump(kozeppont_lista, f, ensure_ascii=False, indent=4)

    print(f"Exportálva: ../data/out_data/lekerdezendo_kordinatak_osm.json ({len(kozeppont_lista)} koordináta)")


if __name__ == '__main__':
    gm_osm_lekerdezes()
