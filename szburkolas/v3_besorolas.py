"""
v3_besorolas – 9. lépés (üres cellák besorolása) és 10. lépés (lezárás).

Leírás: 9.1–9.3, 10.1–10.3, 12. (szerkezeti mutatók).

9. Három szabály, mindig a legbiztosabbtól a legbizonytalanabb felé: utca menti folytonosság
   (sáv), rang-szabály (éltábla), legközelebbi cella.
10. Hibás, egycímes szigetek visszavonása; egyesítés kategóriánként; validáció.
"""

from collections import Counter

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import LineString

import v3_param as P
import v3_topologia as T
from v3_felosztas import jelzes_hozzaad
from v3_io import nyil


# ──────────────────────────────────────────────────────────────────────────────
# 9.1 Sáv: utca menti folytonosság
# ──────────────────────────────────────────────────────────────────────────────

def lepes_9_1_folytonossag(cellak, utca_vonalak, debug, lepes='9.1'):
    """MINDEN üres sávRA (utca U): jelöltek ← a U-hoz tartozó, címes besorolású sávok UGYANAZON AZ
    OLDALON (a reprezentatív pontjaikat összekötő szakasz nem keresztezi U-t); a két irányban a
    legközelebbi jelölt: egyező kategória vagy csak egy → az; eltérő → a közelebbi, és a sáv
    'eltérő-folytonosság' JELZÉST kap. Egy menetben, csak a címes sávokból örökölve."""
    cellak = cellak.copy()
    savok = cellak[(cellak['cellatipus'] == P.CELLA_SAV) & cellak['utca_id'].notna() & cellak['utca_vonal_idx'].notna()]
    cimes = savok[(savok['szabaly'] == P.SZABALY_CIMES) & savok['szkid'].notna()]
    ures = savok[savok['szkid'].isna()]
    rep = {cid: g.representative_point() for cid, g in zip(savok['cella_id'], savok.geometry)}
    nyilak, iranyok, jelzesek = [], [], []
    uj_szkid, uj_szabaly, uj_jelzes = {}, {}, {}
    for sor in ures.itertuples(index=False):
        U = utca_vonalak[sor.utca_id][int(sor.utca_vonal_idx)]
        jeloltek = cimes[(cimes['utca_id'] == sor.utca_id) & (cimes['utca_vonal_idx'] == sor.utca_vonal_idx)]
        if jeloltek.empty:
            continue
        t_sajat = U.project(rep[sor.cella_id])
        alatta, felette = None, None   # (t-távolság, szkid, cella_id)
        for j in jeloltek.itertuples(index=False):
            if LineString([rep[sor.cella_id], rep[j.cella_id]]).crosses(U):
                continue   # a másik oldalon van
            t_j = U.project(rep[j.cella_id])
            if t_j < t_sajat:
                if alatta is None or t_sajat - t_j < alatta[0]:
                    alatta = (t_sajat - t_j, int(j.szkid), j.cella_id)
            else:
                if felette is None or t_j - t_sajat < felette[0]:
                    felette = (t_j - t_sajat, int(j.szkid), j.cella_id)
        if alatta is None and felette is None:
            continue
        if alatta is not None and felette is not None and alatta[1] != felette[1]:
            nyertes = alatta if alatta[0] <= felette[0] else felette
            jelzes = P.JELZES_FOLYTONOSSAG
        else:
            nyertes = alatta if alatta is not None else felette
            jelzes = ''
        uj_szkid[sor.cella_id] = nyertes[1]
        uj_szabaly[sor.cella_id] = P.SZABALY_FOLYTONOSSAG
        if jelzes:
            uj_jelzes[sor.cella_id] = jelzes
        nyilak.append(nyil(sor.geometry, cellak.loc[cellak['cella_id'] == nyertes[2], 'geometry'].iloc[0]))
        iranyok.append('kisebb t' if nyertes is alatta else 'nagyobb t')
        jelzesek.append(jelzes)
    for i in cellak.index:
        cid = cellak.at[i, 'cella_id']
        if cid in uj_szkid:
            cellak.at[i, 'szkid'] = uj_szkid[cid]
            cellak.at[i, 'szabaly'] = uj_szabaly[cid]
            if cid in uj_jelzes:
                cellak.at[i, 'jelzes'] = jelzes_hozzaad(cellak.at[i, 'jelzes'], uj_jelzes[cid])
    debug.reteg(f'{lepes.replace(".", "_")}_folytonossag', lepes, 'folytonosság nyíl', nyilak, P.SZIN['nyíl'],
                irany=iranyok, jelzes=jelzesek)
    return cellak


# ──────────────────────────────────────────────────────────────────────────────
# 9.2 Rang-szabály
# ──────────────────────────────────────────────────────────────────────────────

def lepes_9_2_rang(cellak, eltabla, debug, lepes='9.2'):
    """ISMÉTELD, AMÍG változik: MINDEN üres cellára a besorolt szomszédok közül a legkisebb rangú
    közös élen át (egyenlő rangnál a leghosszabbon) örököl; ugyanabban a körben a többi üres már
    látja. Vázvonalon (rang 1) könnyen, vasúton (rang 6) csak ha más nincs, lakott határon soha."""
    cellak = cellak.copy()
    szkid = dict(zip(cellak['cella_id'], cellak['szkid']))
    geom = dict(zip(cellak['cella_id'], cellak['geometry']))
    nyilak, rangok = [], []
    besorolt_szabaly = {}
    while True:
        valtozott = False
        besoroltak = {c for c, s in szkid.items() if s is not None and not (isinstance(s, float) and np.isnan(s))}
        for cid in sorted(c for c in szkid if c not in besoroltak):
            osszesites = T.szomszedok_osszesitve(eltabla, cid, csak_cellak=besoroltak)
            nyertes = T.nyertes_szomszed(osszesites)
            if nyertes is None:
                continue
            szkid[cid] = szkid[nyertes[0]]
            besorolt_szabaly[cid] = P.SZABALY_RANG
            besoroltak.add(cid)
            nyilak.append(nyil(geom[cid], geom[nyertes[0]])); rangok.append(nyertes[1])
            valtozott = True
        if not valtozott:
            break
    for i in cellak.index:
        cid = cellak.at[i, 'cella_id']
        if cid in besorolt_szabaly:
            cellak.at[i, 'szkid'] = szkid[cid]
            cellak.at[i, 'szabaly'] = besorolt_szabaly[cid]
    debug.reteg(f'{lepes.replace(".", "_")}_rang', lepes, 'rang-szabály nyíl', nyilak, P.SZIN['nyíl'], nyertes_rang=rangok)
    return cellak


# ──────────────────────────────────────────────────────────────────────────────
# 9.3 Legközelebbi cella
# ──────────────────────────────────────────────────────────────────────────────

def lepes_9_3_legkozelebbi(cellak, debug, lepes='9.3'):
    """A még üres cellák (nincs közös élük semmivel, pl. utca nélküli folt) a legközelebbi besorolt
    cella kategóriáját kapják; HA nincs ilyen → üres marad, jelzéssel."""
    cellak = cellak.copy()
    besorolt = cellak[cellak['szkid'].notna()]
    ures_idx = cellak.index[cellak['szkid'].isna()]
    nyilak, tavok = [], []
    if len(ures_idx) == 0:
        return cellak
    if besorolt.empty:
        for i in ures_idx:
            cellak.at[i, 'jelzes'] = jelzes_hozzaad(cellak.at[i, 'jelzes'], P.JELZES_BESOROLATLAN)
        return cellak
    fa = shapely.STRtree(np.asarray(besorolt.geometry.values, dtype=object))
    for i in ures_idx:
        g = cellak.at[i, 'geometry']
        j = int(fa.query_nearest(g)[0])
        cel = besorolt.iloc[j]
        cellak.at[i, 'szkid'] = cel['szkid']
        cellak.at[i, 'szabaly'] = P.SZABALY_LEGKOZELEBBI
        nyilak.append(nyil(g, cel.geometry)); tavok.append(float(g.distance(cel.geometry)))
    debug.reteg(f'{lepes.replace(".", "_")}_legkozelebbi', lepes, 'legközelebbi nyíl', nyilak, P.SZIN['nyíl'], tav=tavok)
    return cellak


def lepes_9_ures_besorolas(cellak, eltabla, cimek, utca_vonalak, debug, lepes_elotag='9', folytonossag=True):
    """9. lépés – a három szabály sorban, majd R. rutin. `lepes_elotag` a debug rétegek nevéhez
    (a 10.1 utáni újrafutásnál '10_1_9'). `folytonossag=False`: a 9.1 kimarad – a 10.1 utáni
    újrafutásnál, mert az utca menti folytonosság ugyanazt a (nem szomszédos sávtól örökölt)
    szigetet hozná létre újra; a rang-szabály és a legközelebbi cella csak szomszédtól örököl,
    így a sziget a környezetébe olvad."""
    if folytonossag:
        cellak = lepes_9_1_folytonossag(cellak, utca_vonalak, debug, lepes=f'{lepes_elotag}.1')
    cellak = lepes_9_2_rang(cellak, eltabla, debug, lepes=f'{lepes_elotag}.2')
    cellak = lepes_9_3_legkozelebbi(cellak, debug, lepes=f'{lepes_elotag}.3')
    cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek)
    debug.cellak(f'{lepes_elotag}_R_cellak', f'{lepes_elotag}.R', cellak, szabaly=cellak['szabaly'].tolist())
    return cellak, eltabla, cimek


# ──────────────────────────────────────────────────────────────────────────────
# 10.1 Hibás, egycímes szigetek
# ──────────────────────────────────────────────────────────────────────────────

def lepes_10_1_szigetek(cellak, cimek, debug):
    """MINDEN kategóriáRA: cellái egyesítve, összefüggő darabokra bontva; a legfeljebb 1 címet
    tartalmazó darab gyanús (BIZTONSÁG: ha minden darab gyanús, a legnagyobb marad). A gyanús
    darabok cellái → ÜRES, a bennük lévő cím 'hibás-sziget' jelzést kap (a besorolásban és a
    validációban nem számít). Visszatérés: (cellak, cimek, gyanús cella_id-k)."""
    cellak = cellak.copy()
    cimek = cimek.copy()
    szamito = T.szamito_cimek(cimek)
    cim_fa = shapely.STRtree(np.asarray(szamito.geometry.values, dtype=object)) if len(szamito) else None
    gyanus_cellak = set()
    gyanus_geomok, gyanus_cimszam = [], []
    for szkid, csoport in cellak[cellak['szkid'].notna()].groupby('szkid'):
        darabok = T.poligonok_darabokra(shapely.unary_union(np.asarray(csoport.geometry.values, dtype=object), grid_size=P.RACS))
        if len(darabok) <= 1:
            continue
        cimszam = [len(cim_fa.query(d, predicate='contains')) if cim_fa is not None else 0 for d in darabok]
        gyanus = [i for i, n in enumerate(cimszam) if n <= 1]
        if len(gyanus) == len(darabok):
            legnagyobb = int(np.argmax([d.area for d in darabok]))
            gyanus = [i for i in gyanus if i != legnagyobb]
        cella_fa = shapely.STRtree(np.asarray(csoport.geometry.values, dtype=object))
        for i in gyanus:
            gyanus_geomok.append(darabok[i]); gyanus_cimszam.append(cimszam[i])
            for ci in cella_fa.query(darabok[i].representative_point(), predicate='within'):
                gyanus_cellak.add(int(csoport.iloc[ci]['cella_id']))
            # minden cella, amelynek reprezentatív pontja a darabban van
            for ci in cella_fa.query(darabok[i], predicate='intersects'):
                if darabok[i].contains(csoport.iloc[ci].geometry.representative_point()):
                    gyanus_cellak.add(int(csoport.iloc[ci]['cella_id']))
            if cim_fa is not None:
                for pi in cim_fa.query(darabok[i], predicate='contains'):
                    idx = szamito.index[pi]
                    cimek.at[idx, 'jelzes'] = jelzes_hozzaad(cimek.at[idx, 'jelzes'], P.JELZES_HIBAS_SZIGET)
    for i in cellak.index:
        if cellak.at[i, 'cella_id'] in gyanus_cellak:
            cellak.at[i, 'szkid'] = None
            cellak.at[i, 'szabaly'] = None
    debug.reteg('10_1_szigetek', '10.1', 'gyanús sziget', gyanus_geomok, P.SZIN['jelzés'], cimek_szama=gyanus_cimszam)
    return cellak, cimek, gyanus_cellak


# ──────────────────────────────────────────────────────────────────────────────
# 10.2 Egyesítés kategóriánként
# ──────────────────────────────────────────────────────────────────────────────

def lepes_10_2_egyesites(cellak, crs):
    """MINDEN kategóriáRA: az összes cellája uniója → a körzet poligonja (MultiPolygon megengedett).
    A besorolatlan cellák (szkid None) külön sorba kerülnek 'besorolatlan' jelzéssel, hogy a
    lefedés teljes maradjon. A körzet jelzése a cellái jelzéseinek uniója."""
    sorok = []
    for szkid, csoport in cellak.groupby('szkid', dropna=False):
        geom = shapely.unary_union(np.asarray(csoport.geometry.values, dtype=object), grid_size=P.RACS)
        jelzesek = sorted({r for j in csoport['jelzes'].fillna('') for r in j.split(';') if r})
        if szkid is None or (isinstance(szkid, float) and np.isnan(szkid)):
            szkid_ertek = None
            jelzesek = sorted(set(jelzesek) | {P.JELZES_BESOROLATLAN})
        else:
            szkid_ertek = int(szkid)
        sorok.append({'szkid': szkid_ertek, 'jelzes': ';'.join(jelzesek), 'geometry': geom})
    korzetek = gpd.GeoDataFrame(sorok, columns=['szkid', 'jelzes', 'geometry'], geometry='geometry', crs=crs)
    return korzetek


# ──────────────────────────────────────────────────────────────────────────────
# 10.3 Validáció
# ──────────────────────────────────────────────────────────────────────────────

def lepes_10_3_validacio(korzetek, referencia_terulet, cimek, debug):
    """VALIDÁCIÓ – mindnek teljesülnie kell:
    • nincs átfedés (> 0,001 m²)
    • a lefedett terület megegyezik a 2. lépés celláinak uniójával (eltérés ≤ max(5 m², 0,01 %))
    • nincs lógó vonal
    • egyetlen (számító) cím sincs a saját kategóriájától eltérő körzetben
    • annyi körzet jött létre, ahány kategória (szkid) a bemeneti címekben van: minden kategóriának
      pontosan egy körzet-poligonja (MultiPolygon) van, és nincs kategória nélküli körzet
    Visszatérés: (rendben, hibaüzenetek listája)."""
    hibak, hiba_geomok, hiba_okok = [], [], []
    geomok = np.asarray(korzetek.geometry.values, dtype=object)

    # körzetek száma = kategóriák száma
    kategoriak = set(int(k) for k in cimek['szkid'].dropna().unique())
    korzet_kategoriak = set(int(k) for k in korzetek['szkid'].dropna())
    hianyzo = sorted(kategoriak - korzet_kategoriak)
    tobblet = sorted(korzet_kategoriak - kategoriak)
    if hianyzo:
        hibak.append(f'{len(hianyzo)} kategóriának nem jött létre körzete: {hianyzo}')
    if tobblet:
        hibak.append(f'körzet olyan kategóriával, amely nincs a címek között: {tobblet}')
    # (a besorolatlan, kategória nélküli poligon – elszigetelt, cím nélküli folt – a leírás 9.3 szerint
    #  megengedett kimenet 'besorolatlan' jelzéssel, nem validációs hiba; a napló számolja)
    if len(korzetek['szkid'].dropna()) != len(korzet_kategoriak):
        hibak.append('egy kategóriához több körzet-sor tartozik')

    # átfedés
    if len(geomok) > 1:
        fa = shapely.STRtree(geomok)
        a_idx, b_idx = fa.query(geomok, predicate='intersects')
        for a, b in zip(a_idx, b_idx):
            if a < b:
                metszet = geomok[a].intersection(geomok[b])
                if metszet.area > P.ATFEDES_TURES_M2:
                    hibak.append(f'átfedés {korzetek.iloc[a]["szkid"]}–{korzetek.iloc[b]["szkid"]}: {metszet.area:.3f} m²')
                    hiba_geomok.append(metszet); hiba_okok.append('átfedés')

    # lefedés
    osszterulet = float(sum(g.area for g in geomok))
    tures = max(P.LEFEDES_TURES_ABS_M2, P.LEFEDES_TURES_REL * referencia_terulet)
    if abs(osszterulet - referencia_terulet) > tures:
        hibak.append(f'lefedés: {osszterulet:.1f} m² vs {referencia_terulet:.1f} m² (tűrés {tures:.1f})')

    # lógó vonal
    hatarok = shapely.unary_union(shapely.set_precision(np.array([g.boundary for g in geomok], dtype=object), P.RACS), grid_size=P.RACS)
    eredmeny = T.polygonize_full_nev_szerint(T.vonalak_darabokra(hatarok))
    if eredmeny['logo'] or eredmeny['ervenytelen']:
        hibak.append(f"lógó vonal: {len(eredmeny['logo'])} lógó, {len(eredmeny['ervenytelen'])} érvénytelen")
        hiba_geomok.extend(eredmeny['logo'] + eredmeny['ervenytelen']); hiba_okok.extend(['lógó'] * (len(eredmeny['logo']) + len(eredmeny['ervenytelen'])))

    # címek a saját kategóriájuk körzetében
    szamito = T.szamito_cimek(cimek)
    if len(szamito) and len(geomok):
        fa = shapely.STRtree(geomok)
        cim_idx, korzet_idx = fa.query(np.asarray(szamito.geometry.values, dtype=object), predicate='intersects')
        talalt = {}
        for ci, ki in zip(cim_idx, korzet_idx):
            talalt.setdefault(ci, set()).add(korzetek.iloc[ki]['szkid'])
        szkidk = szamito['szkid'].to_numpy()
        atkerult = [ci for ci, k in talalt.items() if int(szkidk[ci]) not in k]
        if atkerult:
            hibak.append(f'{len(atkerult)} cím a saját kategóriájától eltérő körzetben')
            for ci in atkerult:
                hiba_geomok.append(szamito.geometry.iloc[ci]); hiba_okok.append('átkerült cím')

    debug.reteg('10_3_validacio', '10.3', 'validációs hiba', hiba_geomok, P.SZIN['jelzés'], ok=hiba_okok)
    return len(hibak) == 0, hibak


# ──────────────────────────────────────────────────────────────────────────────
# 12. Szerkezeti mutatók
# ──────────────────────────────────────────────────────────────────────────────

def szerkezeti_mutatok(cellak_7, cimek_7, vagas_tipusok, korzetek, n_rang3_torolt, n_narancs,
                       qa_cimek_kivul, qa_foutak_kivul, lakott_gyanus, n_azonos_pont, n_hibas_sziget,
                       validacio_ok):
    """A 12. fejezet mutatói településenként egy dict-ben (a napló CSV egy sora)."""
    kategoriak = T.cella_kategoriak(cimek_7)
    n_cella = len(cellak_7)
    n_vegyes = sum(1 for c in cellak_7['cella_id'] if len(kategoriak.get(c, ())) > 1)
    ures_terulet = float(sum(g.area for c, g in zip(cellak_7['cella_id'], cellak_7.geometry) if c not in kategoriak))
    osszterulet = float(cellak_7.area.sum())
    jelzesek = Counter(r for j in korzetek['jelzes'].fillna('') for r in j.split(';') if r)
    return {
        'n_cella': n_cella,
        'n_vegyes': n_vegyes,
        'vegyes_arany': round(n_vegyes / n_cella, 4) if n_cella else 0.0,
        'ures_terulet_arany': round(ures_terulet / osszterulet, 4) if osszterulet else 0.0,
        'vagas_T': vagas_tipusok.get(P.VAGAS_T, 0),
        'vagas_kozep': vagas_tipusok.get(P.VAGAS_KOZEP, 0),
        'vagas_kozos': vagas_tipusok.get(P.VAGAS_KOZOS, 0),
        'vagas_parhuzamos': vagas_tipusok.get(P.VAGAS_PARHUZAMOS, 0),
        'vagas_voronoi': vagas_tipusok.get(P.VAGAS_VORONOI, 0),
        'jelzes_folytonossag': jelzesek.get(P.JELZES_FOLYTONOSSAG, 0),
        'jelzes_haromszog': jelzesek.get(P.JELZES_HAROMSZOG, 0),
        'jelzes_validacio': 0 if validacio_ok else 1,
        'jelzes_besorolatlan': jelzesek.get(P.JELZES_BESOROLATLAN, 0),
        'n_rang3_torolt': n_rang3_torolt,
        'n_narancs': n_narancs,
        'qa_cimek_kivul': round(qa_cimek_kivul, 4),
        'qa_foutak_kivul': round(qa_foutak_kivul, 4),
        'lakott_gyanus': bool(lakott_gyanus),
        'n_azonos_pont': n_azonos_pont,
        'n_hibas_sziget': n_hibas_sziget,
        'n_korzet': int(korzetek['szkid'].notna().sum()),
        'n_kategoria': int(cimek_7['szkid'].nunique()),
    }
