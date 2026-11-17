"""
v3_vonalhalo – 0. lépés (előkészítés), 1. lépés (vonalháló) és 2. lépés (cellák képzése).

Leírás: 0.2–0.4, 1.1–1.7, 2.1–2.3.

A lépések végén álló `debug.reteg(...)` hívások a leírás 11.1 táblázatának rétegeit írják.
"""

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import linemerge

import v3_param as P
import v3_topologia as T
from v3_io import NincsLakottTerulet


# ──────────────────────────────────────────────────────────────────────────────
# 0.2 Lakott terület vágása a közigazgatási határra
# ──────────────────────────────────────────────────────────────────────────────

def lepes_0_2_lakott_vagas(bemenet, debug):
    """MINDEN lakott foltRA: folt ← folt ∩ közigazgatási határ; HA üres → eldobom.
    Szabály: ha kérdéses, mindig a hivatalos közigazgatási határ a szél.
    Visszatérés: GeoSeries, soronként egy Polygon (a MultiPolygon eredmény darabolva)."""
    foltok = []
    for folt in bemenet.lakott_nyers:
        metszet = shapely.make_valid(folt).intersection(bemenet.kozig_hatar)
        foltok.extend(T.poligonok_darabokra(metszet))
    if not foltok:
        raise NincsLakottTerulet(f'A lakott foltok a közigazgatási határon kívül esnek: {bemenet.varos!r}')
    # Az OSM lakott poligonjai átfedhetik egymást (egymásba rajzolt landuse-területek). A foltok a
    # vágott poligonok UNIÓJÁNAK összefüggő darabjai: így egy terület egyszer számít, és a 0.4-ben
    # az utca nélküli folt nem fedheti át a hálózatos foltot.
    unio = shapely.unary_union(np.array(foltok, dtype=object), grid_size=P.RACS)
    lakott_vagott = gpd.GeoSeries(T.poligonok_darabokra(unio), crs=bemenet.crs)

    debug.reteg('0_2_lakott_nyers', '0.2', 'lakott folt (nyers)', bemenet.lakott_nyers, P.SZIN['segéd'])
    debug.reteg('0_2_kozig_hatar', '0.2', 'közigazgatási határ',
                T.vonalak_darabokra(bemenet.kozig_hatar.boundary), P.SZIN['közigazgatási határ'])
    debug.reteg('0_2_lakott_vagott', '0.2', 'lakott folt (vágott)', lakott_vagott, P.SZIN['lakott határ'])
    return lakott_vagott


# ──────────────────────────────────────────────────────────────────────────────
# 0.3 Vonalhálózat vágása a lakott területre
# ──────────────────────────────────────────────────────────────────────────────

def lepes_0_3_vonalak_vagasa(vonalak_nyers, lakott_vagott, debug):
    """MINDEN vonalRA (utca, vasút, folyó): vonal ← vonal ∩ (lakott foltok uniója), az
    attribútumok megmaradnak; MultiLineString → darabok. A hálózaton más előkészítés nincs.

    QA (b): a LEVÁGOTT rang 4–5 úthossz aránya a lakott foltok együttes burkán (convex_hull)
    belüli teljes rang 4–5 úthosszhoz.
    Visszatérés: (vonalak_vagott GeoDataFrame, qa_foutak_kivul arány)."""
    lakott_unio = shapely.unary_union(np.asarray(lakott_vagott.values, dtype=object))
    burok = lakott_unio.convex_hull

    vagott_sorok, levagott_sorok = [], []
    teljes_hossz_45 = 0.0
    levagott_hossz_45 = 0.0
    for sor in vonalak_nyers.itertuples(index=False):
        bent = sor.geometry.intersection(lakott_unio)
        for darab in T.vonalak_darabokra(bent):
            vagott_sorok.append({'tipus': sor.tipus, 'rang': sor.rang, 'utca_id': sor.utca_id,
                                 'highway': sor.highway, 'name': sor.name, 'geometry': darab})
        kint = sor.geometry.difference(lakott_unio)
        for darab in T.vonalak_darabokra(kint):
            levagott_sorok.append({'rang': sor.rang, 'geometry': darab})
        if sor.rang in (P.RANG_LAKOUTCA, P.RANG_FOUT):
            teljes_hossz_45 += sor.geometry.intersection(burok).length
            levagott_hossz_45 += kint.intersection(burok).length

    vonalak_vagott = gpd.GeoDataFrame(
        vagott_sorok, columns=['tipus', 'rang', 'utca_id', 'highway', 'name', 'geometry'],
        geometry='geometry', crs=vonalak_nyers.crs)
    levagott = gpd.GeoDataFrame(levagott_sorok, columns=['rang', 'geometry'],
                                geometry='geometry', crs=vonalak_nyers.crs)
    qa_foutak_kivul = levagott_hossz_45 / teljes_hossz_45 if teljes_hossz_45 > 0 else 0.0

    debug.reteg('0_3_vonalak_nyers', '0.3', 'vonal (nyers)', vonalak_nyers.geometry,
                [P.rang_szin(r, t) for r, t in zip(vonalak_nyers['rang'], vonalak_nyers['tipus'])],
                highway=vonalak_nyers['highway'].tolist(), name=vonalak_nyers['name'].tolist(),
                rang=vonalak_nyers['rang'].tolist())
    debug.reteg('0_3_vonalak_levagott', '0.3', 'vonal (levágott)', levagott.geometry,
                P.SZIN['törölt vonal'], rang=levagott['rang'].tolist())
    debug.reteg('0_3_vonalak_vagott', '0.3', 'vonal (vágott)', vonalak_vagott.geometry,
                [P.rang_szin(r, t) for r, t in zip(vonalak_vagott['rang'], vonalak_vagott['tipus'])],
                rang=vonalak_vagott['rang'].tolist(), utca_id=vonalak_vagott['utca_id'].tolist())
    return vonalak_vagott, qa_foutak_kivul


# ──────────────────────────────────────────────────────────────────────────────
# 0.4 Foltok szétválasztása
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Foltok:
    """A 0.4 lépés kimenete."""
    lakott_terulet: object                 # hálózatos foltok uniója ((Multi)Polygon)
    hatar_vonalak: list                    # a lakott terület körvonalának gyűrűi (LineString-ek, rang 7)
    utca_nelkuli_foltok: list              # Polygon-lista: egyben, egy cellaként mennek tovább
    lakott_osszes: object                  # minden vágott folt uniója (QA (a) és a 0.1 rövid úthoz)
    qa_cimek_kivul: float = 0.0
    lakott_gyanus: bool = False


def lepes_0_4_foltok(lakott_vagott, vonalak_vagott, cimek, qa_foutak_kivul, debug):
    """MINDEN lakott foltRA: HA fut benne vonal (hálózat ∩ folt hossza > 0) → HÁLÓZATOS folt,
    KÜLÖNBEN → UTCA NÉLKÜLI folt. lakott_terület = hálózatos foltok uniója; határ = körvonala.

    QA (a): a lakott területen KÍVÜL eső címpontok aránya; JELZÉS 'lakott terület gyanús',
    HA (a) > 10 % VAGY (b) > 30 %. A jelzés a kimenetet nem változtatja."""
    vonal_geomok = np.asarray(vonalak_vagott.geometry.values, dtype=object)
    fa = shapely.STRtree(vonal_geomok) if len(vonal_geomok) else None

    halozatos, utca_nelkuli, folt_tipusok = [], [], []
    for folt in lakott_vagott:
        van_vonal = False
        if fa is not None:
            jeloltek = fa.query(folt, predicate='intersects')
            for j in jeloltek:
                if vonal_geomok[j].intersection(folt).length > 0:
                    van_vonal = True
                    break
        if van_vonal:
            halozatos.append(folt)
            folt_tipusok.append('hálózatos')
        else:
            utca_nelkuli.append(folt)
            folt_tipusok.append('utca nélküli')

    lakott_osszes = shapely.unary_union(np.asarray(lakott_vagott.values, dtype=object))
    lakott_terulet = shapely.unary_union(np.array(halozatos, dtype=object)) if halozatos else shapely.Polygon()
    hatar_vonalak = T.vonalak_darabokra(lakott_terulet.boundary) if not lakott_terulet.is_empty else []

    # QA (a): címek a lakott területen kívül
    kivul = cimek[~cimek.geometry.intersects(lakott_osszes)] if len(cimek) else cimek
    qa_cimek_kivul = len(kivul) / len(cimek) if len(cimek) else 0.0
    lakott_gyanus = (qa_cimek_kivul > P.QA_CIMEK_KIVUL_KUSZOB) or (qa_foutak_kivul > P.QA_FOUTAK_KIVUL_KUSZOB)

    debug.reteg('0_4_foltok', '0.4', 'folt', lakott_vagott,
                [P.SZIN['lakott határ'] if t == 'hálózatos' else P.SZIN['cella üres'] for t in folt_tipusok],
                folt_tipus=folt_tipusok)
    debug.reteg('0_4_cimek_kivul', '0.4', 'címpont a lakott területen kívül', kivul.geometry,
                [debug.kategoria_szin(s) for s in kivul['szkid']], szkid=kivul['szkid'].tolist())
    return Foltok(lakott_terulet=lakott_terulet, hatar_vonalak=hatar_vonalak,
                  utca_nelkuli_foltok=utca_nelkuli, lakott_osszes=lakott_osszes,
                  qa_cimek_kivul=qa_cimek_kivul, lakott_gyanus=lakott_gyanus)


# ──────────────────────────────────────────────────────────────────────────────
# 1. lépés – A vonalháló
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Vonalhalo:
    """Az 1. lépés kimenete: zárt vonalháló, minden darab attribútummal, és a napló-mutatók."""
    tabla: gpd.GeoDataFrame        # vonal_id, tipus, rang, utca_id, geometry
    utca_vonalak: dict             # {utca_id: [LineString, ...]} – az U vonalak (8.1, 8.3, 9.1)
    n_rang3_torolt: int = 0
    n_narancs: int = 0
    n_logo_torolt: int = 0


def _osszefesules(tabla):
    """Összefésülés (noding): az összes vonal egyetlen lépésben egyesítve (unary_union) → minden
    kereszteződésben közös csúcs, minden vonal ott elvágva; utána attribútum-visszaillesztés (1.3).
    Az egyesítés a RÁCS rácson fut (grid_size): így a lakott határra vágott utcavég, amely a
    kerekítés után legfeljebb fél centire ül a határ szakaszától, biztosan csomópontot kap
    (snap-rounding noding) – enélkül mm-es lógó végek maradnának a határ mellett.
    Visszatérés: GeoDataFrame [tipus, rang, utca_id, geometry] a darabokról."""
    unio = shapely.unary_union(np.asarray(tabla.geometry.values, dtype=object), grid_size=P.RACS)
    darabok = T.vonalak_darabokra(unio)
    attributumok = T.attributum_visszaillesztes(darabok, tabla)
    return gpd.GeoDataFrame(attributumok, geometry=darabok, crs=tabla.crs)


def _rang3_logo_vegek_torlese(darabok, debug):
    """1.4 ISMÉTELD, AMÍG változik: MINDEN rang 3 darabRA, HA valamelyik végpontjához egyetlen
    más darab sem csatlakozik (fokszám 1) → törlöm. Visszatérés: (darabok, törölt db)."""
    torolt_geomok, torolt_korok = [], []
    kor = 0
    while True:
        kor += 1
        fokszam = T.vegpont_fokszam(list(darabok.geometry))
        torlendo = []
        for i, sor in enumerate(darabok.itertuples(index=False)):
            if sor.rang != P.RANG_GYALOGUT:
                continue
            eleje, vege = T.vegpontok(sor.geometry)
            if fokszam[eleje] == 1 or fokszam[vege] == 1:
                torlendo.append(i)
        if not torlendo:
            break
        torolt_geomok.extend(darabok.geometry.iloc[torlendo].tolist())
        torolt_korok.extend([kor] * len(torlendo))
        darabok = darabok.drop(darabok.index[torlendo]).reset_index(drop=True)
    debug.reteg('1_4_rang3_torolt', '1.4', 'törölt rang 3 lógó darab', torolt_geomok,
                P.SZIN['törölt vonal'], kor=torolt_korok)
    return darabok, len(torolt_geomok)


def _narancs_vonalak(darabok, debug):
    """1.5 Zsákutcák lezárása – narancs segédvonal.

    zsákutca-vég = csomópont, amelyhez csak EGY darab csatlakozik, ÉS az rang 4 vagy 5.
    irány ← az utca utolsó szakaszának iránya kifelé; sugár ← félegyenes (a befoglaló téglalap
    átlójánál hosszabb szakasz); találat ← a sugár LEGKÖZELEBBI metszése bármely MÁS darabbal
    (a saját darab és a 0,25 m-en belüli találat nem számít). Felső korlát nincs: a lakott
    határ mindig megállítja. Visszatérés: GeoDataFrame [tipus, rang, utca_id, geometry]."""
    geomok = np.asarray(darabok.geometry.values, dtype=object)
    tipusok = darabok['tipus'].tolist()
    rangok = darabok['rang'].tolist()
    fokszam = T.vegpont_fokszam(list(geomok))
    fa = shapely.STRtree(geomok)
    minx, miny, maxx, maxy = shapely.total_bounds(geomok)
    sugar_hossz = np.hypot(maxx - minx, maxy - miny) + 1.0

    kiindulok, narancsok, talalatok, hosszak, talalat_tipusok, kiindulo_rangok = [], [], [], [], [], []
    for i, geom in enumerate(geomok):
        if rangok[i] not in (P.RANG_LAKOUTCA, P.RANG_FOUT):
            continue
        koordinatak = shapely.get_coordinates(geom)
        # a darab mindkét vége lehet zsákutca-vég; az irány mindig kifelé mutat
        for vegpont, belso in ((koordinatak[-1], koordinatak[-2]), (koordinatak[0], koordinatak[1])):
            if fokszam[tuple(vegpont)] != 1:
                continue
            irany = vegpont - belso
            hossz = np.hypot(*irany)
            if hossz == 0:
                continue
            irany = irany / hossz
            sugar = LineString([vegpont, vegpont + irany * sugar_hossz])
            kiindulo = Point(vegpont)
            legkozelebbi, legkozelebbi_tav, legkozelebbi_tipus = None, None, None
            for j in fa.query(sugar, predicate='intersects'):
                if j == i:
                    continue   # a saját darabot nem számítom
                metszet = sugar.intersection(geomok[j])
                for pont in shapely.get_parts(metszet):
                    # vonal-metszet (egybeeső szakasz) esetén a hozzánk legközelebbi pontja számít
                    if pont.geom_type != 'Point':
                        pont = shapely.ops.nearest_points(kiindulo, pont)[1]
                    tav = kiindulo.distance(pont)
                    if tav <= P.NARANCS_SAJAT_TAVOLSAG:
                        continue
                    if legkozelebbi_tav is None or tav < legkozelebbi_tav:
                        legkozelebbi, legkozelebbi_tav, legkozelebbi_tipus = pont, tav, tipusok[j]
            kiindulok.append(kiindulo)
            kiindulo_rangok.append(rangok[i])
            if legkozelebbi is None:
                print(f'  [1.5] zsákutca-végnek nincs találata (nem várt): {kiindulo.wkt}')
                continue
            narancsok.append(LineString([vegpont, shapely.get_coordinates(legkozelebbi)[0]]))
            talalatok.append(legkozelebbi)
            hosszak.append(legkozelebbi_tav)
            talalat_tipusok.append(legkozelebbi_tipus)

    narancs_tabla = gpd.GeoDataFrame({
        'tipus': [P.TIPUS_SEGEDVONAL] * len(narancsok),
        'rang': [P.RANG_SEGEDVONAL] * len(narancsok),
        'utca_id': [None] * len(narancsok),
    }, geometry=narancsok, crs=darabok.crs)

    debug.reteg('1_5_zsakutca_vegek', '1.5', 'zsákutca-vég', kiindulok, P.SZIN['narancs'], rang=kiindulo_rangok)
    debug.reteg('1_5_narancs', '1.5', 'narancs segédvonal', narancsok, P.SZIN['narancs'],
                hossz=hosszak, talalat_tipus=talalat_tipusok)
    debug.reteg('1_5_talalatok', '1.5', 'narancs találat', talalatok, P.SZIN['narancs'])
    return narancs_tabla


def _logo_vegek_torlese(darabok, debug):
    """1.7 ISMÉTELD: poligonok, lógó, cut, érvénytelen ← polygonize_full(darabok) név szerint;
    HA lógó üres ÉS cut üres → KÉSZ; darabok ← darabok − lógó − cut. A ciklus konvergál, mert
    minden körben fogy a vonal. Egy darab akkor tartozik egy lógó/cut vonalhoz, ha a felezőpontja
    rajta ül (RÁCS-on belül)."""
    torolt_geomok, torolt_okok, torolt_korok = [], [], []
    kor = 0
    while True:
        kor += 1
        eredmeny = T.polygonize_full_nev_szerint(list(darabok.geometry))
        logo, cut = eredmeny['logo'], eredmeny['cut']
        if not logo and not cut:
            break
        torlo_geomok = np.array(logo + cut, dtype=object)
        torlo_okok = ['lógó'] * len(logo) + ['cut'] * len(cut)
        fa = shapely.STRtree(torlo_geomok)
        felezok = np.array([T.felezopont(g) for g in darabok.geometry], dtype=object)
        darab_idx, torlo_idx = fa.query(felezok, predicate='dwithin', distance=P.RACS)
        if len(darab_idx) == 0:
            break   # védelem: nincs megfeleltethető darab (nem várt)
        ok_darabonkent = {}
        for d, t in zip(darab_idx, torlo_idx):
            ok_darabonkent.setdefault(d, torlo_okok[t])
        torlendo = sorted(ok_darabonkent)
        torolt_geomok.extend(darabok.geometry.iloc[torlendo].tolist())
        torolt_okok.extend([ok_darabonkent[d] for d in torlendo])
        torolt_korok.extend([kor] * len(torlendo))
        darabok = darabok.drop(darabok.index[torlendo]).reset_index(drop=True)
    debug.reteg('1_7_logo_torolt', '1.7', 'törölt lógó/cut darab', torolt_geomok,
                P.SZIN['törölt vonal'], ok=torolt_okok, kor=torolt_korok)
    return darabok, len(torolt_geomok)


def utca_vonalak_epites(tabla):
    """{utca_id: [LineString, ...]}: ugyanannak az utcának a vonalháló-darabjai komponensenként
    összefűzve (linemerge). Ez az U vonal a 8.1 vetítéshez, a 8.3 párhuzamoshoz és a 9.1
    folytonossághoz; az utcák a folyamat során nem mozdulnak, ezért egyszer számoljuk."""
    utca_vonalak = {}
    utcak = tabla[(tabla['tipus'] == P.TIPUS_UTCA) & tabla['utca_id'].notna()]
    for utca_id, csoport in utcak.groupby('utca_id'):
        osszefuzve = linemerge(list(csoport.geometry))
        utca_vonalak[utca_id] = T.vonalak_darabokra(osszefuzve)
    return utca_vonalak


def lepes_1_vonalhalo(foltok, vonalak_vagott, debug):
    """1. lépés – zárt vonalháló, amelynek minden hurka egy cella, egyetlen vonalvég sem lóg,
    és minden vonaldarab tudja, honnan jött."""
    crs = vonalak_vagott.crs

    # 1.1 A vonalak összegyűjtése: lakott határ (rang 7) + utcák (rang 3/4/5) + vasút, folyó (rang 6)
    hatar_tabla = gpd.GeoDataFrame({
        'tipus': [P.TIPUS_LAKOTT_HATAR] * len(foltok.hatar_vonalak),
        'rang': [P.RANG_LAKOTT_HATAR] * len(foltok.hatar_vonalak),
        'utca_id': [None] * len(foltok.hatar_vonalak),
    }, geometry=foltok.hatar_vonalak, crs=crs)
    vonalak = pd.concat([hatar_tabla, vonalak_vagott[['tipus', 'rang', 'utca_id', 'geometry']]],
                        ignore_index=True)
    vonalak = gpd.GeoDataFrame(vonalak, geometry='geometry', crs=crs)
    vonalak['vonal_id'] = np.arange(len(vonalak))
    debug.reteg('1_1_hatar', '1.1', 'lakott határ', foltok.hatar_vonalak, P.SZIN['lakott határ'])
    if vonalak.empty:
        # nincs hálózatos folt (pl. nincs utcahálózat a cache-ben): nincs vonalháló, a 2. lépésben
        # csak az utca nélküli foltok lesznek cellák
        ures = gpd.GeoDataFrame(columns=['vonal_id', 'tipus', 'rang', 'utca_id', 'geometry'], geometry='geometry', crs=crs)
        return Vonalhalo(tabla=ures, utca_vonalak={})

    # 1.2 Kerekítés rácsra – az egyetlen hely, ahol kerekítés történik. A lakott területre vágott
    #     utcavégek a határ szakaszán ülnek, de a külön-külön kerekítés után legfeljebb egy rácsnyit
    #     (1 cm) eltérhetnek tőle; ezeket a csúcsokat RÁCS-on belül a határhoz illesztjük (snap),
    #     hogy az összefésülés biztosan csomópontot adjon – ez a kerekítés része, vonal nem mozdul.
    vonalak['geometry'] = shapely.set_precision(np.asarray(vonalak.geometry.values, dtype=object), P.RACS)
    hatar_unio = shapely.unary_union(np.array(foltok.hatar_vonalak, dtype=object))
    nem_hatar = vonalak['tipus'] != P.TIPUS_LAKOTT_HATAR
    vonalak.loc[nem_hatar, 'geometry'] = [shapely.snap(g, hatar_unio, P.RACS) for g in vonalak.loc[nem_hatar, 'geometry']]
    vonalak = vonalak.explode(index_parts=False)
    vonalak = vonalak[(vonalak.geometry.geom_type == 'LineString') & (vonalak.geometry.length > 0)]
    vonalak = vonalak.reset_index(drop=True)

    # 1.3 Első összefésülés és attribútumok
    darabok = _osszefesules(vonalak)
    # a vonal_id is visszakereshető a felezőpont alapján (debug rétegnek)
    vonal_id_tabla = vonalak.drop(columns=['utca_id']).rename(columns={'vonal_id': 'utca_id'})
    vonal_id_tabla = vonal_id_tabla[['tipus', 'rang', 'utca_id', 'geometry']]
    vonal_idk = T.attributum_visszaillesztes(list(darabok.geometry), vonal_id_tabla)['utca_id']
    debug.reteg('1_3_darabok', '1.3', 'darab az első összefésülés után', darabok.geometry,
                [P.rang_szin(r, t) for r, t in zip(darabok['rang'], darabok['tipus'])],
                rang=darabok['rang'].tolist(), utca_id=darabok['utca_id'].tolist(), vonal_id=vonal_idk.tolist())

    # 1.4 Rang 3 lógó végek törlése
    darabok, n_rang3_torolt = _rang3_logo_vegek_torlese(darabok, debug)

    # 1.5 Zsákutcák lezárása – narancs segédvonal
    narancs = _narancs_vonalak(darabok, debug)

    # 1.6 Második összefésülés
    egyutt = gpd.GeoDataFrame(pd.concat([darabok, narancs], ignore_index=True), geometry='geometry', crs=crs)
    darabok = _osszefesules(egyutt)
    debug.reteg('1_6_darabok', '1.6', 'darab a második összefésülés után', darabok.geometry,
                [P.rang_szin(r, t) for r, t in zip(darabok['rang'], darabok['tipus'])],
                rang=darabok['rang'].tolist(), utca_id=darabok['utca_id'].tolist())

    # 1.7 Lógó végek törlése
    darabok, n_logo_torolt = _logo_vegek_torlese(darabok, debug)

    tabla = darabok.reset_index(drop=True)
    tabla['vonal_id'] = np.arange(len(tabla))
    tabla = tabla[['vonal_id', 'tipus', 'rang', 'utca_id', 'geometry']]
    debug.reteg('1_7_vonalhalo', '1.7', 'kész vonalháló', tabla.geometry,
                [P.rang_szin(r, t) for r, t in zip(tabla['rang'], tabla['tipus'])],
                rang=tabla['rang'].tolist(), utca_id=tabla['utca_id'].tolist(), tipus_vonal=tabla['tipus'].tolist())

    return Vonalhalo(tabla=tabla, utca_vonalak=utca_vonalak_epites(tabla),
                     n_rang3_torolt=n_rang3_torolt, n_narancs=len(narancs), n_logo_torolt=n_logo_torolt)


# ──────────────────────────────────────────────────────────────────────────────
# 2. lépés – Cellák képzése
# ──────────────────────────────────────────────────────────────────────────────

def lepes_2_cellak(vonalhalo, foltok, cimek, debug):
    """2.1 poligonizálás (HA lógó ≠ ∅ VAGY érvénytelen ≠ ∅ → HIBA, a település leáll);
    2.2 utca nélküli foltok egy-egy cellaként; 2.3 éltábla és címek (R. rutin 4–5. pontja).
    Innentől vonalhálót nem építünk: minden további művelet poligon-művelet.
    Visszatérés: (cellak, eltabla, cimek)."""
    crs = vonalhalo.tabla.crs

    # 2.1 Poligonizálás (üres vonalhálónál nincs tömb, csak foltcellák)
    if vonalhalo.tabla.empty:
        eredmeny = {'poligonok': [], 'logo': [], 'cut': [], 'ervenytelen': []}
    else:
        eredmeny = T.polygonize_full_nev_szerint(list(vonalhalo.tabla.geometry))
    if eredmeny['logo'] or eredmeny['ervenytelen']:
        raise RuntimeError(f"2.1 poligonizálás: {len(eredmeny['logo'])} lógó és "
                           f"{len(eredmeny['ervenytelen'])} érvénytelen vonal maradt a vonalhálóban")
    # a lakott terület lyukaiban keletkező lapok nem cellák (terv #6, 0.6/3. garancia)
    lapok = [lap for lap in eredmeny['poligonok']
             if foltok.lakott_terulet.contains(lap.representative_point())]
    sorok = [{'cella_id': i, 'cellatipus': P.CELLA_TOMB, 'geometry': lap} for i, lap in enumerate(lapok)]
    tombok = T.uj_cellatabla(sorok, crs) if sorok else T.uj_cellatabla(
        [{'cella_id': 0, 'cellatipus': P.CELLA_TOMB, 'geometry': shapely.Polygon()}], crs).iloc[0:0]
    debug.cellak('2_1_tombok', '2.1', tombok)

    # 2.2 Utca nélküli foltok, egy-egy cellaként (RÁCS-ra kerekítve, mint minden vonal)
    folt_geomok = []
    for folt in foltok.utca_nelkuli_foltok:
        folt_geomok.extend(T.poligonok_darabokra(shapely.set_precision(folt, P.RACS)))
    elso_id = len(sorok)
    folt_sorok = [{'cella_id': elso_id + i, 'cellatipus': P.CELLA_FOLT, 'geometry': g}
                  for i, g in enumerate(folt_geomok)]
    cellak = T.uj_cellatabla(sorok + folt_sorok, crs)
    if folt_sorok:
        debug.cellak('2_2_foltcellak', '2.2', T.uj_cellatabla(folt_sorok, crs))

    # 2.3 Éltábla és címek
    vonalak = list(vonalhalo.tabla.geometry)
    attributumok = vonalhalo.tabla[['tipus', 'rang', 'utca_id']].reset_index(drop=True)
    # a foltcellák határa is bekerül az éltáblába (rang 7 lakott határ, mindkét oldalon külvilág/folt)
    for folt_sor in folt_sorok:
        for gyuru in T.vonalak_darabokra(folt_sor['geometry'].boundary):
            vonalak.append(gyuru)
            attributumok = pd.concat([attributumok, pd.DataFrame(
                [{'tipus': P.TIPUS_LAKOTT_HATAR, 'rang': P.RANG_LAKOTT_HATAR, 'utca_id': None}])],
                ignore_index=True)
    eltabla = T.eltabla_epites(vonalak, attributumok, cellak)
    cimek = T.cimek_cellaba(cimek, cellak)

    debug.eltabla('2_3_eltabla', '2.3', eltabla)
    debug.reteg('2_3_cimek', '2.3', 'címpont', cimek.geometry,
                [debug.kategoria_szin(s) for s in cimek['szkid']],
                szkid=cimek['szkid'].tolist(), cella_id=cimek['cella_id'].tolist())
    return cellak, eltabla, cimek
