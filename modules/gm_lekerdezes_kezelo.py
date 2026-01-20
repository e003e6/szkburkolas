import json
import os

import pandas as pd
import geopandas as gpd
import osmnx as ox
import numpy as np


'''
Ez a kód egy gyűjtemény minden olyan függvénynek, ami a google lekérdezés olvasáshoz és feldolgozásához szükséges

Maga a lekérdézeés megtaláható egy másik repooban: https://github.com/e003e6/gmapslekerdezes.git
'''



'''
josnl olvasásához és írásához szükséges osztály
'''
class JsonlWriter:
    def __init__(self, path):
        self.path = path
        open(self.path, "a", encoding="utf-8").close()

    def write(self, record: dict):
        line = json.dumps(record, ensure_ascii=False)
        # data lemezre
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read_all(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]




'''
Adatok beolvásása standardizált módon
'''
def read_jsonl_to_df(path='./orszagos_teljes_ms.jsonl'):

    # adatok beolvasása
    writer = JsonlWriter(path)

    # df létrehozása
    df = pd.DataFrame(writer.read_all())

    print(len(df), 'lekérdezett cím beolvasva')

    # itt fogom feldolhozni a beolvasott nyers json sorokat

    df = df.rename(columns={
        0: "geoid",
        1: 'cim',
        2: 'telepules',
        5: 'lat',
        6: 'lon',

    })

    df_jav = df.apply(fix_lat_lon, axis=1)

    df_jav = df_jav[['geoid', 'cim', 'telepules', 'lat', 'lon']]

    return df_jav






'''
Nagyon első verziós chatGPT kód az elcsúszott kordináta sorok javítására.

A lekérdezéskor nem fix hosszúságú listát kapunk vissza, ha több atribútom (pl. cégnév) tartaozik egy címhez akkor
hosszabb listát kapunk vissza így beolvasásá után nem mindig ugyan abba a sorba esnek a lon és lat adatok
'''
def fix_lat_lon(row):
    # minden érték numerikussá kényszerítve (ami nem szám, az NaN lesz)
    coords = pd.to_numeric(row, errors='coerce')

    # jelenlegi lat/lon numerikus formában (ha nincs, akkor NaN)
    lat_cur = coords.get('lat', np.nan)
    lon_cur = coords.get('lon', np.nan)

    # ha már most is jó (Magyarország tartomány), akkor nem piszkáljuk
    if 45 <= lat_cur <= 49 and 16 <= lon_cur <= 23:
        return row

    # jelöltek keresése a sorban
    lat_candidates = coords[(coords >= 45) & (coords <= 49)]
    lon_candidates = coords[(coords >= 16) & (coords <= 23)]

    # új lat kiválasztása
    if 45 <= lat_cur <= 49:
        new_lat = lat_cur
    elif not lat_candidates.empty:
        new_lat = lat_candidates.iloc[0]
        lat_idx = lat_candidates.index[0]
    else:
        new_lat = np.nan
        lat_idx = None

    # lon-jelöltekből dobjuk ki azt az indexet, amit lat-nak már elhasználtunk
    if 'lat_idx' in locals() and lat_idx in lon_candidates.index:
        lon_candidates = lon_candidates.drop(lat_idx)

    # új lon kiválasztása
    if 16 <= lon_cur <= 23:
        new_lon = lon_cur
    elif not lon_candidates.empty:
        new_lon = lon_candidates.iloc[0]
    else:
        new_lon = np.nan

    # visszaírjuk a sorba
    row['lat'] = new_lat
    row['lon'] = new_lon
    return row







'''
Feldolgozza a gm lekérdezés adatok címeit
1. törli ahol nincsen cím
2. szétszedi az utcát és házszámot
3. törli ahol nincsen pontos cím
4. csak a szükséges adatokat adja vissza
'''
def cim_feldolgozas(df):

    # ahol nincsen utca név sem azt rögtön törlöm
    df = df[df['cim'].notna()].copy()

    # utca és házszám szétszedése
    pat = r'^(?P<utca>.*?)(?:\s+(?P<hazszam>\d+\S*))?$'
    df[["utca", "hazszam"]] = df['cim'].str.extract(pat)
    df["utca"] = df["utca"].str.strip()

    # ahol nincsen házszám azt törlöm
    df = df[df["hazszam"].notna()].copy()

    return df




'''
df-ből kiszedi azokat a sorokat, amik a megadott település poligonján belül vannak
'''
def filter_df_varos(df, varos_nev, lat_col='lat', lon_col='lon'):

    # település poligon lekérése
    place_gdf = ox.geocode_to_gdf(varos_nev)

    city_geom = place_gdf.to_crs(epsg=4326).geometry.union_all()

    # eldobjuk a koordináta nélküli sorokat
    df2 = df.dropna(subset=[lat_col, lon_col]).copy()

    # GeoDataFrame pontokkal
    geometry = gpd.points_from_xy(df2[lon_col], df2[lat_col], crs="EPSG:4326")
    gdf_points = gpd.GeoDataFrame(df2, geometry=geometry, crs="EPSG:4326")

    # pont a városon belül?
    mask = gdf_points.within(city_geom)

    return gdf_points[mask].drop(columns=['geometry'])






