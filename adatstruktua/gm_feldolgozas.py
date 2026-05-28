import pandas as pd
import geopandas as gpd
import os
import glob
import json
from shapely.geometry import Point
from utca_nev_norm import *


# jsonl olvasó függvény
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


def jsonl_load(path):
    '''
    A Google-től lekérdezett adatokat rendezett, struktúrált formában adja vissza egy DF-ben
    '''
    
    writer = JsonlWriter(path)
    cimek = writer.read_all()

    print(len(cimek), 'cím beolvasva')

    # struktúrált feldolhozás
    adatok = []
    for cim in cimek:

        # ha a cím kevesebb mint 6 elemből áll akkor tuti None
        if len(cim) < 6:
            continue

        # ha pont 6 elemű a lekérdezés akkor minden jó
        elif len(cim) == 6:
            # ha nincsen házszám akkor kövi
            if not cim[1]:
                continue
            else:
                # print(len(cim), cim)
                tiszta = cim

        # cég vagy üzelethelyiség van a címen (biztos, hogy nincsen None)
        elif len(cim) == 7:
            idd = cim[0]
            utca = cim[3]
            varos = cim[2]
            tobbi = cim[-3:]
            # print(len(cim), [idd, utca, varos, *tobbi])
            tiszta = [idd, utca, varos, *tobbi]

        elif len(cim) > 8:
            idd = cim[0]
            hasznos = cim[-5:]
            rend = [idd, hasznos[1], hasznos[0], *hasznos[2:]]

            # ha a cím sor tartalmaz számokat és betűket is akkor jó esélyel nem hibás az adatsor
            s = rend[1]
            if any(c.isalpha() for c in s) and any(c.isdigit() for c in s):
                # print(len(cim), '\t', rend)
                tiszta = rend

        # itt még lehet feltételeket adni az adatosornak szűréshez

        # az 1-es oszlopnak szintén tartalmaznia kell számkat és betűket is mert utca + házszám
        s = tiszta[1]
        if not (any(c.isalpha() for c in s) and any(c.isdigit() for c in s)):
            continue

        # 3-as oszlopnak tartalmaznia kell számokat és betűket is mert irányitó szám + ország
        s = tiszta[3]
        if not (any(c.isalpha() for c in s) and any(c.isdigit() for c in s)):
            continue

        # 3-as oszlopot tudonom kell pontosan ketté osztani irányító szám és ország
        reszek = tiszta[3].split()
        tiszta = tiszta[:3] + reszek + tiszta[4:]

        # ha nem 7 elemű listát kaptam és ha a második tag nem Magyarország akkor baj van
        if len(tiszta) != 7 and reszek[1] != 'Hungary':
            continue


        adatok.append(tiszta)


    print(len(adatok), 'használható cím átadva', f'ez a címek {round((100*len(adatok))/len(cimek), 2)}%-a')

    # df alap beállítások kezelések

    df = pd.DataFrame(adatok)
    # ozslopok átnevezése
    df = df.rename(columns={0: "gid", 1: 'cim', 2: 'telepules', 3: 'iszam', 4: 'orszag', 5: 'lat', 6: 'lon'})

    # kiszűröm azokat a sorkat ahol az irányítószám nem alakítható int-é mert hibás adat van benne
    mask = pd.to_numeric(df['iszam'], errors='coerce').notna()
    df = df[mask]

    # megadom az adatípusokat
    df = df.astype({'gid':int, 'cim':str, 'telepules':str, 'iszam':int, 'orszag':str, 'lat':float, "lon": float})

    return df



def gm_feldolgozas(jsonl_mappa='../data/nyers_data/google_lekredezesk'):

    # 1. google maps lekérdezések mappájának validálása + összes jsonl megkeresése
    if not os.path.isdir(jsonl_mappa):
        raise FileNotFoundError(
            f'Nem található a google lekérdezések mappája: {jsonl_mappa} — '
            f'a gm_feldolgozas leáll.'
        )

    jsonl_fajlok = sorted(glob.glob(os.path.join(jsonl_mappa, '*.jsonl')))
    if not jsonl_fajlok:
        raise FileNotFoundError(
            f'Nincs feldolgozható .jsonl fájl a mappában: {jsonl_mappa} — '
            f'a gm_feldolgozas leáll.'
        )

    print(f'{len(jsonl_fajlok)} db jsonl fájl beolvasásra vár a(z) "{jsonl_mappa}" mappából:')
    for p in jsonl_fajlok:
        print(f'  - {os.path.basename(p)}')


    # 2. minden jsonl beolvasása a változatlan jsonl_load-dal, forras cimkével együtt
    reszek = []
    for path in jsonl_fajlok:
        forras = os.path.splitext(os.path.basename(path))[0]
        print(f'\n>>> {forras}')
        df_resz = jsonl_load(path)
        df_resz['forras'] = forras
        reszek.append(df_resz)

    df = pd.concat(reszek, ignore_index=True)
    print(f'\nÖsszesített nyers DF: {len(df)} sor, {df["forras"].nunique()} forrásból')


    # 3. címek alapján utca név és házszám szétválasztása

    # tisztítás előtte
    tmp = df["cim"].str.strip()

    # szétválsztás regex
    df[["utca", "cim"]] = tmp.str.extract(r"^(.*?)(\d.*)$", expand=True)

    # tisztítás utánna
    df["utca"] = df["utca"].str.strip()
    df["cim"] = df["cim"].str.strip()

    # oszlopok rendezése (forras a végén)
    df = df[['gid', 'utca', 'cim', 'telepules', 'iszam', 'orszag', 'lat', 'lon', 'forras']]


    # 4. közterület nevek egységesítése
    df["utca"] = utca_normalizalas(df["utca"])


    # 5. házszámok egységesítése
    df = cim_standardizalas(df)

    print('Google maps lekérdezés adatok standardizálva')


    # 6. geoDataFrame létrehozása a kordinátákkal
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")
    gdf = gdf.drop(columns=["lat", "lon"])

    # parquet export (változatlan név és hely — a downstream lépések ezt olvassák)
    gdf.to_parquet('../data/work_data/teljes_ms_google_tisztott.parquet')


    # 7. vizualizációs réteg: forras szerint színezett gpkg QGIS-hez
    SZINEK = ['#1b9e77', '#d95f02', '#7570b3', '#e7298a',
              '#66a61e', '#e6ab02', '#a6761d', '#666666']

    forras_kulcsok = sorted(gdf['forras'].unique())
    szin_map = {f: SZINEK[i % len(SZINEK)] for i, f in enumerate(forras_kulcsok)}
    gdf['color'] = gdf['forras'].map(szin_map)

    print('\nForrás → szín hozzárendelés:')
    for f in forras_kulcsok:
        print(f'  {f}: {szin_map[f]}  ({(gdf["forras"] == f).sum()} pont)')

    gpkg_path = '../data/test_data/gm_feldolgozas_forras_cimkezett.gpkg'
    gdf.to_file(gpkg_path, driver='GPKG', layer='gm_pontok')
    print(f'\nVizualizációs gpkg kiírva: {gpkg_path}')


if __name__ == '__main__':
    gm_feldolgozas()
