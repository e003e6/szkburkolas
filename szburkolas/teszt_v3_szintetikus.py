"""
teszt_v3_szintetikus – a V3 burkolás lépésenkénti ellenőrzése egy mesterséges mini-településen.

Futtatás: cd szburkolas && ../.venv/bin/python teszt_v3_szintetikus.py

A település (méterben, UTM 34N koordináta-rendszerben értelmezve):
  - lakott folt 0..320 × 0..200, lyukkal (120..140 × 80..100), a közigazgatási határ x=300-nál vág;
  - külön, utca nélküli lakott folt 400..450 × 0..50;
  - Fő út (rang 5) y=100-on; Petőfi (x=100) és Kossuth (x=200) utca; Zsák utca (zsákutca x=250,
    y 100..160 → narancs a lakott határig); egy bejáró (rang 3, lógó → törlődik); egy köz (rang 3,
    két utcát összekötő → marad); Határ menti utca y=197-en (3 m-es sliver a határ mellett);
    vasút y=20-on;
  - két kategória: 1 (a Fő út felett), 2 (a Fő út alatt és a Fő úttól keletre).
"""

import sys
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import LineString, Point, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v3_param as P
import v3_io
import v3_topologia as T
import v3_vonalhalo as V
import v3_sliver as S
import v3_savok as SV
import v3_felosztas as F
import v3_besorolas as B

CRS = 'EPSG:32634'


def szintetikus_bemenet():
    lakott_nyers = gpd.GeoSeries([
        Polygon(box(0, 0, 320, 200).exterior.coords, [box(120, 80, 140, 100).exterior.coords]),
        box(400, 0, 450, 50),
    ], crs=CRS)
    kozig = box(-50, -50, 300, 300).union(box(380, -50, 500, 300))
    vonalak = [
        ('utca', 5, 'fout', 'primary', 'Fő út', LineString([(-20, 100), (340, 100)])),
        ('utca', 4, 'petofi', 'residential', 'Petőfi utca', LineString([(100, -20), (100, 220)])),
        ('utca', 4, 'kossuth', 'residential', 'Kossuth utca', LineString([(200, -20), (200, 220)])),
        ('utca', 4, 'zsak', 'residential', 'Zsák utca', LineString([(250, 100), (250, 160)])),
        ('utca', 3, 'bejaro', 'footway', None, LineString([(50, 0), (50, 60)])),
        ('utca', 3, 'koz', 'footway', None, LineString([(100, 150), (200, 150)])),
        ('utca', 4, 'hatarmenti', 'residential', 'Határ menti utca', LineString([(-20, 197), (100, 197)])),
        ('vasút', 6, None, None, None, LineString([(-20, 20), (340, 20)])),
    ]
    vonalak_nyers = gpd.GeoDataFrame(
        [{'tipus': t, 'rang': r, 'utca_id': u, 'highway': h, 'name': n, 'geometry': g} for t, r, u, h, n, g in vonalak],
        geometry='geometry', crs=CRS)
    pontok = []
    for x in range(110, 200, 20):
        pontok += [(1, Point(x, 190)), (1, Point(x, 110)), (2, Point(x, 90)), (2, Point(x, 30))]
    for x in range(10, 100, 20):
        pontok += [(1, Point(x, 110)), (1, Point(x, 90))]
    for x in range(210, 300, 20):
        pontok += [(2, Point(x, 110)), (2, Point(x, 90))]
    pontok.append((2, Point(425, 25)))
    cimek = gpd.GeoDataFrame({'szavazokorid': [k for k, _ in pontok], 'utca': 'x', 'hazszam': '1', 'irsz': '1000',
                              'telepules': 'Szint'}, geometry=[p for _, p in pontok], crs=CRS)
    return v3_io.Bemenet(varos='Szint', crs=CRS, cimek=v3_io.cimek_elokeszites(cimek.to_crs('EPSG:4326'), 'Szint', CRS),
                         lakott_nyers=lakott_nyers, kozig_hatar=kozig, vonalak_nyers=vonalak_nyers)


def kozel(a, b, tures=0.5):
    return abs(a - b) <= tures


def teszt():
    b = szintetikus_bemenet()
    debug = v3_io.DebugIro(None, CRS)

    # 0.2: a közigazgatási határ x=300-nál vág, a folt megmarad
    lak = V.lepes_0_2_lakott_vagas(b, debug)
    assert len(lak) == 2 and kozel(lak.area.sum(), 300 * 200 - 20 * 20 + 50 * 50), '0.2 lakott vágás'

    # 0.3–0.4: a második folt utca nélküli
    vag, qa_b = V.lepes_0_3_vonalak_vagasa(b.vonalak_nyers, lak, debug)
    foltok = V.lepes_0_4_foltok(lak, vag, b.cimek, qa_b, debug)
    assert len(foltok.utca_nelkuli_foltok) == 1, '0.4 utca nélküli folt'
    assert kozel(foltok.lakott_terulet.area, 300 * 200 - 400), '0.4 hálózatos terület'

    # 1.: a bejáró (rang 3, lógó) törlődik, a köz marad, a zsákutca narancsot kap
    vh = V.lepes_1_vonalhalo(foltok, vag, debug)
    assert vh.n_rang3_torolt == 1, '1.4 rang 3 lógó vég'
    assert vh.n_narancs == 1, '1.5 narancs'
    assert 'koz' in vh.utca_vonalak and 'zsak' in vh.utca_vonalak, '1.7 köz és zsákutca a hálóban'
    assert T.polygonize_full_nev_szerint(list(vh.tabla.geometry))['logo'] == [], '1.7 nincs lógó vég'

    # 2.: cellák – a lyuk nem cella; a folt egy cella; az összterület a lakott terület
    cellak, eltabla, cimek = V.lepes_2_cellak(vh, foltok, b.cimek, debug)
    assert (cellak['cellatipus'] == P.CELLA_FOLT).sum() == 1, '2.2 foltcella'
    assert kozel(cellak.area.sum(), foltok.lakott_terulet.area + 2500), '2.1 teljes lefedés, lyuk nélkül'
    assert ((eltabla['cella_bal'] == P.KULVILAG) & (eltabla['cella_jobb'] == P.KULVILAG)).sum() == 0, '2.3 éltábla oldalak'
    # a (130, 90) cím a lakott folt lyukában ül: nem tartozik cellához; minden más cím cellában van
    cellan_kivul = cimek[cimek['cella_id'].isna()]
    assert len(cellan_kivul) == 1 and cellan_kivul.geometry.iloc[0].distance(Point(130, 90)) < 0.01, '2.3 címek a cellákban'
    terulet_2 = cellak.area.sum()

    # 3.: a 3 m-es határ menti sliver beolvad, a terület nem változik
    c3, e3, ci3 = S.lepes_3_sliver_tisztitas(cellak, eltabla, cimek, debug)
    assert not c3['sliver'].any() and kozel(c3.area.sum(), terulet_2), '3. sliver beolvasztás'
    assert len(c3) == len(cellak) - 1, '3. egy sliver olvadt be'

    # 4.: a Fő út–Petőfi–Kossuth–köz tömb 4 sávra bomlik, a vázvonalak egyenesek
    tombok, e_tombok = c3.copy(), e3.copy()
    c4, e4, ci4 = SV.lepes_4_savositas(c3, e3, ci3, vh.utca_vonalak, debug)
    tomb11 = c3[(c3.geometry.bounds.minx >= 99) & (c3.geometry.bounds.maxx <= 201) & (c3.geometry.bounds.miny >= 99) & (c3.geometry.bounds.maxy <= 151)]
    savok = c4[c4['tomb_id'] == tomb11.iloc[0]['cella_id']]
    assert len(savok) == 4 and set(savok['utca_id']) == {'fout', 'petofi', 'kossuth', 'koz'}, '4.3 négy sáv'
    assert kozel(c4.area.sum(), terulet_2), '4. terület'
    assert all(len(shapely.get_coordinates(g)) <= 12 for g in e4.loc[e4['tipus'] == P.TIPUS_VAZVONAL, 'geometry']), '4.3 egyenes vázvonal'

    # 5.: a tömb 4 sarkán sarokcella, 30 × 25 (a Petőfi/Kossuth oldal 50 m → 25 m); a Fő út–Petőfi
    #     saroknál a Fő út e tömb menti lánca csak 20 m (a lyuk felső éle lakott határ) → 10 × 25
    c5, e5, ci5 = SV.lepes_5_sarokcellak(c4, e4, ci4, tombok, e_tombok, debug)
    sarkok = c5[(c5['cellatipus'] == P.CELLA_SAROK) & (c5['tomb_id'] == tomb11.iloc[0]['cella_id'])]
    assert len(sarkok) == 4, '5. négy sarokcella'
    assert all(kozel(a, b, 5) for a, b in zip(sorted(sarkok.area), [250, 750, 750, 750])), '5. sarokcellák mérete'
    assert kozel(c5.area.sum(), terulet_2), '5. terület'

    # 6.–7.: üres cellák T-feldarabolása, besorolás
    kategoriak = T.cella_kategoriak(ci5)
    ures = {c for c in c5['cella_id'] if c not in kategoriak}
    c6, _, _ = T.t_feldarabolas(c5, e5, ures)
    c6, e6, ci6 = T.topologia_frissites(c6, e5, ci5)
    c7, ci7, vegyes, n_azonos = F.lepes_7_besorolas(c6, ci6, debug)
    assert n_azonos == 0, '7. nincs azonos pont'

    # 8.: minden vegyes cella egykategóriás darabokra esik; egyetlen cím sem kerül rossz cellába
    c8, e8, ci8, stat = F.lepes_8_vegyes_felosztas(c7, e6, ci7, vegyes, vh.utca_vonalak, debug)
    kategoriak = T.cella_kategoriak(ci8)
    assert all(len(k) == 1 for k in kategoriak.values()), '8. nincs vegyes cella'
    assert kozel(c8.area.sum(), terulet_2), '8. terület'

    # 9.–10.: minden cella besorolva, validáció rendben
    c9, e9, ci9 = B.lepes_9_ures_besorolas(c8, e8, ci8, vh.utca_vonalak, debug)
    assert c9['szkid'].notna().all(), '9. minden cella besorolva'
    c10, ci10, gyanus = B.lepes_10_1_szigetek(c9, ci9, debug)
    if gyanus:
        c10, e9, ci10 = B.lepes_9_ures_besorolas(c10, e9, ci10, vh.utca_vonalak, debug, lepes_elotag='10_1_9', folytonossag=False)
    c10, e10, ci10 = T.topologia_frissites(c10, e9, ci10)
    korzetek = B.lepes_10_2_egyesites(c10, CRS)
    ok, hibak = B.lepes_10_3_validacio(korzetek, terulet_2, ci10, debug)
    assert ok, f'10.3 validáció: {hibak}'
    assert set(korzetek['szkid'].dropna()) == {1, 2}, '10.2 két körzet'
    # a Fő út felett 1-es, alatta 2-es kategória: a (150, 150) pont az 1-esben, a (150, 50) a 2-esben
    k1 = korzetek[korzetek['szkid'] == 1].geometry.iloc[0]
    k2 = korzetek[korzetek['szkid'] == 2].geometry.iloc[0]
    assert k1.contains(Point(150, 150)) and k2.contains(Point(150, 50)) and k2.contains(Point(425, 25)), '10.2 körzetek helye'
    print('teszt_v3_szintetikus: minden ellenőrzés rendben', dict(stat))


if __name__ == '__main__':
    teszt()
