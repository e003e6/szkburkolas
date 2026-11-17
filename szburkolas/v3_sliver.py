"""
v3_sliver – 3. lépés: sliver-tisztítás.

Leírás: 3.1 felismerés, 3.2 T-feldarabolás, 3.3 beolvasztás, 3.4 ismert korlát (V-alak).

Cél: a telekszélesség alatti, cím nélküli cellák eltüntetése, mielőtt a sávosítás
eltorzulna tőlük. Címes cella soha nem olvad be ebben a lépésben, bármilyen alakú.
"""

import shapely

import v3_param as P
import v3_topologia as T
from v3_io import nyil


def beirt_atmero(cella_geom):
    """A cella legnagyobb beírt körének átmérője (shapely.maximum_inscribed_circle; az eredmény
    a középpontból a legközelebbi határpontba húzott szakasz, hossza a sugár)."""
    if cella_geom.is_empty or cella_geom.area == 0:
        return 0.0
    return 2.0 * shapely.maximum_inscribed_circle(cella_geom).length


def lepes_3_1_sliver_felismeres(cellak, cimek, debug):
    """3.1 MINDEN cellára: SLIVER, HA 2·r < TELEKSZÉLESSÉG ÉS a cellában nincs címpont.
    A `sliver` oszlopot állítja. Visszatérés: cellak."""
    cellak = cellak.copy()
    kategoriak = T.cella_kategoriak(cimek)
    sliver = []
    atmerok = []
    for sor in cellak.itertuples(index=False):
        atmero = beirt_atmero(sor.geometry)
        atmerok.append(atmero)
        sliver.append(atmero < P.TELEKSZELESSEG and sor.cella_id not in kategoriak)
    cellak['sliver'] = sliver
    sliverek = cellak[cellak['sliver']]
    debug.reteg('3_1_sliverek', '3.1', 'sliver', sliverek.geometry, P.SZIN['cella sliver'],
                cella_id=sliverek['cella_id'].tolist(),
                beirt_atmero=[a for a, s in zip(atmerok, sliver) if s])
    return cellak


def lepes_3_2_t_feldarabolas(cellak, eltabla, debug):
    """3.2 MINDEN sliverRE: elvágjuk minden T-csomópontjánál (a határra merőlegesen, az első
    kilépésig). Így minden darab egy tömb hosszú, és csak a saját tömbjébe tud beolvadni."""
    sliver_idk = set(cellak.loc[cellak['sliver'], 'cella_id'])
    cellak, t_pontok, t_vonalak = T.t_feldarabolas(cellak, eltabla, sliver_idk)
    debug.reteg('3_2_T_csomopontok', '3.2', 'T-csomópont', t_pontok, P.SZIN['T-vágás'])
    debug.reteg('3_2_T_vagasok', '3.2', 'T-vágóvonal', t_vonalak, P.SZIN['T-vágás'])
    return cellak


def lepes_3_3_beolvasztas(cellak, eltabla, cimek, debug):
    """3.3 ISMÉTELD, AMÍG van sliver-darab: minden sliver-darab a legkisebb rangú közös élen át
    (egyenlő rangnál a leghosszabbon) beolvad a nem-sliver szomszédjába; körönként R. rutin.
    HA egy körben semmi nem olvadt be → a maradék cím nélküli cellaként marad (6. lépés kezeli).
    Visszatérés: (cellak, eltabla, cimek)."""
    nyilak, nyertes_rangok, kozos_hosszak = [], [], []
    while True:
        sliverek = cellak[cellak['sliver']]
        if sliverek.empty:
            break
        nem_sliver_idk = set(cellak.loc[~cellak['sliver'], 'cella_id'])
        geom_szerint = dict(zip(cellak['cella_id'], cellak['geometry']))
        beolvadt = set()
        for sliver in sliverek.itertuples(index=False):
            jeloltek = T.szomszedok_osszesitve(eltabla, sliver.cella_id, csak_cellak=nem_sliver_idk)
            nyertes = T.nyertes_szomszed(jeloltek)
            if nyertes is None:
                continue   # csak sliver szomszédai vannak: kihagyom ebben a körben
            nyertes_id, rang, hossz = nyertes
            nyilak.append(nyil(sliver.geometry, geom_szerint[nyertes_id]))
            nyertes_rangok.append(rang)
            kozos_hosszak.append(hossz)
            geom_szerint[nyertes_id] = shapely.unary_union([geom_szerint[nyertes_id], sliver.geometry])
            beolvadt.add(sliver.cella_id)
        if not beolvadt:
            # a maradék sliver cím nélküli cellaként megy tovább
            cellak = cellak.copy()
            cellak['sliver'] = False
            break
        cellak = cellak[~cellak['cella_id'].isin(beolvadt)].copy()
        cellak['geometry'] = [geom_szerint[c] for c in cellak['cella_id']]
        # az unió MultiPolygon-t is adhat (pontban érintkező darabok): az R. rutin lapokra bontja
        cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek)
    debug.reteg('3_3_beolvasztas', '3.3', 'beolvasztás nyíl', nyilak, P.SZIN['nyíl'],
                nyertes_rang=nyertes_rangok, kozos_hossz=kozos_hosszak)
    return cellak, eltabla, cimek


def lepes_3_4_egycimes_haromszog_jelzes(cellak, cimek):
    """3.4 Ismert korlát: egycímes, telekszélesség alatti cella (a V-alakú csonk háromszöge, amelynek
    végében egy cím van). Nem sliver (van címe), de a kimenetben jelezni kell."""
    cellak = cellak.copy()
    kategoriak = T.cella_kategoriak(cimek)
    jelzesek = []
    for sor in cellak.itertuples(index=False):
        szamlalo = kategoriak.get(sor.cella_id)
        if szamlalo is not None and sum(szamlalo.values()) == 1 and beirt_atmero(sor.geometry) < P.TELEKSZELESSEG:
            jelzesek.append(_jelzes_hozzaad(sor.jelzes, P.JELZES_HAROMSZOG))
        else:
            jelzesek.append(sor.jelzes)
    cellak['jelzes'] = jelzesek
    return cellak


def _jelzes_hozzaad(meglevo, uj):
    """Jelzés hozzáfűzése ';'-vel, ismétlés nélkül."""
    reszek = [r for r in (meglevo or '').split(';') if r]
    if uj not in reszek:
        reszek.append(uj)
    return ';'.join(reszek)


def lepes_3_sliver_tisztitas(cellak, eltabla, cimek, debug):
    """3. lépés – sliver-tisztítás: felismerés, T-feldarabolás, beolvasztás, R. rutin, jelzés."""
    cellak = lepes_3_1_sliver_felismeres(cellak, cimek, debug)
    cellak = lepes_3_2_t_feldarabolas(cellak, eltabla, debug)
    # a T-feldarabolás új darabjai után az éltáblát frissítjük, hogy a beolvasztás a darabokat lássa
    cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek)
    cellak, eltabla, cimek = lepes_3_3_beolvasztas(cellak, eltabla, cimek, debug)
    cellak = lepes_3_4_egycimes_haromszog_jelzes(cellak, cimek)
    debug.cellak('3_R_cellak', '3.R', cellak)
    return cellak, eltabla, cimek
