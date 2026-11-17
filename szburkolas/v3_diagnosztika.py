"""v3_diagnosztika – a V3 burkolás debug GPKG-jének mérőszámai.

Használat:
    python v3_diagnosztika.py <település>_debug.gpkg

Csak olvas. Egy `<település>_debug.gpkg` rétegeiből kiszámolja azokat a mutatókat, amelyek a
Budapest XX. kerületi hibák okát megmutatták (vonalháló összetétele, cellaszám lépésenként,
mikrocellák, T-csomópontok eredete, az üres cellák besorolása, a körzethatár összetétele és
töredezettsége, egycímes átnyúlások). Javítás előtt és után lefuttatva a két kimenet soronként
összevethető.

Függőségek: geopandas, shapely >= 2.0, pandas, numpy, pyogrio.
"""
import sys
from collections import Counter, defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from shapely.ops import linemerge, polygonize, unary_union

TELEKSZELESSEG = 6.0
SAROKMERET = 30.0
TUR = 0.02  # illesztési tűrés (2 × RÁCS)


def fo(utvonal):
    retegek = {n for n, _ in pyogrio.list_layers(utvonal)}

    def R(nev):
        if nev not in retegek:
            return None
        return gpd.read_file(utvonal, layer=nev, engine="pyogrio")

    def sor(cimke, ertek):
        print(f"  {cimke:<62s} {ertek}")

    def beirt(geoms):
        return np.array([shapely.maximum_inscribed_circle(g).length * 2 for g in geoms])

    # ------------------------------------------------------------------ 1. vonalháló
    print("\n1. VONALHÁLÓ (1_7_vonalhalo)")
    vh = R("1_7_vonalhalo").reset_index(drop=True)
    vh["L"] = vh.length
    vh["nevtelen"] = vh.utca_id.fillna("").str.startswith("osm_")
    r3 = vh[vh.rang == 3]
    sor("rang 3 hossz, névtelen (km)", f"{r3.L[r3.nevtelen].sum() / 1000:.1f}")
    sor("rang 3 hossz, neves (km)", f"{r3.L[~r3.nevtelen].sum() / 1000:.1f}")
    sor("rang 4–5 hossz (km)", f"{vh.L[vh.rang.isin([4, 5])].sum() / 1000:.1f}")
    sor("különböző utca_id", f"{vh.utca_id.nunique()}  (ebből névtelen, osm_*: {vh.utca_id[vh.nevtelen].nunique()})")
    tombok = R("2_1_tombok")
    sor("tömbök a poligonizálás után (2_1)", len(tombok))
    sl = R("3_1_sliverek")
    sor("sliverek (3_1)", 0 if sl is None else len(sl))
    valodi = list(polygonize(unary_union(list(vh[~((vh.rang == 3) & vh.nevtelen)].geometry.values))))
    sor("tömbök névtelen rang 3 nélkül (számított)", f"{len(valodi)}  (ebből 6 m-nél keskenyebb: {(beirt(valodi) < TELEKSZELESSEG).sum()})")

    # ------------------------------------------------------------------ 2. sarkok
    sc = R("5_sarok_csomopontok")
    if sc is not None:
        print("\n2. SAROKCELLÁK (5.)")
        n1 = sc.utca_1.fillna("").str.startswith("osm_")
        n2 = sc.utca_2.fillna("").str.startswith("osm_")
        sor("sarok-csomópontok", f"{len(sc)}  (ebből névtelen utcával: {(n1 | n2).sum()})")
        sn = R("5_sarok_negyszogek")
        sor("sarok-négyszög < 100 m²", int((sn.area < 100).sum()))

    # ------------------------------------------------------------------ 3. cellák lépésenként
    print("\n3. CELLÁK LÉPÉSENKÉNT")
    print(f"  {'réteg':<18s}{'cella':>8s}{'< 1 m²':>9s}{'beírt átm. < 6 m':>18s}{'medián m²':>11s}")
    for nev in ["2_1_tombok", "3_R_cellak", "4_R_savok", "5_R_cellak", "6_R_cellak", "8_R_cellak", "9_R_cellak"]:
        g = R(nev)
        if g is None:
            continue
        a = g.area.values
        d = beirt(g.geometry.values)
        print(f"  {nev:<18s}{len(g):>8d}{(a < 1).sum():>9d}{(d < TELEKSZELESSEG).sum():>18d}{np.median(a):>11.1f}")
    c7 = R("7_besorolas")
    if c7 is not None:
        u = c7[c7["allapot_"] == "üres"]
        sor("üres cellák a 7. lépésben", f"{len(u)} / {len(c7)}  (terület {u.area.sum() / 1e4:.1f} ha = {u.area.sum() / c7.area.sum() * 100:.1f} %, medián {u.area.median():.1f} m²)")

    # ------------------------------------------------------------------ 4. T-csomópontok eredete
    print("\n4. T-CSOMÓPONTOK EREDETE")
    vegek = defaultdict(list)
    for i, g in enumerate(vh.geometry.values):
        co = g.coords
        for q in (co[0], co[-1]):
            vegek[(round(q[0], 2), round(q[1], 2))].append(i)
    vt = shapely.STRtree(vh.geometry.values)
    rang = vh.rang.values
    nevtelen = vh.nevtelen.values
    for nev in ["3_2_T_csomopontok", "6_T_csomopontok"]:
        T = R(nev)
        if T is None:
            continue
        pts = T.geometry.values
        halon = np.zeros(len(pts), bool)
        qi, _ = vt.query(pts, predicate="dwithin", distance=TUR)
        halon[np.unique(qi)] = True
        o = Counter()
        for k, pt in enumerate(pts):
            ids = vegek.get((round(pt.x, 2), round(pt.y, 2)), [])
            if not halon[k]:
                o["A szerkesztett vonalak találkozása a tömb belsejében"] += 1
            elif len(ids) < 3:
                o["B hálózati vonalon, de az odaérő vonal szerkesztett"] += 1
            else:
                eros = [i for i in ids if rang[i] >= 4 or rang[i] == 2]
                neves3 = [i for i in ids if rang[i] == 3 and not nevtelen[i]]
                if len(eros) >= 3:
                    o["D valódi keresztutca (≥ 3 rang ≥ 4 él)"] += 1
                elif len(eros) + len(neves3) >= 3:
                    o["C2 neves rang 3 becsatlakozás"] += 1
                else:
                    o["C1 névtelen rang 3 becsatlakozás"] += 1
        print(f"  {nev}: {len(pts)}")
        for a, b in sorted(o.items()):
            print(f"     {a:<58s}{b:>7d}{b / len(pts) * 100:>7.1f} %")

    # ------------------------------------------------------------------ 5. üres cellák besorolása
    c9 = R("9_R_cellak")
    if c9 is not None:
        print("\n5. ÜRES CELLÁK BESOROLÁSA (9.)")
        c9 = c9.reset_index(drop=True)
        c9["A"] = c9.area
        for sz, g in c9.groupby("szabaly", dropna=False):
            sor(f"szabály: {sz}", f"{len(g):>6d} cella, {g.A.sum() / 1e4:7.1f} ha, medián {g.A.median():.2f} m²")
        a91 = R("9_1_folytonossag")
        if a91 is not None:
            L = a91.length
            sor("9.1 öröklési távolság (repr. pontok között)", f"medián {L.median():.0f} m, 90 %: {L.quantile(.9):.0f} m, max {L.max():.0f} m")
            sor("9.1 eltérő-folytonosság jelzés", f"{(a91.jelzes.fillna('') != '').sum()} / {len(a91)}")
        a92 = R("9_2_rang")
        if a92 is not None:
            sor("9.2 öröklés utcán át (nyertes rang ≥ 4)", int((a92.nyertes_rang >= 4).sum()))
            sor("9.2 öröklés rang 3 élen át", int((a92.nyertes_rang == 3).sum()))
        sz = R("10_1_szigetek")
        if sz is not None:
            sor("10.1 gyanús szigetek", f"{len(sz)}  (0 címes: {(sz.cimek_szama == 0).sum()}, 1 címes: {(sz.cimek_szama == 1).sum()})")

    # ------------------------------------------------------------------ 6. körzethatár
    k = R("10_2_korzetek")
    if k is not None:
        print("\n6. KÖRZETHATÁR (két körzet közös határa)")
        geoms = k.geometry.values
        fa = shapely.STRtree(geoms)
        ia, ib = fa.query(geoms, predicate="intersects")
        m = ia < ib
        szak = []
        for a, b in zip(ia[m], ib[m]):
            g = shapely.intersection(shapely.boundary(geoms[a]), shapely.boundary(geoms[b]))
            ls = [q for q in shapely.get_parts(g) if q.geom_type == "LineString" and q.length > 0]
            if not ls:
                continue
            for q in shapely.get_parts(linemerge(ls)):
                q = shapely.simplify(q, TUR, preserve_topology=False)  # kollineáris csúcsok összevonva
                co = np.asarray(q.coords)
                szak += [shapely.LineString([co[i], co[i + 1]]) for i in range(len(co) - 1)]
        szak = np.array(szak)
        L = shapely.length(szak)
        kozep = shapely.line_interpolate_point(szak, 0.5, normalized=True)
        qi, vi = vt.query(kozep, predicate="dwithin", distance=0.05)
        r = np.ones(len(szak), dtype=int)
        mx = pd.DataFrame({"q": qi, "r": rang[vi]}).groupby("q").r.max()
        r[mx.index.values] = mx.values
        nevek = {1: "szerkesztett vonal (váz-/vágóvonal)", 2: "segédvonal", 3: "rang 3 (gyalogút, szervizút)",
                 4: "rang 4 (lakóutca)", 5: "rang 5 (főút)", 6: "vasút, folyó", 7: "lakott határ"}
        sor("összes hossz (km)", f"{L.sum() / 1000:.1f}")
        for rr in sorted(set(r)):
            sor(f"  ebből {nevek[rr]}", f"{L[r == rr].sum() / 1000:6.1f} km  {L[r == rr].sum() / L.sum() * 100:5.1f} %")
        sor("egyenes szakaszok száma", len(szak))
        sor("egyenes szakasz medián hossza (m)", f"{np.median(L):.1f}")
        sor("5 m-nél rövidebb szakaszok", f"{(L < 5).sum()}  ({(L < 5).mean() * 100:.0f} %)")
        sor("töréspont / km", f"{len(szak) / (L.sum() / 1000):.0f}")
        v4 = linemerge(list(vh[vh.rang >= 4].geometry.values))
        n = tot = 0
        for q in shapely.get_parts(v4):
            q = shapely.simplify(q, TUR, preserve_topology=False)
            n += len(q.coords) - 1
            tot += q.length
        sor("viszonyítás: a rang ≥ 4 utcaháló töréspont / km", f"{n / (tot / 1000):.0f}")
        sor("körzetek / körzet-darabok", f"{len(k)} / {sum(len(g.geoms) if g.geom_type == 'MultiPolygon' else 1 for g in geoms)}")

    # ------------------------------------------------------------------ 7. egycímes átnyúlások
    cv = R("10_1_9_R_cellak")
    if cv is None:
        cv = c9
    cim = R("2_3_cimek")
    if cv is not None and cim is not None:
        print("\n7. EGYCÍMES ÁTNYÚLÁSOK A VÉGEREDMÉNYBEN")
        cv = cv.reset_index(drop=True)
        g = cv.geometry.values
        fa = shapely.STRtree(g)
        ia, ib = fa.query(g, predicate="intersects")
        m = ia < ib
        ia, ib = ia[m], ib[m]
        hat = shapely.boundary(g)
        met = shapely.intersection(hat[ia], hat[ib])
        Lm = shapely.length(met)
        ok = Lm > TUR
        elek = []
        for a, b, q, l in zip(ia[ok], ib[ok], met[ok], Lm[ok]):
            reszek = [x for x in shapely.get_parts(q) if x.geom_type == "LineString" and x.length > 0]
            if not reszek:
                continue
            f = max(reszek, key=lambda s: s.length).interpolate(0.5, normalized=True)
            j = vt.query(f, predicate="dwithin", distance=TUR)
            if len(j):
                kk = j[np.argmax(rang[j])]
                elek.append((a, b, int(rang[kk]), vh.utca_id[kk], l))
            else:
                elek.append((a, b, 1, None, l))
        sz = cv.szkid.values
        j = gpd.sjoin(cim[["szkid", "geometry"]], cv[["geometry"]], predicate="within")
        ncim = Counter(j.index_right)
        szulo = list(range(len(cv)))

        def gy(x):
            while szulo[x] != x:
                szulo[x] = szulo[szulo[x]]
                x = szulo[x]
            return x

        for a, b, rr, u, l in elek:
            if rr <= 3 and sz[a] == sz[b] and not pd.isna(sz[a]):
                szulo[gy(a)] = gy(b)
        darab = np.array([gy(i) for i in range(len(cv))])
        db = Counter()
        for i in range(len(cv)):
            db[darab[i]] += ncim.get(i, 0)
        erint = defaultdict(set)
        mas = Counter()
        for a, b, rr, u, l in elek:
            if pd.isna(sz[a]) or pd.isna(sz[b]):
                continue
            if rr >= 4 and sz[a] == sz[b] and darab[a] != darab[b]:
                erint[darab[a]].add(u)
                erint[darab[b]].add(u)
            if rr <= 3 and sz[a] != sz[b]:
                mas[darab[a]] += l
                mas[darab[b]] += l
        osszes = set(darab[~pd.isna(sz)])
        egy = [x for x in osszes if db[x] == 1]
        sor("tömbön belüli kategória-darabok (utcán át nem összefüggő)", len(osszes))
        sor("  ebből pontosan egy címes", len(egy))
        sor("  egy címes, saját kategóriáját csak EGY utcán át éri", sum(1 for x in egy if len(erint[x]) == 1))
        sor("    … és a tömbön belül más kategória a szomszédja", sum(1 for x in egy if len(erint[x]) == 1 and mas[x] > 0))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    fo(sys.argv[1])
