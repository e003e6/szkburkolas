# Választási címjegyzék (attribútum) ↔ koordinátás pontkészlet (OSM+Gmaps) összekapcsolása.
#
# Bal oldal (anchor, koordinátás): osm_gmaps_merged.parquet  — minden sornak van geometriája.
# Jobb oldal (attribútum): nyers választási címjegyzék CSV   — szavazokör id + cím, geometria nélkül.
#   - 2014–2022-es formátum (vesszős CSV) → szkid_cim_kapcsolas()
#   - 2026-os formátum (TAB-os, irsz-kulcsos) → szkid_cim_kapcsolas_v2()
# Irány: a koordinátás pontok kapnak szavazokorid-ot. Egy választási címhez több pont is matchelhet.
#
# Mindkét függvénynek KÉT bemenete van: elect_csv (nyers címjegyzék) és out_path (kimenet
# útja+neve) — más futáshoz csak ezt a kettőt kell cserélni.
# Kimenet (<out_path>.gpkg/.parquet): a burkolas_v3 által módosítás nélkül olvasható séma:
#   szavazokorid (int), utca (str), hazszam (str), telepules (str), irsz (str), geometry (Point, EPSG:4326)
#   telepules: Budapesten kerületnév ("Budapest I. kerület" ... "Budapest XXIII. kerület",
#              pontosan a varosok.txt-beli alak — a burkolás erre a névre szűr).
#   irsz: az anchor-oldali normalizált irányítószám; a 2026-os úton mindig 4 jegyű (illesztő-kulcs),
#         a régi (2022) úton nem-budapesti pontnál lehet "" (ott nem illesztési kulcs).

import os
import re
import csv

import numpy as np
import pandas as pd
import geopandas as gpd
from unidecode import unidecode

from utca_nev_norm import utca_normalizalas

pd.set_option("future.no_silent_downcasting", True)


# ----------------------------------------------------------------------------------------
# Útvonalak
# ----------------------------------------------------------------------------------------
COORD_PATH = "../data/work_data/osm_gmaps_merged.parquet"
QA_DIR = "../data/work_data/qa"

# QA-ban használt metrikus vetület (EOV) a centroid-távolsághoz
METRIC_CRS = "EPSG:23700"
GEOM_OUTLIER_M = 500.0


# ========================================================================================
# A. NORMALIZÁLÓ RÉTEG  (mindkét oldalra ugyanazok a függvények)
# ========================================================================================

_EMPTY_TOKENS = {"", "n.a.", "na", "n/a", "nincs", "0", "-", "--", "none", "nan", "null"}

_BP_DISTRICT_RE = re.compile(r"^(?:[ivxlcdm]+|\d+)\.?\s*kerulet$")

_NOISE_RE = re.compile(
    r"\s*(épület|epulet|l[eé]pcs[őo]h[áa]z|lph\.?|lh\.?|emelet|em\.?|ajt[óo]|szint|fszt|földszint|building)\b.*$",
    re.IGNORECASE,
)
_SZ_SUFFIX_RE = re.compile(r"\b(sz[áa]m|sz\.?)\b", re.IGNORECASE)
_LEADING_ZERO_RE = re.compile(r"(?<!\d)0\d")
_RANGE_RE = re.compile(r"^\s*0*(\d+)\s*-\s*0*(\d+)")
_SINGLE_RE = re.compile(r"^\s*0*(\d+)\s*([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű]?)")
_ROMAN_RE = re.compile(r"^\s*([IVXLCDM]+)\s*$", re.IGNORECASE)
_LEFTOVER_RE = re.compile(r"[0-9A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű]")

_ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(s):
    s = s.upper()
    total, prev = 0, 0
    for ch in reversed(s):
        if ch not in _ROMAN_MAP:
            return None
        val = _ROMAN_MAP[ch]
        total += -val if val < prev else val
        prev = max(prev, val)
    return total


# Budapest 23 kerületének kanonikus neve — PONTOSAN a varosok.txt-beli alak
# (a burkolás oldala erre a névre szűr, az egyezést az önteszt is ellenőrzi).
_BP_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
             "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXII", "XXIII"]
BP_KERULET_NEV = {n: f"Budapest {r}. kerület" for n, r in enumerate(_BP_ROMAN, start=1)}


def kerulet_nev(n):
    """Kerületszám (1..23) → kanonikus kerületnév; minden másra (None/NA/0/24...) None."""
    try:
        return BP_KERULET_NEV.get(int(n))
    except (TypeError, ValueError):
        return None


# Várt formák (unidecode + lower után):
#   "budapest xvii. kerulet" / "xvii. kerulet" / "17. kerulet"  (2022-es alak + defenzív)
#   "budapest xvii" / "budapest 17"                             (2026-os telepnev, nincs utótag)
# Puszta "xvii" (se prefix, se utótag) szándékosan NEM kerület.
_KERULET_RE = re.compile(
    r"^(?:budapest\s+([ivxlcdm]+|\d+)\.?(?:\s*kerulet)?"
    r"|([ivxlcdm]+|\d+)\.?\s*kerulet)$"
)


def kerulet_from_telepules_nev(s):
    """Választási oldal telepules_nev-jéből Budapest-kerületszám (1..23) vagy None."""
    if s is None:
        return None
    t = unidecode(str(s)).lower().strip()
    t = re.sub(r"\s+", " ", t)
    m = _KERULET_RE.match(t)
    if not m:
        return None
    tok = m.group(1) or m.group(2)
    n = int(tok) if tok.isdigit() else _roman_to_int(tok)
    return n if (n is not None and 1 <= n <= 23) else None


def kerulet_from_postcode(s):
    """OSM/Gmaps oldal 4-jegyű 1ABC postcode-jából kerületszám (1..23) vagy None."""
    if s is None:
        return None
    t = str(s).strip()
    if len(t) != 4 or not t.isdigit() or t[0] != "1":
        return None
    n = int(t[1:3])
    return n if 1 <= n <= 23 else None


def irsz_norm(s):
    """Irányítószám kanonikus alak: pontosan 4 számjegy → string, különben "".
    Mindkét oldalon ugyanez (anchor postcode ↔ választási pir) az illesztés szimmetriájáért."""
    if s is None:
        return ""
    t = str(s).strip()
    return t if (len(t) == 4 and t.isdigit()) else ""


def telepules_fp(s):
    """Településnév kanonikus alak: lowercase + diakritika + whitespace-kollapszálás.
    Budapest minden kerülete egyetlen 'budapest' alá vonva."""
    if s is None:
        return ""
    t = unidecode(str(s)).lower().strip()
    t = re.sub(r"\s+", " ", t)
    if not t or t in _EMPTY_TOKENS:
        return ""
    if "budapest" in t or _BP_DISTRICT_RE.match(t):
        return "budapest"
    return t


def _utca_fp_one(x):
    """Egy normalizált utcanévből hézagmentes fingerprint (join-kulcs)."""
    if x is None:
        return ""
    return re.sub(r"[^0-9a-z]", "", unidecode(str(x)).lower())


def utca_fp_series(s):
    """utca_normalizalas (rövidítés-feloldás) → fingerprint. Series → Series."""
    s = utca_normalizalas(s.fillna("").astype(str))
    return s.map(_utca_fp_one)


def hazszam_parse(raw):
    """Házszám string → (lo, hi, letter, raw, flags).

    flags: had_leading_zeros, truncated, is_hrsz, empty_housenumber
    """
    flags = {
        "had_leading_zeros": False,
        "truncated": False,
        "is_hrsz": False,
        "empty_housenumber": False,
    }

    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        flags["empty_housenumber"] = True
        return (None, None, None, raw, flags)

    s = str(raw).strip()
    if s.lower() in _EMPTY_TOKENS:
        flags["empty_housenumber"] = True
        return (None, None, None, raw, flags)

    # HRSZ: külön számtér, nem matchelünk normál házszámként
    if re.search(r"\bhrsz\b", s, re.IGNORECASE) or "helyrajzi" in s.lower():
        flags["is_hrsz"] = True
        return (None, None, None, raw, flags)

    # zaj levágása (épület/lépcsőház/emelet...), majd sz./szám utótag
    s = _NOISE_RE.sub("", s)
    s = _SZ_SUFFIX_RE.sub("", s)
    s = s.replace("/", " ")

    if _LEADING_ZERO_RE.search(s):
        flags["had_leading_zeros"] = True

    # tartomány: szám - szám
    m = _RANGE_RE.match(s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        if _LEFTOVER_RE.search(s[m.end():]):
            flags["truncated"] = True
        return (lo, hi, None, raw, flags)

    # egyetlen szám + opcionális betű
    m = _SINGLE_RE.match(s)
    if m:
        num = int(m.group(1))
        letter = (m.group(2) or "").upper() or None
        if _LEFTOVER_RE.search(s[m.end():]):
            flags["truncated"] = True
        return (num, num, letter, raw, flags)

    # római szám házszámként (ritka)
    m = _ROMAN_RE.match(s)
    if m:
        val = _roman_to_int(m.group(1))
        if val is not None:
            return (val, val, None, raw, flags)

    flags["empty_housenumber"] = True
    return (None, None, None, raw, flags)


# ========================================================================================
# B. ADATBETÖLTÉS + NORMALIZÁLÁS
# ========================================================================================

def _hnum_columns(df, src_col):
    """Egyedi házszám-stringeken futtatja a parsert, majd visszamappeli (gyors)."""
    uniq = pd.Index(df[src_col].astype(object).unique())
    parsed = {v: hazszam_parse(v) for v in uniq}

    lo = df[src_col].map(lambda v: parsed[v][0])
    hi = df[src_col].map(lambda v: parsed[v][1])
    letter = df[src_col].map(lambda v: parsed[v][2])
    flags = df[src_col].map(lambda v: parsed[v][4])

    df["hnum_lo"] = lo
    df["hnum_hi"] = hi
    df["hnum_letter"] = letter
    df["hnum_letter_key"] = letter.map(lambda x: x if x else "")
    df["hnum_raw"] = df[src_col]
    df["had_leading_zeros"] = flags.map(lambda f: f["had_leading_zeros"])
    df["is_hrsz"] = flags.map(lambda f: f["is_hrsz"])
    df["empty_housenumber"] = flags.map(lambda f: f["empty_housenumber"])
    df["truncated"] = flags.map(lambda f: f["truncated"])
    return df


def _telepules_fp_col(series):
    uniq = pd.Index(series.astype(object).unique())
    m = {v: telepules_fp(v) for v in uniq}
    return series.map(m)


def betolt_koordinatas(path=COORD_PATH):
    """Koordinátás (anchor) oldal betöltése + egységes séma."""
    c = gpd.read_parquet(path)
    if c.crs is None:
        c = c.set_crs("EPSG:4326")
    c = c.to_crs("EPSG:4326")

    c = c.rename(columns={"street": "utca", "housenumber": "hazszam", "city": "telepules"})
    c["telepules_fp"] = _telepules_fp_col(c["telepules"])
    c["utca_fp"] = utca_fp_series(c["utca"])
    c["irsz"] = c["postcode"].map(irsz_norm)  # v2 (2026) illesztéshez; a régi út nem használja

    ker = c["postcode"].map(kerulet_from_postcode)
    bp_mask = (c["telepules_fp"] == "budapest")
    c["kerulet_fp"] = pd.Series(pd.NA, index=c.index, dtype="Int8")
    c.loc[bp_mask, "kerulet_fp"] = ker[bp_mask].astype("Int8")
    c.loc[~bp_mask, "kerulet_fp"] = pd.Series(0, index=c.index, dtype="Int8")[~bp_mask]

    bad_pc = bp_mask & ker.isna()
    print(f"  [koord] Budapest sorok: {int(bp_mask.sum())}, "
          f"ebből hibás postcode (nem deriválható kerület): {int(bad_pc.sum())}")
    if bad_pc.any():
        print(c.loc[bad_pc, "postcode"].value_counts().head(10).to_string())

    c = _hnum_columns(c, "hazszam")
    c = c.reset_index(drop=True)
    c["cid"] = np.arange(len(c), dtype=np.int64)
    return c


def betolt_valasztasi(path):
    """Választási (attribútum) oldal betöltése, normalizálás + dedup."""
    e = pd.read_csv(
        path,
        usecols=["szavazokor_id", "utca_nev", "hazszam", "telepules_nev"],
        dtype={"szavazokor_id": "int64", "utca_nev": "string",
               "hazszam": "string", "telepules_nev": "string"},
    )
    e["telepules_fp"] = _telepules_fp_col(e["telepules_nev"])
    e["utca_fp"] = utca_fp_series(e["utca_nev"])

    ker = e["telepules_nev"].map(kerulet_from_telepules_nev)
    bp_mask = (e["telepules_fp"] == "budapest")
    e["kerulet_fp"] = pd.Series(pd.NA, index=e.index, dtype="Int8")
    e.loc[bp_mask, "kerulet_fp"] = ker[bp_mask].astype("Int8")
    e.loc[~bp_mask, "kerulet_fp"] = pd.Series(0, index=e.index, dtype="Int8")[~bp_mask]

    bad_nev = bp_mask & ker.isna()
    print(f"  [valasztas] Budapest sorok: {int(bp_mask.sum())}, "
          f"ebből hibás/hiányzó kerület-név: {int(bad_nev.sum())}")
    if bad_nev.any():
        print(e.loc[bad_nev, "telepules_nev"].value_counts().head(10).to_string())

    e = _hnum_columns(e, "hazszam")

    # kimeneti településnév: Budapest → kanonikus kerületnév (varosok.txt-vel egyező alak);
    # NA-kerületes BP-sor "Budapest" marad (a _valid_mask a matchingből úgyis kizárja)
    e["telepules_out"] = np.where(
        bp_mask, e["kerulet_fp"].map(BP_KERULET_NEV).fillna("Budapest"), e["telepules_nev"]
    )

    # dedup az egyedi (szavazokör, fingerprint, kerület, házszám) kulcsra
    e = e.drop_duplicates(
        subset=["szavazokor_id", "telepules_fp", "kerulet_fp", "utca_fp",
                "hnum_lo", "hnum_hi", "hnum_letter_key"]
    ).reset_index(drop=True)
    return e


def betolt_valasztasi_v2(path):
    """2026-os választási címjegyzék betöltése + normalizálás + dedup.

    Eltérések a régi formátumtól (valasztas_cimek_2022.csv):
      - TAB-tagolt, idézőjel nélküli fájl (nem vesszős/idézett),
      - az utcanév két oszlopra bontva: kozteruletnev + kozteruletjelleg,
      - ÚJ: pir = irányítószám (irsz), ezt szigorú illesztő-kulcsként használjuk,
      - a szavazókör id a wardid oszlop.
    A fingerprint/házszám-normalizálás és a Budapest-egységesítés a régivel közös.
    """
    e = pd.read_csv(
        path,
        sep="\t",
        quoting=csv.QUOTE_NONE,
        dtype=str,
        keep_default_na=False,
        usecols=["telepnev", "pir", "kozteruletnev", "kozteruletjelleg", "hazszam", "wardid"],
    )

    e["szavazokor_id"] = e["wardid"].astype("int64")
    e["telepules_nev"] = e["telepnev"]
    e["utca_nev"] = (e["kozteruletnev"].str.strip() + " " + e["kozteruletjelleg"].str.strip()).str.strip()
    e["irsz"] = e["pir"].map(irsz_norm)

    e["telepules_fp"] = _telepules_fp_col(e["telepules_nev"])
    e["utca_fp"] = utca_fp_series(e["utca_nev"])

    # kerület: a telepnev az irányadó ("Budapest XII" — hiteles), a pir fallback +
    # keresztellenőrzés. A kerulet_fp adja a kimeneti kerületnevet (telepules_out)
    # és a QA kerület-bontását is.
    ker_nev = e["telepules_nev"].map(
        {v: kerulet_from_telepules_nev(v) for v in e["telepules_nev"].unique()})
    ker_pir = e["pir"].map(kerulet_from_postcode)
    ker = ker_nev.where(ker_nev.notna(), ker_pir)
    bp_mask = (e["telepules_fp"] == "budapest")
    mism = bp_mask & ker_nev.notna() & ker_pir.notna() & (ker_nev != ker_pir)
    print(f"  [valasztas 2026] Budapest sorok: {int(bp_mask.sum())}, "
          f"név-kerület nélkül: {int((bp_mask & ker_nev.isna()).sum())}, "
          f"telepnev↔pir kerület-eltérés: {int(mism.sum())}")
    if mism.any():
        print(e.loc[mism, ["telepules_nev", "pir"]].value_counts().head(10).to_string())
    e["kerulet_fp"] = pd.Series(pd.NA, index=e.index, dtype="Int8")
    e.loc[bp_mask, "kerulet_fp"] = ker[bp_mask].astype("Int8")
    e.loc[~bp_mask, "kerulet_fp"] = pd.Series(0, index=e.index, dtype="Int8")[~bp_mask]

    e = _hnum_columns(e, "hazszam")

    # kimeneti településnév: Budapest → kanonikus kerületnév (varosok.txt-vel egyező alak)
    e["telepules_out"] = np.where(
        bp_mask, e["kerulet_fp"].map(BP_KERULET_NEV).fillna("Budapest"), e["telepules_nev"]
    )

    # dedup az egyedi (szavazokör, fingerprint, irsz, házszám) kulcsra
    e = e.drop_duplicates(
        subset=["szavazokor_id", "telepules_fp", "irsz", "utca_fp",
                "hnum_lo", "hnum_hi", "hnum_letter_key"]
    ).reset_index(drop=True)
    return e


def _valid_mask(df, geo_keys=("telepules_fp", "kerulet_fp")):
    m = (
        (~df["empty_housenumber"])
        & (~df["is_hrsz"])
        & df["hnum_lo"].notna()
        & (df["utca_fp"] != "")
    )
    for k in geo_keys:
        if k == "kerulet_fp":
            m &= df["kerulet_fp"].notna()
        else:  # string-fingerprint (telepules_fp, irsz, ...): nem üres kell legyen
            m &= df[k].notna() & (df[k].astype(str) != "")
    return m


# ========================================================================================
# C. MATCHING KASZKÁD
# ========================================================================================

_CAND_COLS = ["cid", "eid", "szavazokor_id", "telepules_out"]


def _e_frame(e_valid, geo_keys=("telepules_fp", "kerulet_fp")):
    """Választási oldal join-frame egyértelmű oszlopnevekkel + sor-azonosító (eid)."""
    cols = ["eid", *geo_keys, "utca_fp",
            "hnum_lo", "hnum_hi", "hnum_letter_key",
            "szavazokor_id", "telepules_out"]
    E = e_valid[cols].rename(columns={
        "hnum_lo": "e_lo", "hnum_hi": "e_hi", "hnum_letter_key": "e_letter_key",
    }).astype({"e_lo": "int64", "e_hi": "int64"})
    return E


def _empty_cand():
    return pd.DataFrame(columns=_CAND_COLS)


def _resolve(cand, rule):
    """Több jelölt egy pontra → determinisztikus (legkisebb szavazokor_id), ambiguous flag."""
    if cand.empty:
        return pd.DataFrame(columns=["cid", "szavazokor_id", "telepules_out", "match_rule", "ambiguous"])
    amb = cand.groupby("cid")["szavazokor_id"].nunique() > 1
    best = (cand.sort_values("szavazokor_id")
                .drop_duplicates("cid", keep="first")
                .loc[:, ["cid", "szavazokor_id", "telepules_out"]]
                .copy())
    best["match_rule"] = rule
    best["ambiguous"] = best["cid"].map(amb).fillna(False).astype(bool).values
    return best


# A szabályok NYERS jelölteket adnak (cid ↔ eid párok). Ugyanaz a jelölthalmaz szolgálja
# a koordináta-hozzárendelést (cid-enként 1 szavazókör) ÉS a választási lefedettséget (mely
# választási sorokat ér el legalább 1 koordináta).

def _cand_exact(C, E, geo_keys=("telepules_fp", "kerulet_fp")):
    gk = list(geo_keys) + ["utca_fp"]
    m = C.merge(
        E,
        left_on=gk + ["hnum_lo", "hnum_hi", "hnum_letter_key"],
        right_on=gk + ["e_lo", "e_hi", "e_letter_key"],
        how="inner",
    )
    return m[_CAND_COLS]


def _cand_range_point(C, E, geo_keys=("telepules_fp", "kerulet_fp")):
    gk = list(geo_keys) + ["utca_fp"]
    rows = []
    cp = C[C["hnum_lo"] == C["hnum_hi"]]
    er = E[E["e_lo"] < E["e_hi"]]
    if len(cp) and len(er):
        m = cp.merge(er, on=gk, how="inner")
        ok = (m["e_lo"] <= m["hnum_lo"]) & (m["hnum_lo"] <= m["e_hi"]) & (m["hnum_letter_key"] == m["e_letter_key"])
        rows.append(m[ok][_CAND_COLS])
    # szimmetrikus: koordinátás tartomány ↔ választási pont (ritka)
    cr = C[C["hnum_lo"] < C["hnum_hi"]]
    ep = E[E["e_lo"] == E["e_hi"]]
    if len(cr) and len(ep):
        m = cr.merge(ep, on=gk, how="inner")
        ok = (m["hnum_lo"] <= m["e_lo"]) & (m["e_lo"] <= m["hnum_hi"]) & (m["hnum_letter_key"] == m["e_letter_key"])
        rows.append(m[ok][_CAND_COLS])
    return pd.concat(rows, ignore_index=True) if rows else _empty_cand()


def _cand_range_overlap(C, E, geo_keys=("telepules_fp", "kerulet_fp")):
    gk = list(geo_keys) + ["utca_fp"]
    cr = C[C["hnum_lo"] < C["hnum_hi"]]
    er = E[E["e_lo"] < E["e_hi"]]
    if not (len(cr) and len(er)):
        return _empty_cand()
    m = cr.merge(er, on=gk, how="inner")
    ok = (m[["hnum_lo", "e_lo"]].max(axis=1) <= m[["hnum_hi", "e_hi"]].min(axis=1)) \
        & (m["hnum_letter_key"] == m["e_letter_key"])
    return m[ok][_CAND_COLS]


def _cand_letter_collapse(C, E, geo_keys=("telepules_fp", "kerulet_fp")):
    gk = list(geo_keys) + ["utca_fp"]
    cp = C[C["hnum_lo"] == C["hnum_hi"]]
    ep = E[E["e_lo"] == E["e_hi"]]
    if not (len(cp) and len(ep)):
        return _empty_cand()
    m = cp.merge(ep, left_on=gk + ["hnum_lo"],
                 right_on=gk + ["e_lo"], how="inner")
    return m[_CAND_COLS]


def _cand_hnum_tolerance(C, E, tol, geo_keys=("telepules_fp", "kerulet_fp")):
    gk = list(geo_keys) + ["utca_fp"]
    cp = C[C["hnum_lo"] == C["hnum_hi"]]
    ep = E[E["e_lo"] == E["e_hi"]]
    if not (len(cp) and len(ep)):
        return _empty_cand()
    m = cp.merge(ep, on=gk, how="inner")
    diff = (m["hnum_lo"] - m["e_lo"]).abs()
    m = m[diff <= tol]
    return m[_CAND_COLS]


def cim_matcher(coord, elect, rules=("exact", "range_point", "range_overlap"),
                hnum_tolerance=2, cardinality="many_to_many",
                geo_keys=("telepules_fp", "kerulet_fp")):
    """Kétirányú matching kaszkád.

    geo_keys: a geo-diszkriminátor join-kulcsok az utca/házszám mellé. Default a régi
      (telepules_fp, kerulet_fp); a 2026-os v2 (telepules_fp, irsz) — szigorú irsz.

    Visszaad:
      matched          – koordinátás pontonként (cid) 1 szavazókör
      unmatched_cids   – nem matchelt koordinátás pontok
      matched_eids     – azon választási sorok (eid) halmaza, amelyeket legalább 1 koordináta elér
      e_valid          – a matchelhető (valid) választási sorok eid + telepules_out-tal
    """
    c_valid = coord[_valid_mask(coord, geo_keys)].copy()
    e_valid = elect[_valid_mask(elect, geo_keys)].copy()
    for col in ("hnum_lo", "hnum_hi"):
        c_valid[col] = c_valid[col].astype("int64")
    e_valid["eid"] = np.arange(len(e_valid), dtype=np.int64)

    E = _e_frame(e_valid, geo_keys)
    c_cols = ["cid", *geo_keys, "utca_fp",
              "hnum_lo", "hnum_hi", "hnum_letter_key"]
    C_full = c_valid[c_cols]

    dispatch = {
        "exact": lambda C: _cand_exact(C, E, geo_keys),
        "range_point": lambda C: _cand_range_point(C, E, geo_keys),
        "range_overlap": lambda C: _cand_range_overlap(C, E, geo_keys),
        "letter_collapse": lambda C: _cand_letter_collapse(C, E, geo_keys),
        "hnum_tolerance": lambda C: _cand_hnum_tolerance(C, E, hnum_tolerance, geo_keys),
    }

    matched_parts = []
    matched_eids = set()
    rem_cids = set(C_full["cid"].tolist())

    for rule in rules:
        if rule not in dispatch:
            continue
        cand = dispatch[rule](C_full)  # a TELJES valid koordinátakészleten — lefedettséghez

        # választási lefedettség: bármely koordináta eléri-e az adott választási sort
        if not cand.empty:
            matched_eids.update(cand["eid"].unique().tolist())

        # koordináta-hozzárendelés kaszkád: csak a még szabad pontokra
        cand_rem = cand[cand["cid"].isin(rem_cids)] if not cand.empty else cand
        best = _resolve(cand_rem, rule)
        if not best.empty:
            matched_parts.append(best)
            rem_cids.difference_update(best["cid"].tolist())
        print(f"  [{rule}] pont-match: {0 if best.empty else len(best):>8} | "
              f"hátralévő pont: {len(rem_cids):>8} | elért választási sor (kumulált): {len(matched_eids):>8}")

    matched = pd.concat(matched_parts, ignore_index=True) if matched_parts else \
        pd.DataFrame(columns=["cid", "szavazokor_id", "telepules_out", "match_rule", "ambiguous"])
    unmatched_cids = pd.Series(sorted(rem_cids), dtype="int64")
    return matched, unmatched_cids, matched_eids, e_valid


# ========================================================================================
# D. QA
# ========================================================================================

def _ensure_qa_dir():
    os.makedirs(QA_DIR, exist_ok=True)


def geom_outlier_flag(coord, matched):
    """Utcánkénti (telepules_fp, utca_fp) centroidtól >500m-re lévő matchek megjelölése."""
    cm = coord[["cid", "telepules_fp", "utca_fp", "geometry"]].merge(
        matched[["cid"]], on="cid", how="inner")
    if cm.empty:
        return pd.Series(dtype=bool)
    g = gpd.GeoDataFrame(cm, geometry="geometry", crs="EPSG:4326").to_crs(METRIC_CRS)
    cent = g.dissolve(by=["telepules_fp", "utca_fp"]).centroid
    g = g.merge(cent.rename("cent").reset_index(), on=["telepules_fp", "utca_fp"], how="left")
    dist = g.geometry.distance(gpd.GeoSeries(g["cent"], crs=METRIC_CRS))
    return pd.Series((dist > GEOM_OUTLIER_M).values, index=g["cid"].values)


def coverage_report(coord, matched, unmatched_cids):
    _ensure_qa_dir()
    base = coord[["cid", "telepules_fp", "telepules", "had_leading_zeros"]].copy()
    base = base.merge(matched[["cid", "match_rule"]], on="cid", how="left")
    base["match_rule"] = base["match_rule"].fillna("unmatched")

    # településenként + szabályonként
    cov = (base.groupby(["telepules", "match_rule"]).size()
               .unstack(fill_value=0).reset_index())
    cov.to_csv(os.path.join(QA_DIR, "coverage_telepules_rule.csv"), index=False)

    # leading-zero sáv
    lz = (base.groupby(["had_leading_zeros", "match_rule"]).size()
              .unstack(fill_value=0))
    lz.to_csv(os.path.join(QA_DIR, "coverage_leading_zero.csv"))
    return base


def match_rule_ratio(coord, matched):
    n = len(coord)
    counts = matched["match_rule"].value_counts()
    print("\n=== match-rule arányok (összes koordinátás ponthoz képest) ===")
    for rule, cnt in counts.items():
        print(f"  {rule:>16}: {cnt:>8}  ({100*cnt/n:5.1f}%)")
    matched_total = len(matched)
    print(f"  {'ÖSSZ matched':>16}: {matched_total:>8}  ({100*matched_total/n:5.1f}%)")
    print(f"  {'unmatched':>16}: {n-matched_total:>8}  ({100*(n-matched_total)/n:5.1f}%)")

    exact_share = counts.get("exact", 0) / n if n else 0
    tol_share = counts.get("hnum_tolerance", 0) / matched_total if matched_total else 0
    if exact_share < 0.30:
        print("  FIGYELEM: exact arány <30% → valószínűleg normalizálási hiba.")
    if tol_share > 0.20:
        print("  FIGYELEM: tolerance arány >20% → túl megengedő beállítás.")


def unmatched_report(coord, unmatched_cids, top=50):
    _ensure_qa_dir()
    um = coord[coord["cid"].isin(unmatched_cids)]
    by_tel = um.groupby("telepules").size().sort_values(ascending=False)
    by_tel.head(top).to_csv(os.path.join(QA_DIR, "unmatched_top_telepules.csv"))
    by_utca = um.groupby(["telepules", "utca"]).size().sort_values(ascending=False)
    by_utca.head(top).to_csv(os.path.join(QA_DIR, "unmatched_top_utca.csv"))


def valasztasi_lefedettseg_report(elect, e_valid, matched_eids):
    """A LEGFONTOSABB metrika: a választási címek hány %-át értük el valós koordinátával.

    Két nézőpont:
      - matchelhető nevező: a valid (nem üres / nem hrsz / van fp) választási címek
      - teljes nevező:      az összes egyedi választási cím (a kihagyott üres/hrsz sorokkal együtt)
    Per-település bontás CSV-be.
    """
    _ensure_qa_dir()
    n_all = len(elect)
    n_valid = len(e_valid)
    n_covered = len(matched_eids)
    n_skipped = n_all - n_valid

    cov = e_valid[["eid", "telepules_out"]].copy()
    cov["covered"] = cov["eid"].isin(matched_eids)
    # a telepules_out Budapesten már kerületnév ("Budapest N. kerület") — külön QA-bontás nem kell
    per_tel = cov.groupby("telepules_out").agg(
        valasztasi_cim=("eid", "size"),
        lefedett=("covered", "sum"),
    )
    per_tel["lefedettseg_pct"] = (100 * per_tel["lefedett"] / per_tel["valasztasi_cim"]).round(1)
    per_tel.sort_values("valasztasi_cim", ascending=False).to_csv(
        os.path.join(QA_DIR, "valasztasi_lefedettseg_telepules.csv"))

    print("\n=== VÁLASZTÁSI CÍM LEFEDETTSÉG (a fő metrika) ===")
    print(f"  egyedi választási cím (összes):      {n_all:>9}")
    print(f"  ebből matchelhető (valid):           {n_valid:>9}  "
          f"(kihagyva üres/hrsz: {n_skipped})")
    print(f"  valós koordinátával lefedett:        {n_covered:>9}")
    print(f"  --> lefedettség a matchelhetőkből:   {100*n_covered/n_valid:6.1f}%")
    print(f"  --> lefedettség az összesből:        {100*n_covered/n_all:6.1f}%")
    return per_tel


# ========================================================================================
# E. EXPORT
# ========================================================================================

def _build_outputs(coord, matched):
    """v2 (tiszta séma) és debug (flag-ekkel) GeoDataFrame felépítése."""
    geom_outlier = geom_outlier_flag(coord, matched)

    enr = coord.merge(matched, on="cid", how="left")
    enr["geom_outlier"] = enr["cid"].map(geom_outlier).fillna(False).astype(bool)

    matched_rows = enr[enr["szavazokor_id"].notna()].reset_index(drop=True)
    v2 = gpd.GeoDataFrame({
        "szavazokorid": matched_rows["szavazokor_id"].astype("int64"),
        "utca": matched_rows["utca"].astype(str),
        "hazszam": matched_rows["hazszam"].astype(str),
        "telepules": matched_rows["telepules_out"].astype(str),
        "irsz": matched_rows["irsz"].astype(str),
        "geometry": matched_rows["geometry"].values,
    }, geometry="geometry", crs="EPSG:4326")

    debug = gpd.GeoDataFrame({
        "szavazokorid": enr["szavazokor_id"],
        "utca": enr["utca"].astype(str),
        "hazszam": enr["hazszam"].astype(str),
        "telepules": enr["telepules"].astype(str),
        "telepules_fp": enr["telepules_fp"],
        "irsz": enr["irsz"].astype(str),
        "utca_fp": enr["utca_fp"],
        "match_rule": enr["match_rule"].fillna("unmatched"),
        "ambiguous": enr["ambiguous"].fillna(False).astype(bool),
        "had_leading_zeros": enr["had_leading_zeros"],
        "is_hrsz": enr["is_hrsz"],
        "empty_housenumber": enr["empty_housenumber"],
        "geom_outlier": enr["geom_outlier"],
        "geometry": enr["geometry"].values,
    }, geometry="geometry", crs="EPSG:4326")
    return v2, debug


def _export_outputs(v2, debug, out_path):
    """Kimenetek kiírása az out_path törzs alapján: <out>.gpkg, <out>.parquet, <out>_debug.gpkg.
    Az out_path megadható kiterjesztés nélkül vagy .gpkg-vel — mindkettő ugyanoda ír."""
    base = str(out_path)
    if base.endswith(".gpkg"):
        base = base[:-5]
    out_gpkg, out_parquet, debug_gpkg = base + ".gpkg", base + ".parquet", base + "_debug.gpkg"
    os.makedirs(os.path.dirname(out_gpkg) or ".", exist_ok=True)
    v2.to_file(out_gpkg, layer="points", driver="GPKG")
    v2.to_parquet(out_parquet)
    debug.to_file(debug_gpkg, layer="points", driver="GPKG")
    print(f"  kiírva: {out_gpkg}")
    print(f"  kiírva: {out_parquet}")
    print(f"  kiírva: {debug_gpkg}")


# ========================================================================================
# Fő belépési pont
# ========================================================================================

def szkid_cim_kapcsolas(elect_csv, out_path,
                        rules=("exact", "range_point", "range_overlap", "letter_collapse"),
                        hnum_tolerance=2, cardinality="many_to_many", export=True):
    """A 2014–2022-es formátumú (vesszős CSV) választási címjegyzék összekapcsolása.

    elect_csv — a nyers címjegyzék CSV útja (pl. ../data/nyers_data/valasztas_cimek_2022.csv).
    out_path  — a kimenet útja és neve (pl. ../data/work_data/osszekapcsolt_pontok_es_cimek_2022);
                ebből lesz <out>.gpkg, <out>.parquet és <out>_debug.gpkg.
    """
    print("Koordinátás oldal betöltése...")
    coord = betolt_koordinatas()
    print(f"  koordinátás pontok: {len(coord)}")

    print("Választási oldal betöltése + normalizálás + dedup...")
    elect = betolt_valasztasi(path=elect_csv)
    print(f"  egyedi választási cím: {len(elect)}")

    print(f"Matching kaszkád: {rules}")
    matched, unmatched_cids, matched_eids, e_valid = cim_matcher(
        coord, elect, rules=rules, hnum_tolerance=hnum_tolerance, cardinality=cardinality)

    print("QA...")
    coverage_report(coord, matched, unmatched_cids)
    match_rule_ratio(coord, matched)
    unmatched_report(coord, unmatched_cids)
    valasztasi_lefedettseg_report(elect, e_valid, matched_eids)

    v2, debug = _build_outputs(coord, matched)
    print(f"kimeneti sorok (matched): {len(v2)}")

    if export:
        _export_outputs(v2, debug, out_path)

    return v2, debug


def szkid_cim_kapcsolas_v2(elect_csv, out_path,
                           rules=("exact", "range_point", "range_overlap", "letter_collapse"),
                           hnum_tolerance=2, cardinality="many_to_many", export=True):
    """A 2026-os formátumú (TAB-os) választási címjegyzék összekapcsolása.

    elect_csv — a nyers címjegyzék útja (pl. ../data/nyers_data/szavkor2026.csv).
    out_path  — a kimenet útja és neve (pl. ../data/work_data/osszekapcsolt_pontok_es_cimek_2026);
                ebből lesz <out>.gpkg, <out>.parquet és <out>_debug.gpkg.

    Eltérés a szkid_cim_kapcsolas()-tól:
      - a választási oldalt a betolt_valasztasi_v2() olvassa (TAB-formátum, wardid, pir),
      - az illesztés SZIGORÚ irányítószám-kulccsal megy: geo_keys=("telepules_fp", "irsz"),
        vagyis település → irányítószám → utca → házszám hierarchia.
    """
    print("Koordinátás oldal betöltése...")
    coord = betolt_koordinatas()
    print(f"  koordinátás pontok: {len(coord)}")

    print("Választási oldal (2026-os formátum) betöltése + normalizálás + dedup...")
    elect = betolt_valasztasi_v2(path=elect_csv)
    print(f"  egyedi választási cím: {len(elect)}")

    geo_keys = ("telepules_fp", "irsz")
    print(f"Matching kaszkád: {rules} | geo-kulcs: {geo_keys}")
    matched, unmatched_cids, matched_eids, e_valid = cim_matcher(
        coord, elect, rules=rules, hnum_tolerance=hnum_tolerance,
        cardinality=cardinality, geo_keys=geo_keys)

    print("QA...")
    coverage_report(coord, matched, unmatched_cids)
    match_rule_ratio(coord, matched)
    unmatched_report(coord, unmatched_cids)
    valasztasi_lefedettseg_report(elect, e_valid, matched_eids)

    v2, debug = _build_outputs(coord, matched)
    print(f"kimeneti sorok (matched): {len(v2)}")

    if export:
        _export_outputs(v2, debug, out_path)

    return v2, debug


# ========================================================================================
# Normalizáló önteszt
# ========================================================================================

def _normalizer_onteszt():
    # házszám-parser: (lo, hi, letter, flags-subset)
    def chk(raw, lo, hi, letter, **expect_flags):
        rl, rh, rletter, _, flags = hazszam_parse(raw)
        assert (rl, rh, rletter) == (lo, hi, letter), \
            f"{raw!r} -> ({rl},{rh},{rletter}) != ({lo},{hi},{letter})"
        for k, v in expect_flags.items():
            assert flags[k] == v, f"{raw!r} flag {k}={flags[k]} != {v}"

    chk("1", 1, 1, None)
    chk("1 C", 1, 1, "C")
    chk("1/C", 1, 1, "C")
    chk("1-3", 1, 3, None)
    chk("1C/2", 1, 1, "C", truncated=True)
    chk("1C-B", 1, 1, "C", truncated=True)
    chk("000026-0028", 26, 28, None, had_leading_zeros=True)
    chk("000010-0012", 10, 12, None, had_leading_zeros=True)
    chk("000032/A", 32, 32, "A", had_leading_zeros=True)
    chk("5. sz.", 5, 5, None, truncated=False)
    chk("5 szám", 5, 5, None)
    chk("10a", 10, 10, "A")
    chk("n.a.", None, None, None, empty_housenumber=True)
    chk("0", None, None, None, empty_housenumber=True)
    chk("123 hrsz", None, None, None, is_hrsz=True)
    chk("V", 5, 5, None)

    # településnév fingerprint + Budapest egységesítés
    assert telepules_fp("Ibafa") == "ibafa"
    assert telepules_fp("Budapest XII. kerület") == "budapest"
    assert telepules_fp("12. kerület") == "budapest"
    assert telepules_fp("III. kerület") == "budapest"
    assert telepules_fp("Budapest") == "budapest"

    # kerület-kinyerés telepules_nev-ből
    assert kerulet_from_telepules_nev("Budapest XII. kerület") == 12
    assert kerulet_from_telepules_nev("Budapest I. kerület") == 1
    assert kerulet_from_telepules_nev("21. kerület") == 21
    assert kerulet_from_telepules_nev("III. kerület") == 3
    assert kerulet_from_telepules_nev("Pécs") is None
    assert kerulet_from_telepules_nev("Budapest") is None  # nincs kerület-token

    # 2026-os telepnev-forma: "Budapest XII" (nincs "kerület" utótag)
    assert kerulet_from_telepules_nev("Budapest XII") == 12
    assert kerulet_from_telepules_nev("Budapest I") == 1
    assert kerulet_from_telepules_nev("Budapest XXIII") == 23
    assert kerulet_from_telepules_nev("budapest 12") == 12
    assert kerulet_from_telepules_nev("XII") is None       # se prefix, se utótag
    assert kerulet_from_telepules_nev("Budapest XXIV") is None
    assert telepules_fp("Budapest VI") == "budapest"

    # kanonikus kerületnevek — pontosan a varosok.txt-beli alak
    assert len(BP_KERULET_NEV) == 23
    assert kerulet_nev(1) == "Budapest I. kerület"
    assert kerulet_nev(23) == "Budapest XXIII. kerület"
    assert kerulet_nev(None) is None and kerulet_nev(pd.NA) is None and kerulet_nev(0) is None
    _vt = "../data/nyers_data/varosok.txt"
    if os.path.exists(_vt):
        with open(_vt, encoding="utf-8") as f:
            _nevek = {v.strip() for v in f if v.strip()}
        _hianyzo = [n for n in BP_KERULET_NEV.values() if n not in _nevek]
        assert not _hianyzo, f"varosok.txt-ből hiányzó kanonikus kerületnév: {_hianyzo}"

    # kerület-kinyerés postcode-ból
    assert kerulet_from_postcode("1219") == 21
    assert kerulet_from_postcode("1011") == 1
    assert kerulet_from_postcode("1033") == 3
    assert kerulet_from_postcode("1500") is None  # nincs 50. kerület
    assert kerulet_from_postcode("2040") is None  # nem Budapest
    assert kerulet_from_postcode("") is None
    assert kerulet_from_postcode(None) is None

    # utcanév fingerprint
    s = pd.Series(["Petőfi Sándor u.", "Kis-altábornagy utca", "BARTÓK BÉLA tér", "Kossuth krt."])
    fp = utca_fp_series(s).tolist()
    assert fp[0] == "petofisandorutca", fp[0]
    assert fp[1] == "kisaltabornagyutca", fp[1]
    assert fp[2] == "bartokbelater", fp[2]
    assert fp[3] == "kossuthkorut", fp[3]

    print("Normalizáló önteszt: minden assert OK.")


if __name__ == '__main__':
    _normalizer_onteszt()

    # 2022 cím generálás (2014–2022-es formátum)
    print("2022 cím generálás")
    szkid_cim_kapcsolas('../data/nyers_data/valasztas_cimek_2022.csv',
                        '../data/work_data/osszekapcsolt_pontok_es_cimek_2022')

    # 2014 cím generálás
    print("2014 cím generálás")
    szkid_cim_kapcsolas('../data/nyers_data/valasztas_cimek_2014.csv',
                        '../data/work_data/osszekapcsolt_pontok_es_cimek_2014')

    # 2026 cím generálás (2026-os formátum, irsz-kulcsos)
    print("2026 cím generálás")
    szkid_cim_kapcsolas_v2('../data/nyers_data/szavkor2026.csv',
                           '../data/work_data/osszekapcsolt_pontok_es_cimek_2026')
