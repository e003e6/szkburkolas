import pandas as pd
from unidecode import unidecode




def norm_utca(s):
    return (
        s.astype(str)
         # láthatatlan/nbspace kezelése
         .str.replace('\u00A0', ' ', regex=False)                          # NBSP → space
         .str.replace(r'[\u200B\u200C\u200D\u2060\uFEFF]', '', regex=True) # ZWSP, ZWNJ, ZWJ, WJ, BOM ki
         # whitespace normalizálás
         .str.replace(r'\s+', ' ', regex=True)                             # több space → 1
         # végződések egységesítése
         .str.replace(r'\bu\s*\.?\s*$', ' utca', case=False, regex=True)   # "u", "u.", "u .", stb. → " utca"
         .str.replace(r'\bút\.\s*$', ' út',  case=False, regex=True)       # "út." → "út"
         .str.replace(r'\but\.\s*$',  ' út', case=False, regex=True)       # "ut." → "út" (ha előfordul)
         # végső tisztítás
         .str.replace(r'\s+', ' ', regex=True).str.strip().str.lower()
    )

def norm_hazszam(s: pd.Series) -> pd.Series:
    return (s.astype(str)
              .str.strip()
              .str.replace(r'\.0$','', regex=True)    # 29.0 → 29
              .str.replace(r'\s+','', regex=True))    # szóköz ki




def import_cimjegyzek(cimjegyzek_path='../adatok/fix/df_22_selected.parquet'):

    # választási adatok importálása
    df_cimjegyzek = pd.read_parquet(cimjegyzek_path, engine="pyarrow")

    # duplikált címek törlése
    df_cimjegyzek = df_cimjegyzek.drop_duplicates()

    return df_cimjegyzek



'''
Összakapcsolja gm lekérdezéseket a hivatalos címjegyzékkel, egy egyszerű normalizálással
'''
def osszekapcs(df_gm_feldolgozott, df_cimjegyzek=None):

    if not df_cimjegyzek:
        df_cimjegyzek = import_cimjegyzek()

    # kulcsok létrehozása
    df_gm_feldolgozott.loc[:, "utca_key"] = norm_utca(df_gm_feldolgozott["utca"]).map(unidecode)
    df_gm_feldolgozott.loc[:, "hazszam_key"] = norm_hazszam(df_gm_feldolgozott["hazszam"])

    df_cimjegyzek.loc[:, "utca_key"] = norm_utca(df_cimjegyzek["kozteruletnev"]).map(unidecode)
    df_cimjegyzek.loc[:, "hazszam_key"] = norm_hazszam(df_cimjegyzek["utcaim_clean"])

    # összekapcsolás
    df_join = df_cimjegyzek.merge(
        df_gm_feldolgozott,
        left_on=["utca_key", "hazszam_key"],
        right_on=["utca_key", "hazszam_key"],
        how="inner",
        suffixes=("_szav", "_geo")
    )[['geoid', 'szavazokorid', 'telepulesnev', 'utca', 'hazszam', 'lon', 'lat']]

    return df_join


