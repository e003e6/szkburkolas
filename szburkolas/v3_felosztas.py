"""
v3_felosztas – 7. lépés (besorolás) és 8. lépés (vegyes cellák felosztása).

Leírás: 7., 8.1–8.5.

8. Cél: a vegyes cellát a besorolás megőrzésével, egyenes vonalakkal darabolni. A Voronoi csak
végső eset. Sávnál: 8.1 vetítés az utca mentén + merőleges vágás (8.2 egyeztetve a szemközti
sávval), 8.3 vetítés az utcától mért távolság szerint + párhuzamos vágás, 8.4 Voronoi a keveredő
szakaszon. Sarokcellánál, tömbnél és utca nélküli foltnál: Voronoi a teljes cellára.
"""

from collections import Counter

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point
from shapely.ops import linemerge

import v3_param as P
import v3_topologia as T


def jelzes_hozzaad(meglevo, uj):
    """Jelzés hozzáfűzése ';'-vel, ismétlés nélkül."""
    reszek = [r for r in (meglevo or '').split(';') if r]
    if uj not in reszek:
        reszek.append(uj)
    return ';'.join(reszek)


# ──────────────────────────────────────────────────────────────────────────────
# 7. lépés – Besorolás
# ──────────────────────────────────────────────────────────────────────────────

def azonos_pont_jelzes(cimek, cella_idk):
    """Terv #7: egy koordinátán több cím eltérő kategóriával (az A-fázis több-a-többhez
    párosítása). Ilyen pont nem választható szét semmilyen vágással; a ponton a többségi
    kategória marad, a kisebbségi címek 'azonos-pont' jelzést kapnak és a besorolásban nem
    számítanak. Csak a megadott (vegyes) cellák címeit vizsgálja. Visszatérés: (cimek, db)."""
    cimek = cimek.copy()
    db = 0
    erintett = cimek[cimek['cella_id'].isin(cella_idk)]
    kulcs = erintett.geometry.apply(lambda g: (round(g.x / P.RACS), round(g.y / P.RACS)))
    for _, csoport in erintett.groupby(kulcs):
        kategoriak = Counter(csoport['szkid'])
        if len(kategoriak) < 2:
            continue
        tobbsegi = min(kategoriak.items(), key=lambda t: (-t[1], t[0]))[0]
        kisebbsegi_idx = csoport.index[csoport['szkid'] != tobbsegi]
        for i in kisebbsegi_idx:
            cimek.at[i, 'jelzes'] = jelzes_hozzaad(cimek.at[i, 'jelzes'], P.JELZES_AZONOS_PONT)
        db += len(kisebbsegi_idx)
    return cimek, db


def lepes_7_besorolas(cellak, cimek, debug):
    """7. MINDEN cellára: 0 cím → ÜRES; minden cím egy kategóriájú → a cella azé a kategóriáé
    (szabály: címes); több kategória → VEGYES → 8. lépés.
    Visszatérés: (cellak, cimek, vegyes cella_id-k listája, n_azonos_pont)."""
    cellak = cellak.copy()
    kategoriak = T.cella_kategoriak(cimek)
    vegyes_elso = [c for c, k in kategoriak.items() if len(k) > 1]
    cimek, n_azonos_pont = azonos_pont_jelzes(cimek, set(vegyes_elso))
    if n_azonos_pont:
        kategoriak = T.cella_kategoriak(cimek)

    allapotok, szkidk, szabalyok, vegyes = [], [], [], []
    for sor in cellak.itertuples(index=False):
        k = kategoriak.get(sor.cella_id)
        if not k:
            allapotok.append(P.ALLAPOT_URES); szkidk.append(None); szabalyok.append(None)
        elif len(k) == 1:
            allapotok.append(P.ALLAPOT_EGYKATEGORIAS); szkidk.append(next(iter(k))); szabalyok.append(P.SZABALY_CIMES)
        else:
            allapotok.append(P.ALLAPOT_VEGYES); szkidk.append(None); szabalyok.append(None)
            vegyes.append(sor.cella_id)
    cellak['szkid'] = szkidk
    cellak['szabaly'] = szabalyok
    debug.cellak('7_besorolas', '7', cellak, allapot=allapotok, allapot_=allapotok)
    return cellak, cimek, vegyes, n_azonos_pont


# ──────────────────────────────────────────────────────────────────────────────
# Közös segédfüggvények a 8. lépéshez
# ──────────────────────────────────────────────────────────────────────────────

def futamok(kategoriak_sorban):
    """Egy rendezett kategória-sor futamai: [(kategória, első index, utolsó index)]."""
    eredmeny = []
    for i, k in enumerate(kategoriak_sorban):
        if eredmeny and eredmeny[-1][0] == k:
            eredmeny[-1] = (k, eredmeny[-1][1], i)
        else:
            eredmeny.append((k, i, i))
    return eredmeny


def tiszta(futam_lista):
    """TISZTA, HA minden kategória egyetlen összefüggő futamot alkot (A A B B: igen; A B A: nem)."""
    return len(set(k for k, _, _ in futam_lista)) == len(futam_lista)


def erinto_irany(U, t):
    """Az U vonal érintő-irányvektora a t méterjelnél: a t ± ERINTO_FELHOSSZ pontok iránya
    (görbe utcánál is egyértelmű)."""
    t0, t1 = max(0.0, t - P.ERINTO_FELHOSSZ), min(U.length, t + P.ERINTO_FELHOSSZ)
    a = shapely.get_coordinates(U.interpolate(t0))[0]
    b = shapely.get_coordinates(U.interpolate(t1))[0]
    irany = b - a
    hossz = np.hypot(*irany)
    return irany / hossz if hossz > 0 else np.array([1.0, 0.0])


def meroleges_egyenes(U, t, hossz):
    """Az U-ra a t méterjelnél merőleges, mindkét irányban `hossz` hosszú egyenes-szakasz."""
    pont = shapely.get_coordinates(U.interpolate(t))[0]
    n = T.normalvektor(erinto_irany(U, t))
    return LineString([pont - n * hossz, pont + n * hossz])


def vago_a_cellaban(egyenes, cella_geom, P_pont):
    """Egy egyenes cellán belüli darabja a P ponthoz legközelebb (a sávból való kilépésig),
    mindkét végén túlnyúlással. None, ha az egyenes nem metszi a cellát."""
    darabok = T.vonalak_darabokra(egyenes.intersection(cella_geom))
    if not darabok:
        return None
    legkozelebbi = min(darabok, key=lambda d: d.distance(P_pont))
    return T.hosszabbit(legkozelebbi, P.VAGOVONAL_TULNYULAS)


def cella_atlo(cella_geom):
    b = cella_geom.bounds
    return np.hypot(b[2] - b[0], b[3] - b[1]) + 1.0


def darabok_kategoriai(darabok, cimek_cella):
    """Minden darabhoz a benne lévő (számító) címek kategória-halmaza."""
    pontok = np.asarray(cimek_cella.geometry.values, dtype=object)
    kategoriak = cimek_cella['szkid'].to_numpy()
    eredmeny = [set() for _ in darabok]
    if len(pontok) == 0:
        return eredmeny
    fa = shapely.STRtree(pontok)
    for di, darab in enumerate(darabok):
        for pi in fa.query(darab, predicate='contains'):
            eredmeny[di].add(int(kategoriak[pi]))
    return eredmeny


def ellenorzes(darabok, cimek_cella):
    """ELLENŐRZÉS: minden darabban legfeljebb egy kategória?"""
    return all(len(k) <= 1 for k in darabok_kategoriai(darabok, cimek_cella))


def t_csomopontok_utcan(U, index):
    """Az U vonalon ülő T-csomópontok (ahol másik vonal végződik vagy keresztez: ≥3 él) t értékei."""
    t_lista = []
    if not index:
        return t_lista
    pontok = np.array([Point(c) for c, elek in index.items() if len(elek) >= 3], dtype=object)
    if len(pontok) == 0:
        return t_lista
    fa = shapely.STRtree(pontok)
    for pi in fa.query(U, predicate='dwithin', distance=P.RACS):
        t_lista.append(U.project(pontok[pi]))
    return sorted(t_lista)


class Gyujto:
    """A 8. lépés debug-geometriáinak és statisztikáinak gyűjtője."""

    def __init__(self):
        self.vetitesek, self.vetitesek_t, self.vetitesek_szkid = [], [], []
        self.sorok, self.sorok_t, self.sorok_futam, self.sorok_tiszta = [], [], [], []
        self.vagas_T, self.vagas_T_t, self.vagas_T_utca = [], [], []
        self.vagas_kozep, self.vagas_kozep_t = [], []
        self.kozos, self.kozos_v, self.kozos_szog = [], [], []
        self.parhuzamos, self.parhuzamos_d = [], []
        self.voronoi_sejtek, self.voronoi_sejtek_szkid = [], []
        self.voronoi_nyers = []
        self.voronoi_vagas, self.voronoi_vagas_t = [], []
        self.vagas_tipus = Counter()


# ──────────────────────────────────────────────────────────────────────────────
# 8.1 Sáv: a címek sorba rendezése az utca mentén, és merőleges vágás
# ──────────────────────────────────────────────────────────────────────────────

def _koz_vagas_t(koz, t_jeloltek):
    """Egy köz (nyílt intervallum) vágási helye: a felezőponthoz legközelebbi T-jelölt, ha a közbe
    esik; különben a köz közepe. Visszatérés: (t_vágás, 'T' | 'közép')."""
    lo, hi = koz
    kozep = (lo + hi) / 2.0
    bent = [t for t in t_jeloltek if lo < t < hi]
    if bent:
        return min(bent, key=lambda t: abs(t - kozep)), 'T'
    return kozep, 'közép'


def _egy_koz_vagovonala(sav_geom, U, koz, t_jeloltek, cimek_cella, elotte_idx, utana_idx, gy):
    """Egy futam-határ vágóvonala: először a T-jelölt / köz közepe, ha a vágás nem választja el
    tisztán a két oldalt (görbe utca), a köz negyedelő pontjai. Visszatérés: (vágóvonal, típus)
    vagy None."""
    lo, hi = koz
    elso_t, elso_tipus = _koz_vagas_t(koz, t_jeloltek)
    probak = [(elso_t, elso_tipus), (lo + (hi - lo) / 4.0, 'közép'), (lo + 3 * (hi - lo) / 4.0, 'közép')]
    atlo = cella_atlo(sav_geom)
    for t_vagas, tipus in probak:
        P_pont = U.interpolate(t_vagas)
        vago = vago_a_cellaban(meroleges_egyenes(U, t_vagas, atlo), sav_geom, P_pont)
        if vago is None:
            continue
        darabok = T.cella_felosztas(sav_geom, [vago])
        if len(darabok) < 2:
            continue
        # a vágás előtti és utáni címek más-más darabba kerültek?
        kat = darabok_kategoriai(darabok, cimek_cella)
        elotte = cimek_cella.iloc[elotte_idx]
        utana = cimek_cella.iloc[utana_idx]
        jo = True
        for darab in darabok:
            e = elotte.geometry.apply(darab.contains).sum()
            u = utana.geometry.apply(darab.contains).sum()
            if e > 0 and u > 0:
                jo = False
                break
        if jo:
            return vago, tipus
    return None


def vagas_8_1(sav_geom, U, cimek_cella, t_jeloltek, gy):
    """8.1: t ← U.project(cím); sor ← t szerint rendezve. HA TISZTA: minden szomszédos futam-pár
    közé merőleges vágóvonal. Visszatérés: (darabok, vágóvonalak, típusok) vagy None (nem tiszta,
    vagy a vágás nem tudta elválasztani a futamokat → 8.3 / 8.4)."""
    t = cimek_cella.geometry.apply(U.project).to_numpy()
    sorrend = np.argsort(t, kind='stable')
    rendezett = cimek_cella.iloc[sorrend]
    futam_lista = futamok(rendezett['szkid'].tolist())
    tiszta_e = tiszta(futam_lista)
    # debug: vetítések és a sor
    for sor_i, (i, cim) in enumerate(rendezett.iterrows()):
        talp = U.interpolate(t[sorrend[sor_i]])
        gy.vetitesek.append(LineString([cim.geometry, talp])); gy.vetitesek_t.append(float(t[sorrend[sor_i]]))
        gy.vetitesek_szkid.append(int(cim.szkid))
        gy.sorok.append(cim.geometry); gy.sorok_t.append(float(t[sorrend[sor_i]]))
        gy.sorok_futam.append(next(f for f, (_, a, b) in enumerate(futam_lista) if a <= sor_i <= b))
        gy.sorok_tiszta.append(bool(tiszta_e))
    if not tiszta_e:
        return None
    vagok, tipusok = [], []
    for (k1, a1, b1), (k2, a2, b2) in zip(futam_lista, futam_lista[1:]):
        koz = (float(t[sorrend[b1]]), float(t[sorrend[a2]]))
        eredmeny = _egy_koz_vagovonala(sav_geom, U, koz, t_jeloltek, cimek_cella,
                                       sorrend[:b1 + 1], sorrend[a2:], gy)
        if eredmeny is None:
            return None
        vagok.append(eredmeny[0]); tipusok.append(eredmeny[1])
    darabok = T.cella_felosztas(sav_geom, vagok)
    if not ellenorzes(darabok, cimek_cella):
        return None
    return darabok, vagok, tipusok


# ──────────────────────────────────────────────────────────────────────────────
# 8.2 Egyeztetés a szemközti sávval
# ──────────────────────────────────────────────────────────────────────────────

def _futam_koz(U, cimek_cella):
    """Egy sáv címeinek t-sora; HA tiszta és pontosan két futam → (köz, {K1, K2}, sorrend) különben None."""
    t = cimek_cella.geometry.apply(U.project).to_numpy()
    sorrend = np.argsort(t, kind='stable')
    futam_lista = futamok(cimek_cella.iloc[sorrend]['szkid'].tolist())
    if len(futam_lista) != 2 or not tiszta(futam_lista):
        return None
    koz = (float(t[sorrend[futam_lista[0][2]]]), float(t[sorrend[futam_lista[1][1]]]))
    return koz, frozenset(k for k, _, _ in futam_lista), sorrend


def _meroleges_a_vazvonalon(U, t, vazvonal, atlo):
    """Az U t-jelénél húzott merőleges hol metszi a vázvonalat: a vázvonal menti helye (project) vagy None."""
    metszet = meroleges_egyenes(U, t, atlo).intersection(vazvonal)
    if metszet.is_empty:
        return None
    pont = metszet if metszet.geom_type == 'Point' else min(shapely.get_parts(metszet), key=lambda g: g.distance(U.interpolate(t)))
    if pont.geom_type != 'Point':
        pont = pont.representative_point()
    return float(vazvonal.project(pont))


def vagas_8_2(sav1, U1, cimek1, sav2, U2, cimek2, vazvonal, t_jeloltek1, t_jeloltek2, gy):
    """8.2: HA a szemközti sáv is vegyes, és ugyanaz a két kategória között van futam-határa, és a
    két köz vázvonalra vetített intervalluma (I1, I2) átfed: közös vonal a vázvonal V pontjából a
    két utcára bocsátott merőlegesekkel (párhuzamos utcáknál egy egyenes, különben V-ben törik).
    Visszatérés: ((darabok1, vágás1), (darabok2, vágás2), v, törésszög) vagy None (→ lépcső, 8.1)."""
    e1, e2 = _futam_koz(U1, cimek1), _futam_koz(U2, cimek2)
    if e1 is None or e2 is None or e1[1] != e2[1]:
        return None
    atlo = max(cella_atlo(sav1), cella_atlo(sav2))
    I1 = [_meroleges_a_vazvonalon(U1, t, vazvonal, atlo) for t in e1[0]]
    I2 = [_meroleges_a_vazvonalon(U2, t, vazvonal, atlo) for t in e2[0]]
    if any(v is None for v in I1 + I2):
        return None
    lo, hi = max(min(I1), min(I2)), min(max(I1), max(I2))
    if lo > hi:
        return None
    # T-jelöltek: bármelyik utca T-csomópontja, amelynek merőlegese az átfedésbe metszi a vázvonalat
    kozep = (lo + hi) / 2.0
    jeloltek = []
    for U, t_lista in ((U1, t_jeloltek1), (U2, t_jeloltek2)):
        for t in t_lista:
            v = _meroleges_a_vazvonalon(U, t, vazvonal, atlo)
            if v is not None and lo <= v <= hi:
                jeloltek.append(v)
    v = min(jeloltek, key=lambda x: abs(x - kozep)) if jeloltek else kozep
    V = vazvonal.interpolate(v)
    eredmenyek, iranyok = [], []
    for sav, U, cimek_cella in ((sav1, U1, cimek1), (sav2, U2, cimek2)):
        talp = U.interpolate(U.project(V))
        if talp.distance(V) <= P.RACS:
            return None
        vago = T.hosszabbit(LineString([V, talp]), P.VAGOVONAL_TULNYULAS)
        darabok = T.cella_felosztas(sav, [vago])
        if len(darabok) < 2 or not ellenorzes(darabok, cimek_cella):
            return None
        eredmenyek.append((darabok, vago))
        d = shapely.get_coordinates(talp)[0] - shapely.get_coordinates(V)[0]
        iranyok.append(d / np.hypot(*d))
    szog = float(np.degrees(np.arccos(np.clip(np.dot(iranyok[0], iranyok[1]), -1.0, 1.0))))
    return eredmenyek[0], eredmenyek[1], v, szog


# ──────────────────────────────────────────────────────────────────────────────
# 8.3 Sáv: a címek sorba rendezése az utcától mért távolság szerint (két házsor)
# ──────────────────────────────────────────────────────────────────────────────

def vagas_8_3(sav_geom, U, cimek_cella, gy):
    """8.3: d ← U.distance(cím); sor ← d szerint rendezve; HA TISZTA: a futamok d-közének közepén
    az utcával párhuzamos vágóvonal (U.offset_curve, a sávra vágva). Visszatérés: (darabok,
    vágóvonalak, d-k) vagy None."""
    d = cimek_cella.geometry.apply(U.distance).to_numpy()
    sorrend = np.argsort(d, kind='stable')
    futam_lista = futamok(cimek_cella.iloc[sorrend]['szkid'].tolist())
    if not tiszta(futam_lista):
        return None
    vagok, d_lista = [], []
    for (k1, a1, b1), (k2, a2, b2) in zip(futam_lista, futam_lista[1:]):
        d_vagas = (float(d[sorrend[b1]]) + float(d[sorrend[a2]])) / 2.0
        # a párhuzamos az utca azon oldalán, ahol a sáv van
        gorbe = None
        for elojel in (1.0, -1.0):
            jelolt = U.offset_curve(elojel * d_vagas)
            if not jelolt.is_empty and jelolt.intersects(sav_geom):
                gorbe = jelolt
                break
        if gorbe is None:
            return None
        darabok_vonal = T.vonalak_darabokra(gorbe.intersection(sav_geom))
        if not darabok_vonal:
            return None
        vagok.extend(T.hosszabbit(v, P.VAGOVONAL_TULNYULAS) for v in darabok_vonal if len(shapely.get_coordinates(v)) >= 2)
        d_lista.extend([d_vagas] * len(darabok_vonal))
    darabok = T.cella_felosztas(sav_geom, vagok)
    if len(darabok) < 2 or not ellenorzes(darabok, cimek_cella):
        return None
    return darabok, vagok, d_lista


# ──────────────────────────────────────────────────────────────────────────────
# 8.4 Végső eset: Voronoi a keveredő szakaszon
# ──────────────────────────────────────────────────────────────────────────────

def _hatart_erinto_vegek_hosszabbitasa(vonal, hatar, hossz):
    """A vágóvonal azon végeit hosszabbítja meg `hossz`-szal, amelyek a cellahatáron ülnek."""
    koordinatak = shapely.get_coordinates(vonal)
    if len(koordinatak) < 2:
        return vonal
    eleje, vege = koordinatak[0].copy(), koordinatak[-1].copy()
    if hatar.distance(Point(eleje)) <= P.RACS:
        irany = koordinatak[0] - koordinatak[1]
        eleje = koordinatak[0] + irany / np.hypot(*irany) * hossz
    if hatar.distance(Point(vege)) <= P.RACS:
        irany = koordinatak[-1] - koordinatak[-2]
        vege = koordinatak[-1] + irany / np.hypot(*irany) * hossz
    return LineString(np.vstack([eleje, koordinatak, vege]))


def voronoi_vagas(cella_geom, cimek_cella, gy):
    """VORONOI-VÁGÁS (cella, címek):
    1. minden címpont egy mag; Voronoi, a cellára vágva; minden darab a magja kategóriáját kapja
    2. azonos kategóriájú darabok egyesítve → nyers al-cellák
    3. a különböző kategóriájú al-cellák közös határvonalai → nyers vágóháló
    4. KIEGYENESÍTÉS: a legnagyobb t tűrés bináris kereséssel (18 lépés, 0 és a cella átlója
       között), amelynél a Douglas–Peucker-rel egyszerűsített vágóvonalakkal vágott cella minden
       darabja egykategóriás (a cellahatárt érintő végek 1 m-rel meghosszabbítva)
    5. a legnagyobb érvényes t-vel vágás; cím nélküli darab a legközelebbi cím kategóriáját kapja;
       azonos kategóriájú darabok egyesítve.
    Visszatérés: [(darab, szkid)] vagy None, ha a Voronoi nem tud tiszta felosztást adni."""
    magok = np.asarray(cimek_cella.geometry.values, dtype=object)
    kategoriak = cimek_cella['szkid'].to_numpy()
    egyedi = {}
    for g, k in zip(magok, kategoriak):
        egyedi.setdefault((round(g.x / P.RACS), round(g.y / P.RACS)), (g, int(k)))
    mag_lista = [g for g, _ in egyedi.values()]
    mag_kat = [k for _, k in egyedi.values()]
    if len(set(mag_kat)) < 2:
        return [(cella_geom, mag_kat[0])] if mag_kat else None

    # 1–2. Voronoi, cellára vágva, kategóriánként egyesítve
    sejtek = shapely.voronoi_polygons(MultiPoint(mag_lista), extend_to=cella_geom, ordered=True)
    sejtek = [shapely.set_precision(s.intersection(cella_geom), P.RACS) for s in sejtek.geoms]
    for s, k in zip(sejtek, mag_kat):
        gy.voronoi_sejtek.append(s); gy.voronoi_sejtek_szkid.append(k)
    al_cellak = {}
    for k in sorted(set(mag_kat)):
        al_cellak[k] = shapely.unary_union([s for s, kk in zip(sejtek, mag_kat) if kk == k and not s.is_empty], grid_size=P.RACS)

    # 3. nyers vágóháló: a különböző kategóriájú al-cellák közös határai
    hatar = shapely.set_precision(cella_geom.boundary, P.RACS)
    vagohalo = shapely.difference(shapely.unary_union([a.boundary for a in al_cellak.values()], grid_size=P.RACS),
                                  hatar, grid_size=P.RACS)
    vonalak_nyers = T.vonalak_darabokra(vagohalo)
    if not vonalak_nyers:
        return None
    vonalak_nyers = T.vonalak_darabokra(linemerge(MultiLineString(vonalak_nyers)))
    gy.voronoi_nyers.extend(vonalak_nyers)

    def vagas(t_tures):
        """A t tűréssel egyszerűsített vágóhálóval vágott cella darabjai, vagy None ha nem zár."""
        if t_tures > 0:
            egyszeru = T.vonalak_darabokra(shapely.simplify(MultiLineString(vonalak_nyers), t_tures, preserve_topology=True))
        else:
            egyszeru = vonalak_nyers
        vagok = [_hatart_erinto_vegek_hosszabbitasa(v, hatar, P.VAGOVONAL_TULNYULAS) for v in egyszeru]
        halo = shapely.unary_union([hatar] + vagok, grid_size=P.RACS)
        lapok = [l for l in T.polygonize_full_nev_szerint(T.vonalak_darabokra(halo))['poligonok']
                 if cella_geom.contains(l.representative_point())]
        if abs(sum(l.area for l in lapok) - cella_geom.area) > max(P.LEFEDES_TURES_ABS_M2, P.LEFEDES_TURES_REL * cella_geom.area):
            return None, vagok
        return lapok, vagok

    # 4. kiegyenesítés: bináris keresés a legnagyobb érvényes tűrésre
    lo, hi = 0.0, cella_atlo(cella_geom)
    legjobb_lapok, legjobb_vagok = vagas(0.0)
    if legjobb_lapok is None or not ellenorzes(legjobb_lapok, cimek_cella):
        # t = 0 sem zár (nem várt): a nyers Voronoi al-cellák adják a felosztást
        legjobb_lapok = [d for a in al_cellak.values() for d in T.poligonok_darabokra(a)]
        legjobb_vagok = vonalak_nyers
        if not ellenorzes(legjobb_lapok, cimek_cella):
            return None
        legjobb_t = 0.0
    else:
        legjobb_t = 0.0
        for _ in range(P.VORONOI_KERESES_LEPES):
            kozep = (lo + hi) / 2.0
            lapok, vagok = vagas(kozep)
            if lapok is not None and ellenorzes(lapok, cimek_cella):
                legjobb_lapok, legjobb_vagok, legjobb_t, lo = lapok, vagok, kozep, kozep
            else:
                hi = kozep
    for v in legjobb_vagok:
        gy.voronoi_vagas.append(v); gy.voronoi_vagas_t.append(legjobb_t)

    # 5. darabok kategóriája; cím nélküli darab a legközelebbi címét
    kat_lista = darabok_kategoriai(legjobb_lapok, cimek_cella)
    fa = shapely.STRtree(magok)
    eredmeny = []
    for lap, kat in zip(legjobb_lapok, kat_lista):
        if kat:
            eredmeny.append((lap, next(iter(kat))))
        else:
            legkozelebbi = fa.query_nearest(lap.representative_point())
            eredmeny.append((lap, int(kategoriak[int(legkozelebbi[0])])))
    return eredmeny


def _elo_es_utofutam(futam_lista):
    """A sor elejének / végének leghosszabb tiszta futama: olyan futam, amelynek kategóriája
    máshol nem fordul elő. Visszatérés: (előfutam vagy None, utófutam vagy None)."""
    szamlalo = Counter(k for k, _, _ in futam_lista)
    elo = futam_lista[0] if len(futam_lista) > 2 and szamlalo[futam_lista[0][0]] == 1 else None
    uto = futam_lista[-1] if len(futam_lista) > 2 and szamlalo[futam_lista[-1][0]] == 1 else None
    if elo is not None and uto is not None and elo[0] == uto[0]:
        uto = None
    return elo, uto


def vagas_8_4_sav(sav_geom, U, cimek_cella, t_jeloltek, gy):
    """8.4 SÁVNÁL: az elő- és utófutamot merőleges vágóvonallal levágjuk (8.1 szerint), a középső,
    keveredő darabra Voronoi-vágás. Visszatérés: [(darab, szkid vagy None)], vágóvonalak."""
    t = cimek_cella.geometry.apply(U.project).to_numpy()
    sorrend = np.argsort(t, kind='stable')
    futam_lista = futamok(cimek_cella.iloc[sorrend]['szkid'].tolist())
    elo, uto = _elo_es_utofutam(futam_lista)
    vagok = []
    for futam, utana in ((elo, True), (uto, False)):
        if futam is None:
            continue
        if utana:
            koz = (float(t[sorrend[futam[2]]]), float(t[sorrend[futam[2] + 1]]))
            elotte_idx, utana_idx = sorrend[:futam[2] + 1], sorrend[futam[2] + 1:]
        else:
            koz = (float(t[sorrend[futam[1] - 1]]), float(t[sorrend[futam[1]]]))
            elotte_idx, utana_idx = sorrend[:futam[1]], sorrend[futam[1]:]
        eredmeny = _egy_koz_vagovonala(sav_geom, U, koz, t_jeloltek, cimek_cella, elotte_idx, utana_idx, gy)
        if eredmeny is not None:
            vagok.append(eredmeny[0])
            gy.vagas_tipus[P.VAGAS_T if eredmeny[1] == 'T' else P.VAGAS_KOZEP] += 1
            (gy.vagas_T if eredmeny[1] == 'T' else gy.vagas_kozep).append(eredmeny[0])
            if eredmeny[1] == 'T':
                gy.vagas_T_t.append(0.0); gy.vagas_T_utca.append(None)
            else:
                gy.vagas_kozep_t.append(0.0)
    darabok = T.cella_felosztas(sav_geom, vagok) if vagok else [sav_geom]
    eredmeny = []
    kat_lista = darabok_kategoriai(darabok, cimek_cella)
    for darab, kat in zip(darabok, kat_lista):
        if len(kat) <= 1:
            eredmeny.append((darab, next(iter(kat)) if kat else None))
            continue
        bent = cimek_cella[cimek_cella.geometry.apply(darab.contains)]
        voronoi = voronoi_vagas(darab, bent, gy)
        if voronoi is None:
            eredmeny.append((darab, None))   # a hívó kezeli (többségi kategória + jelzés)
        else:
            eredmeny.extend(voronoi)
        gy.vagas_tipus[P.VAGAS_VORONOI] += 1
    return eredmeny


# ──────────────────────────────────────────────────────────────────────────────
# 8. lépés – a vegyes cellák feldolgozása
# ──────────────────────────────────────────────────────────────────────────────

def _szemkozti_sav(eltabla, cella_id, cellak_sor):
    """A sáv szemközti sávja: ugyanaz a tömb, közös vázvonal (rang 1 él). Visszatérés:
    (szemközti cella_id, a közös vázvonal LineString) vagy (None, None)."""
    elek = T.cella_elei(eltabla, cella_id)
    elek = elek[elek['tipus'] == P.TIPUS_VAZVONAL]
    sajat = cellak_sor[cella_id]
    jeloltek = {}
    for el in elek.itertuples(index=False):
        masik = T.szomszed_id(el, cella_id)
        if masik == P.KULVILAG or masik not in cellak_sor:
            continue
        m = cellak_sor[masik]
        if m['cellatipus'] != P.CELLA_SAV or m['tomb_id'] != sajat['tomb_id'] or m['utca_id'] == sajat['utca_id']:
            continue
        jeloltek.setdefault(masik, []).append(el.geometry)
    if not jeloltek:
        return None, None
    masik = max(jeloltek, key=lambda k: sum(g.length for g in jeloltek[k]))
    vazvonal = linemerge(MultiLineString(jeloltek[masik])) if len(jeloltek[masik]) > 1 else jeloltek[masik][0]
    if vazvonal.geom_type != 'LineString':
        vazvonal = max(T.vonalak_darabokra(vazvonal), key=lambda g: g.length)
    return masik, vazvonal


def _tobbsegi(cimek_cella):
    """Többségi kategória (döntetlennél a kisebb szkid)."""
    szamlalo = Counter(cimek_cella['szkid'].tolist())
    return min(szamlalo.items(), key=lambda t: (-t[1], t[0]))[0]


def lepes_8_vegyes_felosztas(cellak, eltabla, cimek, vegyes_idk, utca_vonalak, debug):
    """8. lépés – MINDEN vegyes cellára a megfelelő felosztás; 8.5: minden darab a benne lévő címek
    kategóriáját kapja, cím nélküli darab → ÜRES; azonos kategóriájú darabok egyesülnek; R. rutin.
    Visszatérés: (cellak, eltabla, cimek, vágástípus-statisztika Counter)."""
    crs = cellak.crs
    gy = Gyujto()
    cellak_sor = {sor.cella_id: sor._asdict() for sor in cellak.itertuples(index=False)}
    szamito = T.szamito_cimek(cimek)
    index = T.vegpont_index(eltabla)
    t_jelolt_cache = {}

    def t_jeloltek_u(utca_id, idx):
        kulcs = (utca_id, int(idx))
        if kulcs not in t_jelolt_cache:
            t_jelolt_cache[kulcs] = t_csomopontok_utcan(utca_vonalak[utca_id][int(idx)], index)
        return t_jelolt_cache[kulcs]

    def U_vonal(sor):
        if sor['cellatipus'] != P.CELLA_SAV or sor['utca_id'] not in utca_vonalak or sor['utca_vonal_idx'] is None:
            return None
        return utca_vonalak[sor['utca_id']][int(sor['utca_vonal_idx'])]

    kesz = set()
    eredmenyek = {}        # cella_id → [(darab, szkid vagy None)], vagas_tipus
    vagas_tipus_cella = {}

    for cella_id in vegyes_idk:
        if cella_id in kesz or cella_id not in cellak_sor:
            continue
        sor = cellak_sor[cella_id]
        cimek_cella = szamito[szamito['cella_id'] == cella_id]
        U = U_vonal(sor)
        felosztas = None
        tipus = None

        if U is not None:
            t_jel = t_jeloltek_u(sor['utca_id'], sor['utca_vonal_idx'])
            # 8.2: egyeztetés a szemközti sávval, ha az is vegyes
            masik_id, vazvonal = _szemkozti_sav(eltabla, cella_id, cellak_sor)
            if masik_id is not None and masik_id in vegyes_idk and masik_id not in kesz:
                masik = cellak_sor[masik_id]
                U2 = U_vonal(masik)
                if U2 is not None:
                    cimek_masik = szamito[szamito['cella_id'] == masik_id]
                    kozos = vagas_8_2(sor['geometry'], U, cimek_cella, masik['geometry'], U2, cimek_masik, vazvonal,
                                      t_jel, t_jeloltek_u(masik['utca_id'], masik['utca_vonal_idx']), gy)
                    if kozos is not None:
                        (d1, v1), (d2, v2), v, szog = kozos
                        gy.kozos.extend([v1, v2]); gy.kozos_v.extend([v, v]); gy.kozos_szog.extend([szog, szog])
                        gy.vagas_tipus[P.VAGAS_KOZOS] += 2
                        for cid, darabok, cimek_c in ((cella_id, d1, cimek_cella), (masik_id, d2, cimek_masik)):
                            kat = darabok_kategoriai(darabok, cimek_c)
                            eredmenyek[cid] = [(d, next(iter(k)) if k else None) for d, k in zip(darabok, kat)]
                            vagas_tipus_cella[cid] = P.VAGAS_KOZOS
                            kesz.add(cid)
                        continue
            # 8.1: merőleges vágás az utca menti sor szerint
            e = vagas_8_1(sor['geometry'], U, cimek_cella, t_jel, gy)
            if e is not None:
                darabok, vagok, tipusok = e
                for vago, tip in zip(vagok, tipusok):
                    if tip == 'T':
                        gy.vagas_T.append(vago); gy.vagas_T_t.append(0.0); gy.vagas_T_utca.append(sor['utca_id'])
                        gy.vagas_tipus[P.VAGAS_T] += 1
                    else:
                        gy.vagas_kozep.append(vago); gy.vagas_kozep_t.append(0.0)
                        gy.vagas_tipus[P.VAGAS_KOZEP] += 1
                felosztas = darabok
                tipus = P.VAGAS_T if 'T' in tipusok else P.VAGAS_KOZEP
            else:
                # 8.3: párhuzamos vágás az utcától mért távolság szerint
                e = vagas_8_3(sor['geometry'], U, cimek_cella, gy)
                if e is not None:
                    darabok, vagok, d_lista = e
                    gy.parhuzamos.extend(vagok); gy.parhuzamos_d.extend(d_lista)
                    gy.vagas_tipus[P.VAGAS_PARHUZAMOS] += 1
                    felosztas = darabok
                    tipus = P.VAGAS_PARHUZAMOS
            if felosztas is not None:
                kat = darabok_kategoriai(felosztas, cimek_cella)
                eredmenyek[cella_id] = [(d, next(iter(k)) if k else None) for d, k in zip(felosztas, kat)]
                vagas_tipus_cella[cella_id] = tipus
                kesz.add(cella_id)
                continue
            # 8.4 sávnál: elő-/utófutam levágása + Voronoi a keveredő szakaszon
            eredmenyek[cella_id] = vagas_8_4_sav(sor['geometry'], U, cimek_cella, t_jel, gy)
            vagas_tipus_cella[cella_id] = P.VAGAS_VORONOI
            kesz.add(cella_id)
            continue

        # 8.4 sarokcellánál, tömbnél és utca nélküli foltnál: Voronoi a teljes cellára
        voronoi = voronoi_vagas(sor['geometry'], cimek_cella, gy)
        gy.vagas_tipus[P.VAGAS_VORONOI] += 1
        eredmenyek[cella_id] = voronoi if voronoi is not None else [(sor['geometry'], None)]
        vagas_tipus_cella[cella_id] = P.VAGAS_VORONOI
        kesz.add(cella_id)

    # 8.5 a felosztás után: darabok kategóriája, azonos kategóriájú darabok egyesítése a cellán belül,
    #     a vegyes maradék (Voronoi sem tudta szétválasztani) a többségi kategóriát kapja jelzéssel
    kovetkezo_id = T.kovetkezo_cella_id(cellak)
    uj_sorok = []
    vagas_tipus_oszlop = []
    for sor in cellak.itertuples(index=False):
        if sor.cella_id not in eredmenyek:
            uj_sorok.append(sor._asdict()); vagas_tipus_oszlop.append(None)
            continue
        darabok = eredmenyek[sor.cella_id]
        cimek_cella = szamito[szamito['cella_id'] == sor.cella_id]
        csoportok = {}
        for geom, szkid in darabok:
            if szkid is None and len(darabok_kategoriai([geom], cimek_cella)[0]) > 1:
                # szétválaszthatatlan vegyes darab: többségi kategória + jelzés a címeken
                bent = cimek_cella[cimek_cella.geometry.apply(geom.contains)]
                szkid = _tobbsegi(bent)
                for i in bent.index[bent['szkid'] != szkid]:
                    cimek.at[i, 'jelzes'] = jelzes_hozzaad(cimek.at[i, 'jelzes'], P.JELZES_AZONOS_PONT)
            csoportok.setdefault(szkid, []).append(geom)
        elso = True
        for szkid, geomok in csoportok.items():
            unio = shapely.unary_union(geomok, grid_size=P.RACS)
            for darab in T.poligonok_darabokra(unio):
                d = sor._asdict()
                d.update(geometry=darab, szkid=szkid, szabaly=P.SZABALY_CIMES if szkid is not None else None)
                if elso:
                    elso = False
                else:
                    d['cella_id'] = kovetkezo_id
                    kovetkezo_id += 1
                uj_sorok.append(d); vagas_tipus_oszlop.append(vagas_tipus_cella[sor.cella_id])
    cellak = T.uj_cellatabla(uj_sorok, crs)

    # debug rétegek (11.1)
    debug.reteg('8_1_vetitesek', '8.1', 'vetítés', gy.vetitesek, [debug.kategoria_szin(s) for s in gy.vetitesek_szkid],
                t=gy.vetitesek_t, szkid=gy.vetitesek_szkid)
    debug.reteg('8_1_sorok', '8.1', 'cím a sorban', gy.sorok, P.SZIN['segéd'], t=gy.sorok_t, futam=gy.sorok_futam, tiszta=gy.sorok_tiszta)
    debug.reteg('8_1_vagas_T', '8.1', 'merőleges vágás T-csomópontnál', gy.vagas_T, P.SZIN['vágóvonal T'], t=gy.vagas_T_t, T_utca=gy.vagas_T_utca)
    debug.reteg('8_1_vagas_kozep', '8.1', 'merőleges vágás a köz közepén', gy.vagas_kozep, P.SZIN['vágóvonal közép'], t=gy.vagas_kozep_t)
    debug.reteg('8_2_kozos_vonal', '8.2', 'egyeztetett vágás', gy.kozos, P.SZIN['vágóvonal T'], v=gy.kozos_v, tores_szog=gy.kozos_szog)
    debug.reteg('8_3_parhuzamos', '8.3', 'párhuzamos vágás', gy.parhuzamos, P.SZIN['vágóvonal párhuzamos'], d=gy.parhuzamos_d)
    debug.reteg('8_4_voronoi_sejtek', '8.4', 'Voronoi-sejt', gy.voronoi_sejtek, P.SZIN['Voronoi nyers sejt'], szkid=gy.voronoi_sejtek_szkid)
    debug.reteg('8_4_voronoi_nyers_vagohalo', '8.4', 'nyers vágóháló', gy.voronoi_nyers, P.SZIN['vágóvonal Voronoi'])
    debug.reteg('8_4_voronoi_vagas', '8.4', 'kiegyenesített Voronoi-vágás', gy.voronoi_vagas, P.SZIN['vágóvonal Voronoi'], t_tures=gy.voronoi_vagas_t)

    cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek, uj_vonal_tipus=P.TIPUS_VAGOVONAL)
    # a vagas_tipus oszlop a frissítés után cella_id szerint illesztve
    tipus_szerint = {cid: vt for cid, vt in zip([s['cella_id'] for s in uj_sorok], vagas_tipus_oszlop)}
    debug.cellak('8_R_cellak', '8.R', cellak, vagas_tipus=[tipus_szerint.get(c) for c in cellak['cella_id']])
    return cellak, eltabla, cimek, gy.vagas_tipus
