import re
import pandas as pd

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

