'''
Letölti minden városra (data/nyers_data/varosok.txt) az OSM adatokat WGS84-ben:
  - varos_kozigazgatasi_hatar.parquet   (hivatalos közig. határ, geocode_to_gdf)
  - varos_lakottterulet_hatar.parquet   (lakott terület, landuse=residential)
  - utcak_nodes.parquet
  - utcak_edges.parquet

Hiba esetén printel és továbblép. Mindig mindent letölt elejéről.

Ez használjuk a burkolas_v3-ban

Valaint a Microsoft adatok szűrésére, is ennek a lakott területi határait használjuk,
hogy tudjuk, hogy hol nincsenek adatok. 
'''

import json
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd


DATA = Path(__file__).resolve().parent.parent / 'data' / 'nyers_data'

ox.settings.use_cache = True
ox.settings.requests_timeout = 180


def to_json_strings(df):
    # Az osmnx néhány oszlopában (osmid, highway, name, ...) ugyanaz a mező
    # egyik sorban scalar, másikban lista — pyarrow ezt nem tudja parquetbe írni.
    # Ahol legalább egy cellában container van, ott MINDEN nem-None értéket
    # JSON-stringgé alakítunk (scalar int → "123" is, hogy az oszlop egyértékű
    # string legyen). A beolvasó visszafejti.
    for col in df.columns:
        if col in ('geometry', 'telepules') or df[col].dtype != object:
            continue
        if any(isinstance(v, (list, tuple, dict, set)) for v in df[col]):
            df[col] = df[col].apply(
                lambda v: v if v is None or isinstance(v, str)
                else json.dumps(list(v) if isinstance(v, (set, tuple)) else v, default=str)
            )
    return df


def osm_letoltes():
    varosok = [v.strip() for v in (DATA / 'varosok.txt').read_text(encoding='utf-8').splitlines() if v.strip()]

    hatarok, lakott, nodes, edges = [], [], [], []
    hibak = []  # 'varos\tretek\túzenet' soronként

    def hiba(varos, retek, uzenet):
        sor = f'{varos}\t{retek}\t{uzenet}'
        print(f'  {retek} hiba ({varos}): {uzenet}')
        hibak.append(sor)

    for i, varos in enumerate(varosok, 1):
        print(f'[{i}/{len(varosok)}] {varos}')
        query = {'city': varos, 'country': 'Hungary'}

        try:
            place = ox.geocode_to_gdf(query)
            hatarok.append({'telepules': varos, 'geometry': place.geometry.iloc[0]})
        except Exception as ex:
            hiba(varos, 'határ', str(ex))

        try:
            res = ox.features_from_place(query, tags={'landuse': 'residential'})
            res = res[res.geometry.type.isin(['Polygon', 'MultiPolygon'])]
            if res.empty:
                hiba(varos, 'lakott', 'nincs landuse=residential')
            else:
                for g in res.geometry:
                    lakott.append({'telepules': varos, 'geometry': g})
        except Exception as ex:
            hiba(varos, 'lakott', str(ex))

        try:
            G = ox.graph_from_place(query, network_type='drive')
            n, e = ox.graph_to_gdfs(G, nodes=True, edges=True)
            n = n.reset_index()
            e = e.reset_index()
            n['telepules'] = varos
            e['telepules'] = varos
            nodes.append(n)
            edges.append(e)
        except Exception as ex:
            hiba(varos, 'utcahálózat', str(ex))

    gpd.GeoDataFrame(hatarok, geometry='geometry', crs='EPSG:4326').to_parquet(DATA / 'varos_kozigazgatasi_hatar.parquet')
    gpd.GeoDataFrame(lakott, geometry='geometry', crs='EPSG:4326').to_parquet(DATA / 'varos_lakottterulet_hatar.parquet')

    nodes_df = to_json_strings(pd.concat(nodes, ignore_index=True))
    gpd.GeoDataFrame(nodes_df, geometry='geometry', crs='EPSG:4326').to_parquet(DATA / 'utcak_nodes.parquet')

    edges_df = to_json_strings(pd.concat(edges, ignore_index=True))
    gpd.GeoDataFrame(edges_df, geometry='geometry', crs='EPSG:4326').to_parquet(DATA / 'utcak_edges.parquet')

    (DATA / 'osm_letoltes_hibak.txt').write_text('\n'.join(hibak) + ('\n' if hibak else ''), encoding='utf-8')


if __name__ == '__main__':
    osm_letoltes()
