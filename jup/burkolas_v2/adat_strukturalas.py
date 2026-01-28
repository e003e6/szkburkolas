import pandas as pd
import geopandas as gpd
import re
import pickle
from shapely.geometry import Point

from gm_rendezes import jsonl_load



def utca_normalizalas(s):
    '''
    Az közterület rövidítéseket eltüneti
    '''

    kozter_map = {
        r"\bu\.?\b": "utca",
        r"\bkrt\.?\b": "körút",
        r"\bstny\.?\b": "sétány",
        r"\brkp\.?\b": "rakpart",
        r"\bfs\.?\b": "fasor",
        r"\bsgt\.?\b": "sugárút",
        r"\bltp\.?\b": "lakótelep",
        r"\budv\.?\b": "udvar",
        r"\bhrsz\b": "helyrajzi szám",
        r"\bst\.?\b": "utca",
        r"\brd\.?\b": "út",
        r"\bave\.?\b": "sugárút",
        r"\bblvd\.?\b": "körút",
    }

    for pattern, repl in kozter_map.items():
        s = s.str.replace(pattern, repl, regex=True, flags=re.IGNORECASE)

    s = s.str.replace(r"\s+", " ", regex=True).str.strip().str.rstrip(".")

    return s



def cim_standardizalas(df):
    '''
    1. Ahol a cim tartalmazza a 'hrsz' részt ott az utca oszlop kapja meg a cim oszlop értékét és a cim legyen None
    2. Ahol az utca oszlop None (üres) ott a kapja meg a cim oszlop értékét és a cim legyen None
    3. standardizálni kell a cim megjelenítéseket:
        3.1 az 'épület' és hasonló szövegrészeket el kell tüntetni: pl. 31-B épület -> 31-B
        3.2. a / karakter legyen mindig szóközre cserélve: pl. 21/A -> 21 A
        3.3. a - karakter csak számok között maradhat (112-114 jó), házszám és épület között legyen szóközre cserélve: pl. 31-B -> 31 B
        3.4. a szám utáni betű legyen szóközzel elválasztva: pl. 10a -> 10 a
        3.5 a betűk legyen mindig nagyok: pl. 10 a -> 10 A
    '''

    # 1) 'hrsz' a cim-ben -> utca=cim, cim=None
    m_hrsz = df["cim"].str.contains(r"\bhrsz\b", case=False, na=False)
    df.loc[m_hrsz, "utca"] = df.loc[m_hrsz, "cim"]
    df.loc[m_hrsz, "cim"] = pd.NA

    # 2) ahol az utca üres/None -> utca=cim, cim=None
    m_utca_ures = df["utca"].isna() | df["utca"].str.strip().eq("")
    m_cim_van = df["cim"].notna() & df["cim"].str.strip().ne("")
    m_move = m_utca_ures & m_cim_van

    df.loc[m_move, "utca"] = df.loc[m_move, "cim"]
    df.loc[m_move, "cim"] = pd.NA

    # 3) cim standardizálás (csak ahol van cim)
    m = df["cim"].notna() & df["cim"].str.strip().ne("")
    s = df.loc[m, "cim"].str.strip()

    # 3.1 "épület" és hasonló részek levágása (a kulcsszótól a sor végéig)
    s = s.str.replace(
        r"\s*(épület|epulet|l[eé]pcs[őo]h[áa]z|lph\.?|lh\.?|emelet|ajt[óo]|szint|building)\b.*$", "", regex=True,
        flags=re.IGNORECASE)

    # 3.2 / -> szóköz
    s = s.str.replace("/", " ", regex=False)

    # 3.3 - csak számok között maradhat; minden más kötőjel -> szóköz (112-114 marad, 31-B -> 31 B)
    s = s.str.replace(r"(?<!\d)-|-(?!\d)", " ", regex=True)

    # 3.4 szám utáni betű közé szóköz (10a -> 10 a)
    s = s.str.replace(r"(\d)([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű])", r"\1 \2", regex=True)

    # extra: több szóköz összehúzás, szélek vágása
    s = s.str.replace(r"\s+", " ", regex=True).str.strip()

    # 3.5 betűk nagybetűsek
    s = s.str.upper()

    df.loc[m, "cim"] = s

    return df





def gm_feldolgozas(jsonl_path):

    # 1. google maps lekérdezések beolvasása
    df = jsonl_load(jsonl_path)


    # 2. címek alapján utca név és házszám szétválasztása

    # tisztítás előtte
    tmp = df["cim"].str.strip()

    # szétválsztás regex
    df[["utca", "cim"]] = tmp.str.extract(r"^(.*?)(\d.*)$", expand=True)

    # tisztítás utánna
    df["utca"] = df["utca"].str.strip()
    df["cim"] = df["cim"].str.strip()

    # oszlopok rendezése
    df = df[['gid', 'utca', 'cim', 'telepules', 'iszam', 'orszag', 'lat', 'lon']]


    # 3. közterület nevek egységesítése
    df["utca"] = utca_normalizalas(df["utca"])


    # 4. házszámok egységesítése
    df = cim_standardizalas(df)

    print('Google maps lekérdezés adatok standardizálva')


    # 5. geoDataFrame létrehozása a kordinátákkal
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")
    gdf = gdf.drop(columns=["lat", "lon"])

    # exportálás
    #gdf.to_file('../../adatok/working/orszagos_valid_kordinatak.gpkg', layer='network_polygons', driver='GPKG')

    return gdf



def db_feldolgozas(csv_path):
    '''
    Felglgozza az adatbázisból lementett címjegyzéket és visszad egy standardizált df-et

    Használat:
    df = db_feldolgozas('../../adatok/fix/2006_tol_cimek.csv')
    '''

    df = pd.read_csv(csv_path,
                     dtype={'szavazokorid': int, 'kozteruletid': 'Int64', 'kozteruletnevid': int, 'kozteruletnev': str,
                            'utcacim': str, 'telepulesid': 'Int64', 'telepulesnev': str, 'eventfromid': int, 'date': str})

    print('CSV beolvasva')

    # a None id értékű sorokat törlöm
    df = df.dropna(subset=['telepulesid', 'kozteruletid'])

    # sok helyen van hogy duplikált sorok szerepelnek csak a település név más mert más nyelven van írva, ezeket törlöm
    df = df.drop_duplicates(
        subset=['szavazokorid', 'kozteruletid', 'kozteruletnevid', 'kozteruletnev', 'utcacim', 'telepulesid', 'date'])

    van_cim = ['2014-04-06', '2022-04-03']
    nincs_cim = ['2006-04-09', '2010-04-11', '2018-04-08']

    # most csak azokkal az adatokkal dolgozok ahol van cím, szűröm az adatbázist
    df = df[df['date'].isin(van_cim)]

    # azokat a sorokat ahol az utcacim 0 törlöm
    df = df[df['utcacim'] != '0']

    # a nem magyar teleülésneveket cserélem magyarra (OSM API lekérdezéssel)
    '''
    # lementem az unique városneveket hogy másik pyhotn fáljból le tudjam kérdezni
    u = df['telepulesnev'].dropna().astype(str).unique()

    with open('../../adatok/working/varosnevek_lekerdezni.pkl', 'wb') as f:
        pickle.dump(u, f)
    '''

    # beolvasom a lementett map szótárat
    with open('../../adatok/fix/varosnevek_hu_map.pkl', 'rb') as f:
        m = pickle.load(f)

    # mappolom a várhoz
    df['telepulesnev_hu'] = df['telepulesnev'].map(m)

    # ahol a telepulesnev_hu None oda vissza kerül a telepulesnev
    df["telepulesnev_hu"] = df["telepulesnev_hu"].fillna(df["telepulesnev"])

    print('városnév mappolás megtörtént')


    # rendberakom az utcacím oszlop értékeket (előkészítem a közös normalizáláshoz)

    # 1. kapcsos zárójelek és egyenlőségjelek el tüntése szóközzel
    df['utcacim'] = df['utcacim'].str.replace(r'[{}=]', ' ', regex=True)
    df['utcacim'] = df['utcacim'].str.replace(r'[<>]', ' ', regex=True)

    # 2. building és hasonló szavak eltüntetése
    df['utcacim'] = df['utcacim'].str.replace(
        r'\b(building|bldg\.?|block|blokk|épület|epulet)\b', ' ', regex=True, flags=re.IGNORECASE)

    # 3. feleleslegesen sokszorozott szóközök törlése
    df['utcacim'] = df['utcacim'].str.replace(r'\s+', ' ', regex=True).str.strip()

    # 4. felesleges nullák eltüntetése a számok elől
    df['utcacim'] = df['utcacim'].str.replace(r'\d+', lambda m: str(int(m.group(0))), regex=True)

    # szóköz rendbetétel, ha a nullázás után bármi hiba van
    df['utcacim'] = df['utcacim'].str.replace(r'\s+', ' ', regex=True).str.strip()

    # 1/B A  -> 1/B
    # 1/B B  -> 1/B
    df['utcacim'] = df['utcacim'].str.replace(r'(\b\d+/\w)\s+[A-Z]\b', r'\1', regex=True)

    # 10 A B -> 10 A
    df['utcacim'] = df['utcacim'].str.replace(r'(\b\d+\s+[A-Z])\s+[A-Z]\b', r'\1', regex=True)

    # 2-4D D -> 2-4D
    df['utcacim'] = df['utcacim'].str.replace(r'(\b\d+-\d+[A-Z])\s+[A-Z]\b', r'\1', regex=True)

    # most hogy eltüntetk az adathibák rárakom az univerzális standardizáló függvényt
    # ehhez át kell nevetni az oszlopokat -> utca es cim nevű oszlopk kellenek
    df = df.rename(columns={'kozteruletnev':'utca', 'utcacim':'cim'})
    df = cim_standardizalas(df)

    # standardizálom az utca neveket
    df["utca"] = utca_normalizalas(df["utca"])

    # oszlop sorrendek
    df = df[['szavazokorid', 'kozteruletid', 'kozteruletnevid', 'utca', 'cim', 'telepulesid', 'telepulesnev',
             'telepulesnev_hu', 'eventfromid', 'date']]

    # duplikált sorok törlése
    df = df.drop_duplicates()

    # exportálás
    #df.to_parquet('../../adatok/fix/2014_2022_adatbazis_cimek_feldolgozott.parquet', engine='pyarrow', index=False)

    return df














