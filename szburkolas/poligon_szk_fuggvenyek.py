import pandas as pd
import geopandas as gpd
import colorsys

from shapely.ops import unary_union, split, polygonize, voronoi_diagram, linemerge
from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon, MultiPoint, Point


def _distinct_hex_colors(n, s=0.62, v1=0.92, v2=0.78):
    """
    n db jól elkülönülő szín.
    Hue: golden ratio léptetés; V: váltogatva (v1/v2), hogy nagy n-nél is szétváljanak.
    """
    if n <= 0:
        return []
    golden = 0.618033988749895
    h = 0.0
    out = []
    for i in range(n):
        h = (h + golden) % 1.0
        v = v1 if (i % 2 == 0) else v2
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return out


def add_color_to_gdf(gdf):
    ids = list(gdf["szavazokorid"].unique())
    n = len(ids)
    print('Szavazókörök száma', n)
    palette = _distinct_hex_colors(n, s=0.60, v1=0.92, v2=0.80)
    color_map = dict(zip(ids, palette))
    gdf = gdf.copy()
    gdf["color"] = gdf["szavazokorid"].map(color_map)
    return gdf


def pontok_polygonban(gdf, gdf_szigetek, max_depth=3, skip_felezes=False, debug_path=None):
    '''
    Végigmegy minden poligonon, megkeresi a pontokat, és:
      - ha több szavazókör van egy poligonon belül -> Voronoi-cellás felosztás
          (polygon_szkid_voronoi_vagas; skip_felezes=True esetén helyette a többségi
           szavazókör kapja az egész cellát, nem bontjuk)
      - ha egyetlen szavazókör van -> results sorba menti
    debug_path megadásakor a voronoi_sejtek (poligon) és voronoi_elek (vonal) debug
    rétegeket is kiírja a megadott GPKG-be.
    '''

    if gdf.crs is None or gdf_szigetek.crs is None:
        raise ValueError("Mindkét GeoDataFrame-nek kell legyen CRS-e")

    if gdf.crs != gdf_szigetek.crs:
        gdf = gdf.to_crs(gdf_szigetek.crs)

    rows = []
    voronoi_cells = []
    voronoi_edges = []

    for poly_idx, poly_row in gdf_szigetek.iterrows():
        polygon_geom = poly_row.geometry

        points_inside = gdf[gdf.within(polygon_geom)].copy()

        if len(points_inside) == 0:
            rows.append({"szavazokorid": None, "color": None, "geometry": polygon_geom})
            continue

        unique_szavazokorok = points_inside["szavazokorid"].dropna().unique()

        if len(unique_szavazokorok) != 1:
            if skip_felezes:
                counts = points_inside["szavazokorid"].dropna().value_counts()
                winner = counts.index[0]
                winner_color = points_inside.loc[
                    points_inside["szavazokorid"] == winner, "color"
                ].iloc[0]
                rows.append({"szavazokorid": winner, "color": winner_color, "geometry": polygon_geom})
            else:
                try:
                    sub_rows, cells, edges = polygon_szkid_voronoi_vagas(polygon_geom, points_inside)
                    rows.extend(sub_rows)
                    voronoi_cells.extend(cells)
                    voronoi_edges.extend(edges)
                except Exception as e:
                    # degenerált eset (pl. egybeeső magok): többségi szkid fallback
                    print(f"[voronoi_split] hiba, többségi fallback: {e}")
                    counts = points_inside["szavazokorid"].dropna().value_counts()
                    winner = counts.index[0]
                    winner_color = points_inside.loc[
                        points_inside["szavazokorid"] == winner, "color"
                    ].iloc[0]
                    rows.append({"szavazokorid": winner, "color": winner_color, "geometry": polygon_geom})
            continue

        rows.append({
            "szavazokorid": unique_szavazokorok[0],
            "color": points_inside.iloc[0]["color"],
            "geometry": polygon_geom
        })

    results = gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf_szigetek.crs)

    if debug_path is not None:
        if voronoi_cells:
            gpd.GeoDataFrame(voronoi_cells, geometry="geometry", crs=gdf_szigetek.crs)\
                .to_file(debug_path, layer="voronoi_sejtek", driver="GPKG")
        if voronoi_edges:
            gpd.GeoDataFrame(geometry=voronoi_edges, crs=gdf_szigetek.crs)\
                .to_file(debug_path, layer="voronoi_elek", driver="GPKG")

    return results


def felez(poly):
    """
    A poligont kettévágja a centroidon átmenő vágással (a hosszabb bbox tengely mentén).
    """
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = poly.centroid.x, poly.centroid.y

    if (maxx - minx) >= (maxy - miny):
        vago = LineString([(cx, miny - 1), (cx, maxy + 1)])
    else:
        vago = LineString([(minx - 1, cy), (maxx + 1, cy)])

    darabok = list(split(poly, vago).geoms)
    return darabok if len(darabok) >= 2 else [poly]


def polygon_tobb_szavazokor(polygon_geom, points_inside, max_depth=25):
    '''
    Több szavazókörös poligon szétbontása felezéssel (stack-alapú DFS).

    Működés:
    - Minden körben: poligon felezése a centroidon átmenő vágással (hosszabb tengely)
    - A felekre újraszűrjük a pontokat:
        - 0 pont -> üres poligon, nem bontjuk tovább
        - 1 szavazokorid -> kész
        - több szavazokorid -> vissza a stackbe
    '''

    rows = []
    stack = [(polygon_geom, points_inside, 0)]

    while stack:
        poly, pts, depth = stack.pop()

        if depth >= max_depth:
            print('Elérte a poly a mélységi szintet!')
            continue

        for darab in felez(poly):
            darab_pts = pts[pts.within(darab)].copy()

            if len(darab_pts) == 0:
                rows.append({"szavazokorid": None, "color": None, "geometry": darab})
                continue

            uniq = darab_pts["szavazokorid"].dropna().unique()

            if len(uniq) == 1:
                rows.append({
                    "szavazokorid": uniq[0],
                    "color": darab_pts.iloc[0]["color"],
                    "geometry": darab
                })
                continue

            stack.append((darab, darab_pts, depth + 1))

    return rows


def _extract_polys(geom):
    """Polygon részek kinyerése tetszőleges geometriából."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [g for g in geom.geoms if g.geom_type == "Polygon" and not g.is_empty]
    return []


def _extract_lines(geom):
    """LineString részek kinyerése tetszőleges geometriából."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            out += _extract_lines(g)
        return out
    return []


def _merge_lines(lines):
    """LineString-ek összevonása (linemerge csak MultiLineString-re hívható)."""
    if not lines:
        return []
    merged = unary_union(lines)
    if merged.geom_type == "MultiLineString":
        merged = linemerge(merged)
    return _extract_lines(merged)


def _extend_boundary_ends(ln, boundary, delta, tol=1e-6):
    """A vonal HATÁRON ülő végpontjait kifelé hosszabbítja delta-val (a belső
    csomópont-végeket érintetlenül hagyja). Egy csak a végpontján érintkező húrt a
    shapely nem vág át a poligonon — a kis túlnyúlás garantálja az átvágást, a
    túllógó részt utána a poligonra vágással levágjuk."""
    cs = list(ln.coords)
    if len(cs) < 2:
        return ln
    if boundary.distance(Point(cs[0])) <= tol:
        (x0, y0), (x1, y1) = cs[0], cs[1]
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 or 1.0
        cs[0] = (x0 - (x1 - x0) / d * delta, y0 - (y1 - y0) / d * delta)
    if boundary.distance(Point(cs[-1])) <= tol:
        (xa, ya), (xb, yb) = cs[-2], cs[-1]
        d = ((xb - xa) ** 2 + (yb - ya) ** 2) ** 0.5 or 1.0
        cs[-1] = (xb + (xb - xa) / d * delta, yb + (yb - ya) / d * delta)
    return LineString(cs)


def polygon_szkid_voronoi_vagas(polygon_geom, points_inside):
    '''
    Vegyes (több szkid-ű) cella Voronoi-cellás felosztása, osztály-megőrző
    egyszerűsítéssel.

    Magok = a cella ÖSSZES címpontja, mindegyik a saját szkid-jével. Minden maghoz
    Voronoi-territóriumot rendelünk (a sík minden pontja a hozzá legközelebbi maghoz
    tartozik), a territóriumot a cellára vágjuk és a mag szkid-jével színezzük, majd az
    azonos szkid-ű territóriumokat összevonjuk (dissolve). A megmaradó belső él a
    különböző szkid-ek közötti Voronoi-felezővonal.

    Ez a pontos felezővonal sok apró szakaszból áll (mikro-hullámzás), ezért
    osztály-megőrző módon EGYSZERŰSÍTJÜK: Douglas–Peucker-rel, és bináris kereséssel
    cellánként megkeressük a LEGNAGYOBB toleranciát, amelynél még minden pont a saját
    szkid-oldalán marad. A végpontok (utca-horgonyok / csomópontok) rögzítve maradnak,
    így kevés, nagy, egyenes szakaszt kapunk — mint egy utcahatár.

    Visszatérés: (rows, cells, edges)
      rows  : list[dict] {szavazokorid, color, geometry} — szkid szerint összevont al-cellák
      cells : list[dict] {szavazokorid, color, geometry} — MINDEN levágott Voronoi-cella (debug)
      edges : list[LineString]                           — egyszerűsített vágóélek (debug)
    '''

    seeds = [(row.geometry, row["szavazokorid"], row["color"])
             for _, row in points_inside.iterrows()]
    mp = MultiPoint([pt for pt, _, _ in seeds])

    # 1) Voronoi-territóriumok a magokra (a cella bbox-ára kiterjesztve), majd cellára vágva
    regions = list(voronoi_diagram(mp, envelope=polygon_geom).geoms)

    cells = []
    for reg in regions:
        for part in _extract_polys(reg.intersection(polygon_geom)):
            # a territóriumot birtokló mag a benne lévő pont (Voronoi-tulajdonság);
            # FP-biztos tartalék: a legközelebbi mag
            owner = None
            for s in seeds:
                if part.covers(s[0]):
                    owner = s
                    break
            if owner is None:
                owner = min(seeds, key=lambda s: part.distance(s[0]))
            _, szkid, color = owner
            cells.append({"szavazokorid": szkid, "color": color, "geometry": part})

    # 2) azonos szkid-ű territóriumok összevonása (dissolve) -> pontos al-cellák
    by_szkid = {}
    for c in cells:
        by_szkid.setdefault((c["szavazokorid"], c["color"]), []).append(c["geometry"])
    exact_subcells = []  # (szkid, color, geom)
    for (szkid, color), geoms in by_szkid.items():
        for part in _extract_polys(unary_union(geoms)):
            exact_subcells.append((szkid, color, part))

    color_of = {s: c for _, s, c in seeds}

    # 3) pontos belső vágóhálózat = a különböző szkid-ű al-cellák közös határa
    raw = []
    for i in range(len(exact_subcells)):
        for j in range(i + 1, len(exact_subcells)):
            if exact_subcells[i][0] == exact_subcells[j][0]:
                continue
            shared = exact_subcells[i][2].boundary.intersection(exact_subcells[j][2].boundary)
            raw += _extract_lines(shared)
    cut_lines = _merge_lines(raw)

    # ha nincs belső vágás (gyakorlatilag egy szkid) -> a pontos al-cellák a kimenet
    if not cut_lines:
        rows = [{"szavazokorid": s, "color": c, "geometry": g} for s, c, g in exact_subcells]
        return rows, cells, []

    # 4) osztály-megőrző egyszerűsítés: a LEGNAGYOBB tolerancia, amelynél MINDEN pont a
    #    saját szkid-oldalán marad. Douglas–Peucker (végpontok rögzítve), a toleranciát
    #    cellánként bináris kereséssel hangoljuk.
    boundary = polygon_geom.boundary
    minx, miny, maxx, maxy = polygon_geom.bounds
    t_max = ((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5

    def _build_pieces(tol):
        simp = []
        for ln in cut_lines:
            s = ln.simplify(tol, preserve_topology=True) if tol > 0 else ln
            if not s.is_empty:
                simp.append(_extend_boundary_ends(s, boundary, 1.0))
        if not simp:
            return []
        pieces = []
        for f in polygonize(unary_union([boundary] + simp)):
            pieces += _extract_polys(f.intersection(polygon_geom))
        return pieces

    def _assign(pieces):
        # minden magot pontosan egy darabhoz rendel; None, ha bármelyik darab kevert szkid-ű
        piece_szk = [set() for _ in pieces]
        for pt, s, _ in seeds:
            owner = None
            for k, pc in enumerate(pieces):
                if pc.contains(pt):
                    owner = k
                    break
            if owner is None:
                owner = min(range(len(pieces)), key=lambda k: pieces[k].distance(pt))
            piece_szk[owner].add(s)
        if any(len(ss) > 1 for ss in piece_szk):
            return None
        return piece_szk

    lo, hi, best_tol = 0.0, t_max, 0.0
    for _ in range(18):
        mid = (lo + hi) / 2.0
        pieces = _build_pieces(mid)
        if pieces and _assign(pieces) is not None:
            best_tol = mid
            lo = mid
        else:
            hi = mid

    # 5) végső al-cellák a legjobb toleranciával (tol=0 mindig tiszta -> mindig van megoldás)
    pieces = _build_pieces(best_tol)
    piece_szk = _assign(pieces)
    if piece_szk is None:
        pieces = _build_pieces(0.0)
        piece_szk = _assign(pieces)

    final_by_szkid = {}
    for pc, ss in zip(pieces, piece_szk):
        szkid = next(iter(ss)) if ss else min(seeds, key=lambda s: pc.distance(s[0]))[1]
        final_by_szkid.setdefault(szkid, []).append(pc)

    rows = []
    for szkid, geoms in final_by_szkid.items():
        for part in _extract_polys(unary_union(geoms)):
            rows.append({"szavazokorid": szkid, "color": color_of.get(szkid), "geometry": part})

    # 6) egyszerűsített vágóélek (voronoi_elek debug) a végső al-cellákból
    raw2 = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if rows[i]["szavazokorid"] == rows[j]["szavazokorid"]:
                continue
            shared = rows[i]["geometry"].boundary.intersection(rows[j]["geometry"].boundary)
            raw2 += _extract_lines(shared)
    edges = _merge_lines(raw2)

    return rows, cells, edges


def ures_polyk_besorolasa(results):
    """
    Azokat a sorokat kezeli, ahol szavazokorid hiányzik (NaN/None):
      - megkeresi a közös éllel érintkező, már címkézett szomszédokat
      - abba a szomszédba olvasztja (annak szkid-jét örökli), amellyel a
        LEGHOSSZABB a közös határa
    Több menetben iterál — egymás mellett lévő üres poligonok láncát is feltölti,
    amíg egyik sem marad besorolatlan (vagy elszigetelt).
    """

    out = results.copy()
    sindex = out.sindex

    while True:
        missing_idxs = out.index[out["szavazokorid"].isna()].tolist()
        if not missing_idxs:
            break

        filled_this_round = 0
        for idx in missing_idxs:
            geom = out.at[idx, "geometry"]

            candidate_idxs = list(sindex.intersection(geom.bounds))
            candidates = out.loc[candidate_idxs]

            # Csak azok a szomszédok kellenek, amelyekkel közös ÉL van (hossz > 0),
            # a pontérintkezés (figura-8) nem szomszédság. A közös határ hosszát
            # is eltároljuk, hogy a leghosszabb élű szomszédot válasszuk.
            shared_len = candidates.geometry.apply(
                lambda g: g.boundary.intersection(geom.boundary).length
            )
            neighbors_labeled = candidates[
                (shared_len > 0.01) & candidates["szavazokorid"].notna()
            ]

            if len(neighbors_labeled) == 0:
                continue

            # A leghosszabb közös határú címkézett szomszéd nyer (nem gyakoriság).
            winner_idx = shared_len.loc[neighbors_labeled.index].idxmax()

            out.at[idx, "szavazokorid"] = out.at[winner_idx, "szavazokorid"]
            out.at[idx, "color"] = out.at[winner_idx, "color"]
            filled_this_round += 1

        if filled_this_round == 0:
            # egyik üres poligon sem talált címkézett szomszédot → elszigetelt
            remaining = out["szavazokorid"].isna().sum()
            print(f"[ures_polyk_besorolasa] {remaining} cella marad besorolatlanul (elszigetelt, nincs címkézett szomszéd)")
            break

    return out


def rebuild_coverage(gdf):
    '''
    Cella-halmazt tisztán újra-topológizál coverage-ként.

    Gyökér probléma: a felez() split sub-nanométeres FP-ULP drift-et hagy a
    splittelt cella és a nem-splittelt szomszéd közös élén (~1e-8 m² mikro-
    átfedés). Ez az unary_union kimenetén 0-szélességű slit-ként mutatkozik →
    QGIS-ben „lógó random vonalak".

    Megoldás: a cellák külső határvonalainak unary_union-ja NODING-ot csinál
    (minden kvázi-koincidens metszéspont BYTE-PONTOS csúccsá válik). Ebből a
    tiszta linework-ből polygonize új, coverage-szerű cellákat ad vissza.
    A szkid / color attribútumokat centroid-alapján öröklik az eredetiből.

    Megjegyzés: a rebuild utáni cellaszám változhat (összeolvadnak mikro-
    sliverek, új cellák keletkezhetnek a noding által létrehozott csúcsokon).
    Ez topológiailag helyes.
    '''
    if len(gdf) == 0:
        return gdf.copy()

    boundaries = unary_union([g.boundary for g in gdf.geometry if g is not None and not g.is_empty])
    new_cells = list(polygonize(boundaries))

    if not new_cells:
        return gdf.copy()

    sidx = gdf.sindex
    rows = []
    for cell in new_cells:
        rp = cell.representative_point()
        cand = list(sidx.intersection(rp.bounds))
        found = None
        for j in cand:
            if gdf.geometry.iloc[j].contains(rp):
                found = j
                break
        if found is None and cand:
            found = min(cand, key=lambda j: gdf.geometry.iloc[j].distance(rp))
        if found is None:
            continue
        rows.append({
            "szavazokorid": gdf["szavazokorid"].iloc[found],
            "color": gdf["color"].iloc[found] if "color" in gdf.columns else None,
            "geometry": cell,
        })
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf.crs)


def hibas_szigetek_torlese(results, gdf, min_cimek=1):
    '''
    Végső adattisztítás: az egyesítés után különálló szigetekké váló, hibás
    forrásadat miatt keletkező EGY-CÍMES szavazókör-szigetek eltüntetése.

    Háttér: néhol egy koordinátához rossz cím → rossz szavazokorid van rendelve.
    Ez a coverage egyetlen celláját rossz szkid-hez sorolja, ami az egyesítés
    után a szavazókör távoli, EGYETLEN címet tartalmazó szigeteként jelenik meg
    (egy másik, körülvevő szavazókör területébe ágyazva). A valós, elkülönült
    szigetek (pl. egy másik szk-hez sorolt utcatömb) több címet tartalmaznak —
    ezeket nem bántjuk.

    Cella-szinten (még az egyesítés ELŐTT) dolgozik: a hibás szigetek celláinak
    szkid-jét None-ra állítja, majd az `ures_polyk_besorolasa`-val a leghosszabb
    közös határú szomszédhoz (= a körülvevő szk) sorolja át őket. A `gdf`
    címpontokat szándékosan NEM módosítja (a koordináta úgyis hibás).
    '''
    if len(results) == 0:
        return results.copy()

    # A címek 4326-ban vannak, a coverage UTM-ben — egyeztetni kell a .within-hez.
    if gdf.crs != results.crs:
        gdf = gdf.to_crs(results.crs)
    pts = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]

    out = results.copy()
    torolt_szigetek = 0
    torolt_cellak = 0

    for szkid, grp in out.groupby("szavazokorid", dropna=False):
        if szkid is None or (isinstance(szkid, float) and pd.isna(szkid)):
            continue

        parts = _extract_polys(unary_union(list(grp.geometry)))
        if len(parts) <= 1:
            # egyetlen összefüggő törzs — nem lehet elszórt hibás sziget
            continue

        counts = [int(pts.within(part).sum()) for part in parts]
        bad = [i for i, c in enumerate(counts) if c <= min_cimek]

        # Biztonsági korlát: egy szavazókör ÖSSZES szigetét sosem töröljük.
        # Ha minden sziget hibásnak minősülne, a legnagyobb területűt megtartjuk.
        if len(bad) == len(parts):
            keep = max(range(len(parts)), key=lambda i: parts[i].area)
            bad = [i for i in bad if i != keep]

        if not bad:
            continue

        # A hibás szigetekbe eső cellákat kiürítjük (újra-besorolásra jelöljük).
        for i in bad:
            part = parts[i]
            mask = grp.geometry.apply(lambda g: part.contains(g.representative_point()))
            out.loc[grp.index[mask], "szavazokorid"] = None
            out.loc[grp.index[mask], "color"] = None
            torolt_szigetek += 1
            torolt_cellak += int(mask.sum())

    print(f"[hibas_szigetek_torlese] {torolt_szigetek} egy-címes hibás sziget törölve "
          f"({torolt_cellak} cella átsorolva a körülvevő szavazókörhöz)")

    # A kiürített cellákat a leghosszabb közös határú címkézett szomszéd nyeli el.
    out = ures_polyk_besorolasa(out)
    return out


def polygonok_egyesitese(results, **_ignored):
    '''
    Szavazókörönkénti unió. rebuild_coverage után a bemenet tiszta coverage,
    így unary_union nem gyárt 0-szélességű slit-eket.
    '''

    out_rows = []

    for szkid, grp in results.groupby("szavazokorid", dropna=False):
        color = grp["color"].iloc[0] if "color" in grp.columns else None
        geom = unary_union(list(grp.geometry))
        geom = make_valid(geom)
        out_rows.append({"szavazokorid": szkid, "color": color, "geometry": geom})

    return gpd.GeoDataFrame(out_rows, geometry="geometry", crs=results.crs)
