"""
v3_savok – 4. lépés (tömbök felbontása utcafront-sávokra) és 5. lépés (sarokcellák).

Leírás: 4.1–4.4, 5.

4. Cél: minden tömböt annyi sávra bontani, ahány utca szegélyezi, hogy a tömb belsejében a
   telekszerkezetet követő, egyenes határjelölt vonal (vázvonal) legyen. A címpontok ebben a
   lépésben nem vesznek részt; a vázvonal helye kizárólag a tömb alakjától függ.
5. Cél: a saroktelek egy négyszögletes ingatlan, amelynek jogi utcája nem ismert; nem futhat
   rajta át a sávok közötti 45°-os vonal.
"""

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import linemerge

import v3_param as P
import v3_topologia as T


def _nem_utca_csoport(el_id):
    """Nem-utca él (lakott határ, vasút, folyó, segédvonal) saját csoportneve a sávosításban."""
    return f'el_{el_id}'


def _utca_vonal_index(utca_vonalak, utca_id, sav_geom):
    """Egy sáv U-vonala: az utca azon komponense, amellyel a sáv határának a leghosszabb a közös
    része (ha egyik sem érinti, a legközelebbi). Visszatérés: komponens-index vagy None."""
    komponensek = utca_vonalak.get(utca_id)
    if not komponensek:
        return None
    if len(komponensek) == 1:
        return 0
    hatar = sav_geom.boundary
    kozos = [k.intersection(hatar).length if k.intersects(hatar) else -k.distance(hatar) for k in komponensek]
    return int(np.argmax(kozos))


# ──────────────────────────────────────────────────────────────────────────────
# 4.3 Egy tömb sávosítása
# ──────────────────────────────────────────────────────────────────────────────

def tomb_savositas(tomb_geom, tomb_elek, debug_gyujto):
    """Egy tömb felbontása utcafront-sávokra Voronoival a tömb határpontjaiból (4.3).

    tomb_elek: az éltábla e tömböt határoló sorai (tipus, rang, utca_id, el_id, geometry).
    Visszatérés: [(sáv_geom, utca_id)] – a nem-utca sávok már beolvasztva; vagy None, ha a tömb
    határán kevesebb mint két különböző utca van (akkor nem sávosítunk).
    A `debug_gyujto` dict listáiba kerülnek a 11.1 szerinti köztes geometriák.
    """
    utca_elek = tomb_elek[tomb_elek['tipus'] == P.TIPUS_UTCA]
    utca_idk = sorted(set(utca_elek['utca_id'].dropna()))
    if len(utca_idk) < 2:
        return None

    # 1. HATÁRPONTOK: a tömb gyűrűi VÁZFELBONTÁS-onként felosztva; minden pont csoportja az
    #    éltábla-sor utca_id-je, amelyen a pont ül (nem-utca élnél az él_id mint saját csoport)
    gyuruk = [tomb_geom.exterior] + list(tomb_geom.interiors)
    pontok = []
    for gyuru in gyuruk:
        koordinatak = shapely.get_coordinates(shapely.segmentize(gyuru, P.VAZFELBONTAS))[:-1]
        pontok.extend(map(tuple, koordinatak))
    pontok = list(dict.fromkeys(pontok))   # ismétlődések nélkül, sorrendtartóan
    pont_geomok = np.array([Point(p) for p in pontok], dtype=object)

    el_geomok = np.asarray(tomb_elek.geometry.values, dtype=object)
    el_fa = shapely.STRtree(el_geomok)
    pont_idx, el_idx = el_fa.query(pont_geomok, predicate='dwithin', distance=P.RACS)
    pont_el = {}
    for pi, ei in zip(pont_idx, el_idx):
        pont_el.setdefault(pi, ei)
    el_tipus = tomb_elek['tipus'].tolist()
    el_utca = tomb_elek['utca_id'].tolist()
    el_idk = tomb_elek['el_id'].tolist()

    csoportok = []
    hasznalt_pontok = []
    for pi, pont in enumerate(pont_geomok):
        if pi not in pont_el:
            continue   # nem ül élen (nem várt: a határ és az élek ugyanazok a vonalak)
        ei = pont_el[pi]
        if el_tipus[ei] == P.TIPUS_UTCA and el_utca[ei]:
            csoportok.append(el_utca[ei])
        else:
            csoportok.append(_nem_utca_csoport(el_idk[ei]))
        hasznalt_pontok.append(pont)
    if len(set(c for c in csoportok if not c.startswith('el_'))) < 2:
        return None
    debug_gyujto['hatarpontok'].extend(hasznalt_pontok)
    debug_gyujto['hatarpontok_csoport'].extend(csoportok)

    # 2. VORONOI a határpontokból, minden sejt a tömbre vágva, a saját pontjához rendelve
    voronoi = shapely.voronoi_polygons(MultiPoint(hasznalt_pontok), extend_to=tomb_geom, ordered=True)
    sejtek = [shapely.set_precision(sejt.intersection(tomb_geom), P.RACS) for sejt in voronoi.geoms]
    for sejt, csoport in zip(sejtek, csoportok):
        debug_gyujto['voronoi_nyers'].append(sejt)
        debug_gyujto['voronoi_nyers_csoport'].append(csoport)

    # 3. EGYESÍTÉS CSOPORTONKÉNT → nyers sávok (a csoporton belüli belső vonalak eltűnnek)
    sav_nyers = {}
    for csoport in dict.fromkeys(csoportok):
        tagok = [s for s, c in zip(sejtek, csoportok) if c == csoport and not s.is_empty]
        if tagok:
            sav_nyers[csoport] = shapely.unary_union(tagok, grid_size=P.RACS)
    for csoport, geom in sav_nyers.items():
        debug_gyujto['savok_nyers'].append(geom)
        debug_gyujto['savok_nyers_csoport'].append(csoport)

    # 4. VÁZVONALAK KIEGYENESÍTÉSE: a közös vonalakat egyszerűsítjük, nem a poligonokat külön
    tomb_hatar = shapely.set_precision(tomb_geom.boundary, P.RACS)
    hatarok_unio = shapely.unary_union([g.boundary for g in sav_nyers.values()], grid_size=P.RACS)
    vazvonalak_nyers = shapely.difference(hatarok_unio, tomb_hatar, grid_size=P.RACS)
    # az unió szakaszonként adja a vonalakat: a 2-fokú csúcsoknál összefűzzük (linemerge), hogy az
    # egyszerűsítés a teljes vázvonal-ágon fusson; a ≥3-fokú csomópontok végpontok maradnak
    vazvonalak_nyers = T.vonalak_darabokra(linemerge(shapely.MultiLineString(T.vonalak_darabokra(vazvonalak_nyers))))
    debug_gyujto['vazvonalak_nyers'].extend(vazvonalak_nyers)
    vazvonalak = T.vonalak_darabokra(
        shapely.simplify(shapely.MultiLineString(vazvonalak_nyers), P.VAZFELBONTAS, preserve_topology=True))

    def savok_polygonize(vaz):
        """polygonize(tömb határa + vázvonalak) → a tömbön belüli lapok; csoport a nyers sáv
        szerint, amelyben a lap reprezentatív pontja van."""
        halo = shapely.unary_union([tomb_hatar] + list(vaz), grid_size=P.RACS)
        lapok = [lap for lap in T.polygonize_full_nev_szerint(T.vonalak_darabokra(halo))['poligonok']
                 if tomb_geom.contains(lap.representative_point())]
        nyers_lista = list(sav_nyers.items())
        nyers_fa = shapely.STRtree(np.array([g for _, g in nyers_lista], dtype=object))
        eredmeny = []
        for lap in lapok:
            talalat = nyers_fa.query(lap.representative_point(), predicate='within')
            if len(talalat) == 0:
                talalat = nyers_fa.query_nearest(lap.representative_point())
            eredmeny.append((lap, nyers_lista[int(talalat[0])][0]))
        return eredmeny

    savok = savok_polygonize(vazvonalak)
    # védelem: ha az egyszerűsített vázvonal kilépett a tömbből (terület veszne), a nyers vázvonal marad
    if abs(sum(l.area for l, _ in savok) - tomb_geom.area) > max(P.LEFEDES_TURES_ABS_M2, P.LEFEDES_TURES_REL * tomb_geom.area):
        vazvonalak = vazvonalak_nyers
        savok = savok_polygonize(vazvonalak)
    debug_gyujto['vazvonalak'].extend(vazvonalak)

    # 5. NEM-UTCA SÁVOK BEOLVASZTÁSA: ezekre a vonalakra nem néz telek; a sáv abba a szomszédos
    #    utca-sávba olvad, amellyel a leghosszabb a közös vázvonala
    savok = [[geom, csoport] for geom, csoport in savok]
    while True:
        nem_utca = [i for i, (_, cs) in enumerate(savok) if cs.startswith('el_')]
        if not nem_utca:
            break
        valtozott = False
        for i in nem_utca:
            geom_i, csoport_i = savok[i]
            legjobb, legjobb_hossz = None, 0.0
            for j, (geom_j, csoport_j) in enumerate(savok):
                if j == i or csoport_j.startswith('el_'):
                    continue
                kozos = geom_i.boundary.intersection(geom_j.boundary).length
                if kozos > legjobb_hossz:
                    legjobb, legjobb_hossz = j, kozos
            if legjobb is None:
                continue
            debug_gyujto['nemutca_beolvasztas'].append(geom_i)
            debug_gyujto['nemutca_beolvasztas_csoport'].append(csoport_i)
            debug_gyujto['nemutca_beolvasztas_nyertes'].append(savok[legjobb][1])
            savok[legjobb][0] = shapely.unary_union([savok[legjobb][0], geom_i], grid_size=P.RACS)
            savok[i] = None
            valtozott = True
            break   # a lista változott: újraindítjuk a keresést
        savok = [s for s in savok if s is not None]
        if not valtozott:
            # nem-utca sáv, amely csak nem-utca sávokkal érintkezik (nem várt): a legnagyobb utca-sávba
            utca_savok = [i for i, (_, cs) in enumerate(savok) if not cs.startswith('el_')]
            if not utca_savok:
                return None
            cel = max(utca_savok, key=lambda i: savok[i][0].area)
            for i in [i for i, (_, cs) in enumerate(savok) if cs.startswith('el_')]:
                savok[cel][0] = shapely.unary_union([savok[cel][0], savok[i][0]], grid_size=P.RACS)
            savok = [s for i, s in enumerate(savok) if not s[1].startswith('el_')]
            break

    # a beolvasztás után ugyanazon utca sávjai érintkezhetnek: egyesítjük (egy utca = egy sáv a tömbön,
    # kivéve ha a tömb két különböző helyén határolja)
    eredmeny = []
    for csoport in dict.fromkeys(cs for _, cs in savok):
        unio = shapely.unary_union([g for g, cs in savok if cs == csoport], grid_size=P.RACS)
        for darab in T.poligonok_darabokra(unio):
            eredmeny.append((darab, csoport))
    return eredmeny


def lepes_4_savositas(cellak, eltabla, cimek, utca_vonalak, debug):
    """4. lépés – MINDEN tömbRE (cella, amelynek határán ≥ 2 különböző utca_id van) sávosítás.
    Ha a tömb határán csak egy utca van, a tömb maga egy sáv; ha egy sincs, nem sávosítunk.
    Visszatérés: (cellak, eltabla, cimek) az R. rutin után (a vázvonalak itt kapják a
    vázvonal típust és az 1-es rangot)."""
    crs = cellak.crs
    gyujto = {k: [] for k in ['hatarpontok', 'hatarpontok_csoport', 'voronoi_nyers', 'voronoi_nyers_csoport',
                              'savok_nyers', 'savok_nyers_csoport', 'vazvonalak_nyers', 'vazvonalak',
                              'nemutca_beolvasztas', 'nemutca_beolvasztas_csoport', 'nemutca_beolvasztas_nyertes']}
    uj_sorok = []
    kovetkezo_id = T.kovetkezo_cella_id(cellak)
    for sor in cellak.itertuples(index=False):
        if sor.cellatipus != P.CELLA_TOMB:
            uj_sorok.append(sor._asdict())
            continue
        tomb_elek = T.cella_elei(eltabla, sor.cella_id)
        utca_idk = sorted(set(tomb_elek.loc[tomb_elek['tipus'] == P.TIPUS_UTCA, 'utca_id'].dropna()))
        if len(utca_idk) == 0:
            uj_sorok.append(sor._asdict())          # nincs utca a határán: marad tömb
            continue
        if len(utca_idk) == 1:
            d = sor._asdict()                        # egy utca: a tömb maga egy sáv
            d.update(cellatipus=P.CELLA_SAV, tomb_id=sor.cella_id, utca_id=utca_idk[0],
                     utca_vonal_idx=_utca_vonal_index(utca_vonalak, utca_idk[0], sor.geometry))
            uj_sorok.append(d)
            continue
        savok = tomb_savositas(sor.geometry, tomb_elek, gyujto)
        if savok is None:
            uj_sorok.append(sor._asdict())
            continue
        for geom, utca_id in savok:
            d = sor._asdict()
            d.update(cella_id=kovetkezo_id, cellatipus=P.CELLA_SAV, tomb_id=sor.cella_id, utca_id=utca_id,
                     utca_vonal_idx=_utca_vonal_index(utca_vonalak, utca_id, geom), geometry=geom)
            kovetkezo_id += 1
            uj_sorok.append(d)
    cellak = T.uj_cellatabla(uj_sorok, crs)

    # debug rétegek (11.1)
    utca_paletta = P.kategoria_paletta([c for c in gyujto['hatarpontok_csoport'] if not c.startswith('el_')])
    def csoport_szin(cs):
        return utca_paletta.get(cs, P.SZIN['határpont'])
    debug.reteg('4_3_hatarpontok', '4.3', 'határpont', gyujto['hatarpontok'],
                [csoport_szin(c) for c in gyujto['hatarpontok_csoport']], csoport=gyujto['hatarpontok_csoport'])
    debug.reteg('4_3_voronoi_nyers', '4.3', 'Voronoi-sejt', gyujto['voronoi_nyers'],
                [csoport_szin(c) for c in gyujto['voronoi_nyers_csoport']], csoport=gyujto['voronoi_nyers_csoport'])
    debug.reteg('4_3_savok_nyers', '4.3', 'nyers sáv', gyujto['savok_nyers'],
                [csoport_szin(c) for c in gyujto['savok_nyers_csoport']], csoport=gyujto['savok_nyers_csoport'])
    debug.reteg('4_3_vazvonalak_nyers', '4.3', 'vázvonal (nyers)', gyujto['vazvonalak_nyers'], P.SZIN['vázvonal'])
    debug.reteg('4_3_vazvonalak', '4.3', 'vázvonal', gyujto['vazvonalak'], P.SZIN['vázvonal'])
    debug.reteg('4_3_nemutca_beolvasztas', '4.3', 'beolvasztott nem-utca sáv', gyujto['nemutca_beolvasztas'],
                P.SZIN['cella üres'], csoport=gyujto['nemutca_beolvasztas_csoport'],
                nyertes_utca=gyujto['nemutca_beolvasztas_nyertes'])

    # R. rutin: a vázvonalak itt kapják a vázvonal típust és az 1-es rangot
    cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek, uj_vonal_tipus=P.TIPUS_VAZVONAL)
    debug.cellak('4_R_savok', '4.R', cellak)
    return cellak, eltabla, cimek


# ──────────────────────────────────────────────────────────────────────────────
# 5. lépés – Sarokcellák
# ──────────────────────────────────────────────────────────────────────────────

def _lanc_s_vegponttal(elek_geom, S):
    """A tömb egy utcájának éleiből összefűzött lánc(ok) közül az, amelynek S végpontja.
    Visszatérés: (lánc, S az eleje?) vagy None."""
    lancok = T.vonalak_darabokra(linemerge(list(elek_geom))) if len(elek_geom) > 1 else list(elek_geom)
    for lanc in lancok:
        eleje, vege = T.vegpontok(lanc)
        if Point(eleje).distance(S) <= P.RACS:
            return lanc, True
        if Point(vege).distance(S) <= P.RACS:
            return lanc, False
    return None


def _meroleges_egyenes(lanc, S_elol, d, atlo):
    """P ← S-től d méterre a lánc mentén; m ← a láncra P-ben merőleges, mindkét irányban `atlo`
    hosszú egyenes-szakasz (a tömbre később vágjuk). Visszatérés: (P, m)."""
    tav = d if S_elol else lanc.length - d
    P_pont = lanc.interpolate(tav)
    irany = T._irany_a_ponton(lanc, tav / lanc.length)
    n = T.normalvektor(irany)
    c = shapely.get_coordinates(P_pont)[0]
    return P_pont, LineString([c - n * atlo, c + n * atlo])


def tomb_sarkai(tomb_geom, tomb_elek):
    """Egy tömb sarok-csomópontjai: a határ csúcsai, ahol két KÜLÖNBÖZŐ utca_id-jű utca találkozik
    (ugyanannak az utcának a kanyarja nem sarok). Visszatérés: [(S, U1, U2)]."""
    utca_elek = tomb_elek[(tomb_elek['tipus'] == P.TIPUS_UTCA) & tomb_elek['utca_id'].notna()]
    vegpont_utcak = {}
    for el in utca_elek.itertuples(index=False):
        for csucs in T.vegpontok(el.geometry):
            vegpont_utcak.setdefault(csucs, set()).add(el.utca_id)
    sarkok = []
    for csucs, utcak in vegpont_utcak.items():
        if len(utcak) >= 2:
            u1, u2 = sorted(utcak)[:2]
            sarkok.append((Point(csucs), u1, u2))
    sarkok.sort(key=lambda t: (t[0].x, t[0].y))
    return sarkok


def lepes_5_sarokcellak(cellak, eltabla, cimek, tombok, eltabla_tombok, debug):
    """5. lépés – MINDEN tömbRE, MINDEN sarok-csomópontRA (S; U1, U2):
       d ← min(SAROKMÉRET, az utca e tömb menti hosszának fele); P1, P2 a két utcán d-re S-től;
       m1, m2 a merőlegesek; X ← m1 ∩ m2 (HA X nincs a tömbben → nincs sarokcella);
       sarok_négyszög ← S, P1, X, P2; az U1 és U2 sávjait elvágjuk m1 és m2 mentén;
       sarokcella ← azok a darabok, amelyek reprezentatív pontja a sarok_négyszögben van.
    A vágás a merőlegesek P–X szakaszával történik (a négyszög két belső oldala): így a
    sarok pontosan a négyszög, a sávok távolabbi részét a merőleges nem darabolja.
    `tombok`/`eltabla_tombok`: a 3. lépés utáni (sávosítás előtti) tömbök és éltáblájuk.
    Visszatérés: (cellak, eltabla, cimek) az R. rutin után."""
    crs = cellak.crs
    sorok = {sor.cella_id: sor._asdict() for sor in cellak.itertuples(index=False)}
    kovetkezo_id = T.kovetkezo_cella_id(cellak)
    d_sarok_pontok, d_utca1, d_utca2, d_merolegesek, d_merolegesek_d, d_negyszogek = [], [], [], [], [], []

    for tomb in tombok.itertuples(index=False):
        if tomb.cellatipus != P.CELLA_TOMB:
            continue
        tomb_elek = T.cella_elei(eltabla_tombok, tomb.cella_id)
        utca_elek = tomb_elek[tomb_elek['tipus'] == P.TIPUS_UTCA]
        atlo = np.hypot(tomb.geometry.bounds[2] - tomb.geometry.bounds[0],
                        tomb.geometry.bounds[3] - tomb.geometry.bounds[1]) + 1.0
        for S, U1, U2 in tomb_sarkai(tomb.geometry, tomb_elek):
            lanc1 = _lanc_s_vegponttal(utca_elek.loc[utca_elek['utca_id'] == U1, 'geometry'].values, S)
            lanc2 = _lanc_s_vegponttal(utca_elek.loc[utca_elek['utca_id'] == U2, 'geometry'].values, S)
            if lanc1 is None or lanc2 is None:
                continue
            # d ← min(SAROKMÉRET, az utca e tömb menti hosszának fele): két sarok nem érhet össze
            d1 = min(P.SAROKMERET, lanc1[0].length / 2.0)
            d2 = min(P.SAROKMERET, lanc2[0].length / 2.0)
            if d1 <= P.RACS or d2 <= P.RACS:
                continue
            P1, m1 = _meroleges_egyenes(lanc1[0], lanc1[1], d1, atlo)
            P2, m2 = _meroleges_egyenes(lanc2[0], lanc2[1], d2, atlo)
            X = m1.intersection(m2)
            if X.geom_type != 'Point' or not tomb.geometry.contains(X):
                continue   # nagyon tompa szög vagy párhuzamos utcák: nincs sarokcella
            negyszog = Polygon([S, P1, X, P2])
            if not negyszog.is_valid or negyszog.area <= 0:
                continue
            d_sarok_pontok.append(S); d_utca1.append(U1); d_utca2.append(U2)
            d_negyszogek.append(negyszog)

            # vágóvonalak: P1→X és P2→X (a P végén túlnyúlással, X-ben pontosan találkoznak)
            vagok = []
            for P_pont, d in ((P1, d1), (P2, d2)):
                szakasz = LineString([P_pont, X])
                d_merolegesek.append(szakasz); d_merolegesek_d.append(d)
                vagok.append(_hosszabbit_eleje(szakasz, P.VAGOVONAL_TULNYULAS))

            # az U1 és U2 sávjainak elvágása; a négyszögbe eső darabok adják a sarokcellát
            sarok_darabok = []
            erintett = [cid for cid, s in sorok.items()
                        if s['cellatipus'] == P.CELLA_SAV and s['tomb_id'] == tomb.cella_id and s['utca_id'] in (U1, U2)]
            for cid in erintett:
                sav = sorok[cid]
                darabok = T.cella_felosztas(sav['geometry'], vagok)
                bent = [d for d in darabok if negyszog.contains(d.representative_point())]
                if not bent:
                    continue
                del sorok[cid]
                sarok_darabok.extend(bent)
                for darab in darabok:
                    if darab in bent:
                        continue
                    uj = dict(sav); uj['cella_id'] = kovetkezo_id; uj['geometry'] = darab
                    sorok[kovetkezo_id] = uj
                    kovetkezo_id += 1
            if sarok_darabok:
                sarok_geom = shapely.unary_union(sarok_darabok, grid_size=P.RACS)
                for darab in T.poligonok_darabokra(sarok_geom):
                    sorok[kovetkezo_id] = {
                        'cella_id': kovetkezo_id, 'cellatipus': P.CELLA_SAROK, 'tomb_id': tomb.cella_id,
                        'utca_id': None, 'utca_vonal_idx': None, 'szkid': None, 'szabaly': None,
                        'jelzes': '', 'sliver': False, 'geometry': darab}
                    kovetkezo_id += 1

    cellak = T.uj_cellatabla(list(sorok.values()), crs).sort_values('cella_id').reset_index(drop=True)
    debug.reteg('5_sarok_csomopontok', '5', 'sarok-csomópont', d_sarok_pontok, P.SZIN['sarok-merőleges'],
                utca_1=d_utca1, utca_2=d_utca2)
    debug.reteg('5_merolegesek', '5', 'sarok-merőleges', d_merolegesek, P.SZIN['sarok-merőleges'], d=d_merolegesek_d)
    debug.reteg('5_sarok_negyszogek', '5', 'sarok-négyszög', d_negyszogek, P.SZIN['cella sarok'])
    cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek, uj_vonal_tipus=P.TIPUS_VAGOVONAL)
    debug.cellak('5_R_cellak', '5.R', cellak)
    return cellak, eltabla, cimek


def _hosszabbit_eleje(vonal, hossz):
    """A vonal ELEJÉT hosszabbítja meg `hossz` méterrel (a vége – X – helyben marad)."""
    koordinatak = shapely.get_coordinates(vonal)
    irany = koordinatak[0] - koordinatak[1]
    irany = irany / np.hypot(*irany)
    return LineString(np.vstack([koordinatak[0] + irany * hossz, koordinatak]))
