'''
Polír réteg — a kész szavazóköri poligonok (`merged`) határhálózatának kozmetikai
tisztítása az utcahálózat segítségével. KIZÁRÓLAG a szépségért felel; a tényleges
burkolás-logikától elkülönül (minden polír-kód itt van), és a `generalas_pipeline`
UTOLSÓ lépéseként hívjuk.

Kötelező invariáns — OSZTÁLY-MEGŐRZÉS: egyetlen címpont sem kerülhet rossz
szavazókörbe. Minden mutáló lépés (VW, irány-illesztés, snap) előtt „söpört-terület"
(swept) tesztet futtatunk: a régi és az új vonal közé eső terület címpontjait nézzük;
ha bármelyik átfordulna, a lépést visszafogjuk. Globális háló: ha a záró validáció
bukik, az érintetlen `merged`-et adjuk vissza — a kozmetikai réteg sosem ronthat a
helyességen.

Adatfolyam (Shapely/GeoPandas, nincs PostGIS; a spec ST_* hívásai → Shapely):
  1. ív-kinyerés        : unary_union(határok) + linemerge → minden szkid↔szkid határ
                          EGY ív, EGYSZER feldolgozva → szomszéd-koherencia.
  2. zárolt/szabad       : utca-egybeesés (ε + helyi azimut) + hosszú, kis fordulású
                          futam → ZÁROLT (érintetlen); minden más SZABAD. A külső
                          (residential) határ mindig ZÁROLT.
  3. VW-egyszerűsítés    : Visvalingam–Whyatt a szabad darabokon, osztály-megőrzéssel
                          (per-ív bináris keresés a legnagyobb biztonságos területre).
  4. irány-illesztés     : szabad szegmensek illesztése a helyi utca-azimuthoz (vagy
                          +90°), sarkok = szomszéd vonalak metszéspontja; végpontok fixek.
  5. feltételes snap     : ha a szabad ív közel + párhuzamos egy utcával → belső csúcsait
                          a centerline-ra vetítjük; különben marad a saját eltolásában.
  6. újraépítés + valid. : vonalak → polygonize → szkid öröklés a belső pontból az
                          EREDETI merged-ből; coverage- és pont-ellenőrzés.

Bemenetek (a hívó adja): `merged` (UTM, szkid-enként egy sor), `gdf` (címpontok,
EPSG:4326), `edges` (valódi OSM utca-középvonalak UTM-ben — a `poly_gen_pipeline`-ból).
'''

import math

import pandas as pd
import geopandas as gpd

from shapely.ops import unary_union, linemerge, polygonize, polygonize_full
from shapely import make_valid, set_precision, STRtree
from shapely.geometry import LineString, Point, Polygon

from poligon_szk_fuggvenyek import (
    _extract_lines, polygonok_egyesitese,
)


# ── Paraméterek (méter / fok; a számítás UTM-ben zajlik) ───────────────────────
EPS_STREET     = 4.0     # m: egy szegmens "utcán fekszik" max. távolsága a centerline-tól
LOCK_AZ_TOL    = 12.0    # fok: a szegmens iránya ennyin belül legyen a helyi utca-azimuthoz
LOCK_MIN_LEN   = 25.0    # m: ennél hosszabb összefüggő utca-egybeesés kell a ZÁROLT-hoz
LOCK_MAX_TURN  = 25.0    # fok: a ZÁROLT futam kumulált fordulási szöge ennél kisebb (egyenes)

# A VW MOHÓ (greedy): nincs globális effektív-terület korlát, mert az a nagy ÜRES
# tüskéket bent tartaná, ha az íven pont-határolt rész is van. Minden csúcsot külön
# tesztel a söpört-teszttel — lásd _oszt_megorzo_vw.

AZ_TOL         = 15.0    # fok: a snap párhuzamosság-küszöbe (az irány-illesztés már
                         # kapu nélkül igazít a rácshoz — lásd _irany_illesztes)

FLIP_TOL       = 1.0     # m: OSZTÁLY-MEGŐRZÉS tolerancia. Egy cím csak akkor kerülhet
                         # át a szomszéd poligonba, ha az új határtól ≤ FLIP_TOL-ra esik
                         # (gyakorlatilag a vonalon ül). Szándékosan KICSI: a nagyobb
                         # érték látható hibás átsorolást okoz, beauty-nyereség nélkül —
                         # az ÜRES tüskéket úgyis a mohó VW tünteti el (ott 0 cím esik a
                         # söpört területbe, tehát FLIP_TOL-tól függetlenül törölhető).

SNAP_DELTA     = 8.0     # m: ennél közelebbi + párhuzamos ívet snap-elünk a centerline-ra

GRID_PREC      = 0.001   # m: set_precision rács újraépítéskor (1 mm). FONTOS: durvább
                         # rács (pl. 1 cm) a már pontos coverage-határoknál összecsúsztat
                         # közeli csúcsokat és lapot tüntethet el — 1 mm biztonságos.
NODE_PREC      = 0.001   # m: set_precision rács az ív-kinyeréskor (1 mm)
SIDE_TOL       = 0.05    # m: belső/külső döntésnél ekkora távolságon belül nézzük, hány
                         # KÜLÖNBÖZŐ szkid szegélyezi az ívet. NEM lehet ~1e-6 (mikron):
                         # az ív-középpont a set_precision miatt ~mm-rel elcsúszhat a
                         # merged poligon határától → a szomszéd szkid tévesen kimaradna.
AREA_EPS       = 1e-3    # m^2: átfedés sliver-küszöb
GAP_ABS        = 5.0     # m^2: lefedés-eltérés abszolút floor (FP-sliverek; ennyi a
                         # merged-ben is benne van a polygonize round-trip után)
GAP_REL        = 1e-4    # arány: a lefedés szimmetrikus differenciája ennél kisebb a
                         # teljes területhez képest (durva topológia-törést így is fog)


# ── Kis geometriai segédek ─────────────────────────────────────────────────────
def _szegmens_azimut(p0, p1):
    '''Egy szakasz azimutja fokban, [0, 180) tartományra hajtva (irány-agnosztikus).'''
    return math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0])) % 180.0


def _szog_elteres(a, b):
    '''Két [0,180) azimut közti eltérés fokban (max 90).'''
    d = abs(a - b) % 180.0
    return 180.0 - d if d > 90.0 else d


def _fordulasi_szog(p0, p1, p2):
    '''A p1 csúcs fordulási szöge fokban (a két szomszédos szakasz iránykülönbsége).'''
    a1 = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    a2 = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    d = math.degrees(abs(a2 - a1)) % 360.0
    return 360.0 - d if d > 180.0 else d


def _kumulalt_fordulas(coords):
    '''Egy polivonal kumulált (abszolút) fordulási szöge fokban.'''
    return sum(_fordulasi_szog(coords[i - 1], coords[i], coords[i + 1])
               for i in range(1, len(coords) - 1))


def _egyseg_vektor(azimut_fok):
    r = math.radians(azimut_fok)
    return (math.cos(r), math.sin(r))


def _metszespont(p1, u1, p2, u2):
    '''Két egyenes (pont + irány) metszéspontja; None ha közel-párhuzamos.'''
    (x1, y1), (dx1, dy1) = p1, u1
    (x2, y2), (dx2, dy2) = p2, u2
    den = dx1 * dy2 - dy1 * dx2
    if abs(den) < 1e-9:
        return None
    t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / den
    return (x1 + t * dx1, y1 + t * dy1)


def _dedup_coords(coords):
    '''Egymás utáni azonos koordináták eltüntetése.'''
    out = [coords[0]]
    for c in coords[1:]:
        if c != out[-1]:
            out.append(c)
    return out


# ── Utcahálózat index ──────────────────────────────────────────────────────────
def _utcak_indexe(edges, crs):
    '''Az `edges` (valódi utca-középvonalak) `crs`-re vetítve → (lista, STRtree).'''
    if edges is None or len(edges) == 0:
        return [], None
    e = edges.to_crs(crs) if edges.crs is not None and edges.crs != crs else edges
    streets = []
    for g in e.geometry:
        streets += _extract_lines(g)
    if not streets:
        return [], None
    return streets, STRtree(streets)


def _helyi_azimut(pt, tree, streets):
    '''A legközelebbi utcaszegmens lokális azimutja a `pt` pontnál + a távolság.
    Visszatérés: (azimut_fok vagy None, tav).'''
    if tree is None:
        return None, float('inf')
    idx = tree.query_nearest(pt)
    if len(idx) == 0:
        return None, float('inf')
    line = streets[int(idx[0])]
    tav = line.distance(pt)
    s = line.project(pt)
    p0 = line.interpolate(max(0.0, s - 1.0))
    p1 = line.interpolate(min(line.length, s + 1.0))
    if p0.equals(p1):
        return None, tav
    return _szegmens_azimut((p0.x, p0.y), (p1.x, p1.y)), tav


# ── 1. lépés: ív-kinyerés ──────────────────────────────────────────────────────
def _ivek_kinyerese(merged):
    '''A merged-lapok határai noding + linemerge → ívek. A linemerge a fok≥3
    csomópontoknál ELVÁG, a fok-2-n ÁTMEGY, így minden megosztott szkid↔szkid határ
    EGY ívként, EGYSZER jelenik meg → a közös ívet egyszer módosítjuk, mindkét
    szomszéd abból épül újra (varratmentesség). A külső (residential) ívet külön
    jelöljük: a felezőpontját pontosan 1 lap fedi (a belsőt 2).'''
    boundaries = unary_union(
        [g.boundary for g in merged.geometry if g is not None and not g.is_empty]
    )
    boundaries = set_precision(boundaries, NODE_PREC)
    merged_lines = linemerge(boundaries)
    arcs = _extract_lines(merged_lines)

    sidx = merged.sindex
    szk_vals = list(merged["szavazokorid"])
    out = []
    for arc in arcs:
        if arc.is_empty or arc.length <= 0:
            continue
        mid = arc.interpolate(0.5, normalized=True)
        cand = list(sidx.intersection(mid.buffer(4 * SIDE_TOL).bounds))
        # KÜLSŐ, ha az ívet csak EGY szkid szegélyezi (másik oldal void / városhatár);
        # BELSŐ, ha kettő különböző. Távolság-tűréssel (SIDE_TOL), mert a középpont
        # ~mm-rel elcsúszhat a poligonhatártól.
        near = set()
        for j in cand:
            if merged.geometry.iloc[j].distance(mid) <= SIDE_TOL:
                near.add(szk_vals[j])
        out.append({"geom": arc, "kulso": len(near) < 2})
    return out


# ── 2. lépés: zárolt / szabad osztályozás ──────────────────────────────────────
def _arc_osztalyozas(arc, tree, streets):
    '''Egy ív szegmenseit ZÁROLT/SZABAD-ra bontja. ZÁROLT egy szegmens, ha utcán
    fekszik (ε + irány) ÉS hosszú, kis fordulású összefüggő utca-futam része.
    Az ívet a címke-váltásoknál vágja; a váltás-csúcsok közös horgonyok maradnak.
    Visszatérés: list[(LineString, locked_bool)].'''
    coords = _dedup_coords(list(arc.coords))
    n = len(coords)
    if n < 2:
        return []

    # szegmensenként: utcán fekszik-e
    seg_on = []
    for i in range(n - 1):
        mx = (coords[i][0] + coords[i + 1][0]) / 2.0
        my = (coords[i][1] + coords[i + 1][1]) / 2.0
        az_seg = _szegmens_azimut(coords[i], coords[i + 1])
        az_utca, tav = _helyi_azimut(Point(mx, my), tree, streets)
        seg_on.append(tav <= EPS_STREET and az_utca is not None
                      and _szog_elteres(az_seg, az_utca) <= LOCK_AZ_TOL)

    # összefüggő utca-futamok → ZÁROLT csak ha elég hosszú és egyenes
    seg_locked = [False] * (n - 1)
    i = 0
    while i < n - 1:
        if not seg_on[i]:
            i += 1
            continue
        j = i
        while j < n - 1 and seg_on[j]:
            j += 1
        run = coords[i:j + 1]
        if LineString(run).length >= LOCK_MIN_LEN and _kumulalt_fordulas(run) <= LOCK_MAX_TURN:
            for k in range(i, j):
                seg_locked[k] = True
        i = j

    # vágás a címke-váltásoknál
    subs = []
    start = 0
    for k in range(1, n - 1):
        if seg_locked[k] != seg_locked[k - 1]:
            subs.append((LineString(coords[start:k + 1]), seg_locked[k - 1]))
            start = k
    subs.append((LineString(coords[start:n]), seg_locked[n - 2]))
    return subs


# ── Osztály-megőrzés: söpört-terület teszt ─────────────────────────────────────
def _swept_biztonsagos(old_line, new_line, pts_tree):
    '''Igaz, ha a régi→új vonalcsere osztály-megőrző a FLIP_TOL tolerancia mellett.
    A régi és az új vonal (közös végpontokkal) zárt hurko(ka)t alkot; az általuk
    bezárt „söpört" területbe eső címek kerülnének át a másik oldalra. Ez MEGENGEDETT,
    ha az átkerülő cím az ÚJ vonaltól ≤ FLIP_TOL-ra esik (borderline ház); NEM
    biztonságos, ha bármely átkerülő cím ennél messzebb kerülne (durva átsorolás).'''
    if new_line is None:
        return False
    if old_line.equals(new_line):
        return True
    if not new_line.is_simple:           # önmetsző jelölt → visszafogás
        return False
    swept = unary_union(list(polygonize(unary_union([old_line, new_line]))))
    if swept.is_empty or swept.area <= AREA_EPS:
        return True
    if pts_tree is None:
        return True
    for i in pts_tree.query(swept, predicate="contains"):
        if new_line.distance(pts_tree.geometries[int(i)]) > FLIP_TOL:
            return False
    return True


# ── 3. lépés: Visvalingam–Whyatt (osztály-megőrző) ─────────────────────────────
def _vw_kulcsok(coords):
    '''Minden belső csúcshoz a VW „effektív terület" küszöböt rendeli (monoton, a
    törlési sorrend szerint). A monotonitás miatt: „egyszerűsítés t-re" = minden
    olyan csúcs eldobása, amelynek kulcsa ≤ t.'''
    import heapq
    n = len(coords)
    if n <= 2:
        return {}

    def tri(a, b, c):
        return 0.5 * abs(
            (coords[b][0] - coords[a][0]) * (coords[c][1] - coords[a][1])
            - (coords[c][0] - coords[a][0]) * (coords[b][1] - coords[a][1])
        )

    prevn = list(range(-1, n - 1))
    nextn = list(range(1, n + 1))
    alive = [True] * n
    ver = [0] * n
    heap = []
    for i in range(1, n - 1):
        heapq.heappush(heap, (tri(i - 1, i, i + 1), i, ver[i]))

    keys = {}
    last = 0.0
    while heap:
        a, i, v = heapq.heappop(heap)
        if not alive[i] or v != ver[i]:
            continue
        if a < last:
            a = last          # monoton kulcs (Visvalingam-trükk)
        last = a
        keys[i] = a
        alive[i] = False
        p, q = prevn[i], nextn[i]
        nextn[p] = q
        prevn[q] = p
        for m in (p, q):
            if 1 <= m <= n - 2 and alive[m]:
                ver[m] += 1
                heapq.heappush(heap, (tri(prevn[m], m, nextn[m]), m, ver[m]))
    return keys


def _haromszog_ures(a, b, c, pts_tree):
    '''Igaz, ha a b csúcs eldobásakor söpört (a, b, c) háromszög NEM tartalmaz címet.
    Ekkor a vágás SEMELYIK címet sem viszi át a másik oldalra → bizonyíthatóan zéró
    hiba (akkor is, ha sok egymást követő vágás történik, mert egy pontot se söpör meg
    soha — nincs kumulatív drift). Az ÜRES tüskét/fűrészfogat mérettől függetlenül
    eltünteti, a pontot tartalmazó kitüremkedést megtartja. O(háromszögbe eső pont).'''
    tri = Polygon((a, b, c))
    if tri.area <= AREA_EPS or pts_tree is None:
        return True
    return len(pts_tree.query(tri, predicate="contains")) == 0


def _oszt_megorzo_vw(line, pts_tree):
    '''Mohó Visvalingam–Whyatt, INKREMENTÁLIS osztály-megőrző teszttel (gyors, O(n)
    arcánként — nincs per-csúcs polygonize). A belső csúcsokat növekvő effektív-terület
    sorrendben dobja; egy csúcsot CSAK akkor, ha az eldobásakor keletkező háromszög
    osztály-megőrző (_haromszog_biztonsagos). Az ÜRES kitüremkedéseket (fűrészfog,
    háromszög-tüske) MÉRETTŐL függetlenül eltünteti, a pont-határolt hullámzást
    megtartja — nincs globális tolerancia-korlát, ami a nagy üres tüskéket bent tartaná.'''
    coords = _dedup_coords(list(line.coords))
    n = len(coords)
    if n <= 2:
        return line
    keys = _vw_kulcsok(coords)
    order = sorted(range(1, n - 1), key=lambda i: keys.get(i, float('inf')))
    prevn = list(range(-1, n - 1))   # kétirányú láncolt lista a megmaradó csúcsokon
    nextn = list(range(1, n + 1))
    alive = [True] * n
    for v in order:
        p, q = prevn[v], nextn[v]
        if p < 0 or q >= n:
            continue
        if _haromszog_ures(coords[p], coords[v], coords[q], pts_tree):
            alive[v] = False
            nextn[p] = q
            prevn[q] = p
    kept = _dedup_coords([coords[i] for i in range(n) if alive[i]])
    return LineString(kept) if len(kept) >= 2 else line


# ── 4. lépés: irány-illesztés a helyi utca-azimuthoz ───────────────────────────
def _irany_illesztes(line, tree, streets, pts_tree, ref=None):
    '''A szabad vonalat az utcahálózat alakjához igazítja: MINDEN szegmenst a helyi
    utca PÁRHUZAMOS vagy MERŐLEGES irányához húz (kapu nélkül — a legközelebbi
    rács-irányhoz), így a vonal felveszi az utcarács alakját akkor is, ha nem az
    utcán fut. A szomszédos azonos-irányú szegmenseket egy éllé vonja össze (nincs
    tüske), a köztes sarkokat a szomszédos illesztett egyenesek metszéspontjaként
    számolja, a VÉGPONTOKAT fixen tartja (a szélső élek a fix végponton mennek át).
    Osztály-megőrző visszafogás (FLIP_TOL).'''
    coords = _dedup_coords(list(line.coords))
    n = len(coords)
    if n < 3 or tree is None:
        return line

    # 1) szegmensenként cél-irány = a helyi utca párhuzamosa/merőlegese közül a közelebbi
    seg_dir = []
    for i in range(n - 1):
        mx = (coords[i][0] + coords[i + 1][0]) / 2.0
        my = (coords[i][1] + coords[i + 1][1]) / 2.0
        az_seg = _szegmens_azimut(coords[i], coords[i + 1])
        az_utca, _ = _helyi_azimut(Point(mx, my), tree, streets)
        if az_utca is None:
            seg_dir.append(az_seg)
        else:
            cands = [az_utca % 180.0, (az_utca + 90.0) % 180.0]
            seg_dir.append(min(cands, key=lambda a: _szog_elteres(az_seg, a)))

    # 2) szomszédos közel-azonos irányú szegmensek csoportosítása (egy él)
    groups = [[0]]
    for i in range(1, n - 1):
        if _szog_elteres(seg_dir[i], seg_dir[groups[-1][-1]]) <= 5.0:
            groups[-1].append(i)
        else:
            groups.append([i])

    # 3) csoportonként illesztett egyenes; a szélső csoportok a FIX végponton át
    glines = []
    for gi, g in enumerate(groups):
        u = _egyseg_vektor(seg_dir[g[0]])
        if gi == 0:
            p = coords[0]
        elif gi == len(groups) - 1:
            p = coords[-1]
        else:
            ks = sorted(set([k for si in g for k in (si, si + 1)]))
            p = (sum(coords[k][0] for k in ks) / len(ks),
                 sum(coords[k][1] for k in ks) / len(ks))
        glines.append((p, u))

    # 4) új csúcsok: fix végpontok + a csoporthatár-sarkok = szomszéd egyenesek metszése
    new_coords = [coords[0]]
    for gi in range(len(glines) - 1):
        m = _metszespont(glines[gi][0], glines[gi][1], glines[gi + 1][0], glines[gi + 1][1])
        new_coords.append(m if m is not None else coords[groups[gi][-1] + 1])
    new_coords.append(coords[-1])
    new_coords = _dedup_coords(new_coords)
    if len(new_coords) < 2:
        return line

    cand = LineString(new_coords)
    return cand if _swept_biztonsagos(ref if ref is not None else line, cand, pts_tree) else line


# ── 5. lépés: feltételes pozíció-snap a centerline-ra ──────────────────────────
def _pozicio_snap(line, tree, streets, pts_tree, ref=None):
    '''Ha az ív közel (≤ SNAP_DELTA) ÉS párhuzamos egy utcával, a BELSŐ csúcsait a
    legközelebbi utca-középvonalra vetíti (a végpontok fixek). Különben (utcáktól
    távol = két utca közt) marad a saját eltolásában → a középső sor sosem fordul át.'''
    coords = _dedup_coords(list(line.coords))
    n = len(coords)
    if n < 3 or tree is None:
        return line

    mid = line.interpolate(0.5, normalized=True)
    az_utca, tav = _helyi_azimut(mid, tree, streets)
    if az_utca is None or tav > SNAP_DELTA:
        return line
    if _szog_elteres(_szegmens_azimut(coords[0], coords[-1]), az_utca) > AZ_TOL:
        return line

    idx = tree.query_nearest(mid)
    if len(idx) == 0:
        return line
    street = streets[int(idx[0])]

    new_coords = [coords[0]]
    for i in range(1, n - 1):
        p = Point(coords[i])
        if street.distance(p) <= SNAP_DELTA:
            proj = street.interpolate(street.project(p))
            new_coords.append((proj.x, proj.y))
        else:
            new_coords.append(coords[i])
    new_coords.append(coords[-1])
    new_coords = _dedup_coords(new_coords)
    if len(new_coords) < 2:
        return line

    cand = LineString(new_coords)
    return cand if _swept_biztonsagos(ref if ref is not None else line, cand, pts_tree) else line


# ── 6. lépés: újraépítés + validáció ───────────────────────────────────────────
def _ujraepites(all_lines, merged):
    '''A zárolt + feldolgozott szabad vonalakból újra-polygonizál, és minden lapnak
    a belső (representative) pontja alapján örökli a szkid-et az EREDETI merged-ből
    (rebuild_coverage minta). Visszatérés: (faces_gdf, dangle_üres_bool).'''
    linework = set_precision(unary_union(all_lines), GRID_PREC)
    faces = list(polygonize(linework))

    _, _, dangles, _ = polygonize_full(linework)
    dangle_ures = dangles.is_empty

    sidx = merged.sindex
    rows = []
    for f in faces:
        if f.is_empty or f.area <= AREA_EPS:
            continue
        rp = f.representative_point()
        cand = list(sidx.intersection(rp.bounds))
        found = None
        for j in cand:
            if merged.geometry.iloc[j].contains(rp):
                found = j
                break
        if found is None and cand:
            found = min(cand, key=lambda j: merged.geometry.iloc[j].distance(rp))
        if found is None:
            continue
        rows.append({
            "szavazokorid": merged["szavazokorid"].iloc[found],
            "color": merged["color"].iloc[found] if "color" in merged.columns else None,
            "geometry": f,
        })
    faces_gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=merged.crs)
    return faces_gdf, dangle_ures


def _coverage_ellenorzes(faces_gdf, merged_union):
    '''ST_CoverageInvalidEdges-analóg: nincs átfedés a lapok közt, és a lapok uniója
    területileg megegyezik a polír ELŐTTI lefedéssel (nincs rés/kilógás).'''
    geoms = list(faces_gdf.geometry)
    if not geoms:
        return False
    tree = STRtree(geoms)
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j <= i:
                continue
            inter = g.intersection(geoms[j])
            if not inter.is_empty and inter.area > AREA_EPS:
                return False
    u = unary_union(geoms)
    symdiff = merged_union.difference(u).area + u.difference(merged_union).area
    if symdiff > max(GAP_ABS, GAP_REL * merged_union.area):
        return False
    return True


def _pont_ellenorzes(faces_gdf, merged, pts):
    '''REGRESSZIÓ-ellenőrzés: nem a regiszter-szkidhez hasonlít (azt eleve elronthatta
    egy rossz geokód még a polír ELŐTT), hanem azt nézi, MEGVÁLTOZTATTA-E a polír egy
    cím tartalmazó szavazókörét. Egy cím, amelynek szkidje a merged-ben (polír előtt) X
    volt és a faces-ben (polír után) Y lett, „átkerült". Az átkerülés megengedett, ha a
    cím a lap határától ≤ FLIP_TOL-ra esik (borderline); DURVA (tiltott), ha messzebb.
    Visszatérés: (átkerült_összes, durva).'''
    if len(pts) == 0:
        return 0, 0
    base = pts[["geometry"]].copy()
    jm = gpd.sjoin(
        base, merged[["szavazokorid", "geometry"]].rename(columns={"szavazokorid": "szk_m"}),
        how="left", predicate="within")
    jm = jm[~jm.index.duplicated(keep="first")][["szk_m"]]
    jf = gpd.sjoin(
        base, faces_gdf[["szavazokorid", "geometry"]].rename(columns={"szavazokorid": "szk_f"}),
        how="left", predicate="within")
    jf = jf[~jf.index.duplicated(keep="first")][["szk_f", "index_right"]]
    m = base.join(jm).join(jf)

    flipped = 0
    gross = 0
    for _, r in m.iterrows():
        sm, sf, fr = r["szk_m"], r["szk_f"], r["index_right"]
        if pd.isna(sm) or pd.isna(sf) or sm == sf:
            continue                       # a polír nem változtatta a szkidjét
        flipped += 1
        if not pd.isna(fr) and faces_gdf.geometry.iloc[int(fr)].boundary.distance(r["geometry"]) > FLIP_TOL:
            gross += 1
    return flipped, gross


# ── Orkesztrátor ───────────────────────────────────────────────────────────────
def polir(merged, gdf, edges, debug_path=None):
    '''A kész szkid-poligonok (merged) határhálózatának kozmetikai csiszolása az
    utcahálózat (edges) alapján, osztály-megőrzéssel. Bemenet/kimenet azonos séma:
    GeoDataFrame {szavazokorid, color, geometry}. Bukásnál az érintetlen merged-et
    adja vissza.'''
    if merged is None or len(merged) <= 1:
        return merged

    streets, tree = _utcak_indexe(edges, merged.crs)

    pts = gdf.to_crs(merged.crs) if gdf.crs is not None and gdf.crs != merged.crs else gdf
    pts = pts[pts.geometry.notna() & ~pts.geometry.is_empty].copy()
    pts_geoms = list(pts.geometry)
    pts_tree = STRtree(pts_geoms) if pts_geoms else None
    merged_union = unary_union(list(merged.geometry))

    # 1–2. ív-kinyerés + osztályozás
    arcs = _ivek_kinyerese(merged)
    locked_lines = []
    free_lines = []
    for a in arcs:
        if a["kulso"]:
            locked_lines.append(a["geom"])          # külső határ: mindig zárolt
            continue
        for sub, locked in _arc_osztalyozas(a["geom"], tree, streets):
            if sub.is_empty or sub.length <= 0:
                continue
            (locked_lines if locked else free_lines).append(sub)

    # 3–5. szabad darabok feldolgozása (mindegyik söpört-teszttel guardolva)
    processed = []
    valtozott = 0
    for ln in free_lines:
        orig = ln
        ln = _oszt_megorzo_vw(ln, pts_tree)                            # 3. VW (vs orig)
        ln = _irany_illesztes(ln, tree, streets, pts_tree, ref=orig)   # 4. irány (vs orig)
        ln = _pozicio_snap(ln, tree, streets, pts_tree, ref=orig)      # 5. snap (vs orig)
        if ln is not None and not ln.is_empty and ln.length > 0:
            processed.append(ln)
            if not ln.equals(orig):
                valtozott += 1

    # 6. újraépítés + validáció
    all_lines = locked_lines + processed
    if not all_lines:
        return merged
    faces, dangle_ures = _ujraepites(all_lines, merged)

    coverage_ok = _coverage_ellenorzes(faces, merged_union)
    atkerult, durva = _pont_ellenorzes(faces, merged, pts)
    print(f"[polir] ívek={len(arcs)} zárolt={len(locked_lines)} "
          f"szabad={len(free_lines)} (változott {valtozott}) | coverage_ok={coverage_ok} "
          f"dangle_üres={dangle_ures} átkerült_cím={atkerult} (durva={durva}, FLIP_TOL={FLIP_TOL:g}m)")

    if not (coverage_ok and dangle_ures and durva == 0):
        print("[polir] validáció bukott — az érintetlen merged-et adom vissza.")
        return merged

    out = polygonok_egyesitese(faces)
    out["geometry"] = out.geometry.apply(make_valid)

    if debug_path is not None:
        if locked_lines:
            gpd.GeoDataFrame(geometry=locked_lines, crs=merged.crs).to_file(
                debug_path, layer="polir_zarolt_ivek", driver="GPKG")
        if processed:
            gpd.GeoDataFrame(geometry=processed, crs=merged.crs).to_file(
                debug_path, layer="polir_szabad_ivek", driver="GPKG")

    return out
