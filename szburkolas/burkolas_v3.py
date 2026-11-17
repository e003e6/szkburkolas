"""
burkolas_v3 – a B. burkoló egység V3 folyamata egy településre.

A leírás (B-burkolas-v3-mukodes.md) lépései sorban, a lépés sorszámával kommentben. Minden
lépés külön modulban van:
    v3_param      0.4–0.5  paraméterek, rangok, színek
    v3_io         Bemenetek, 11., 11.1  betöltés, debug GPKG, kimenet
    v3_vonalhalo  0.2–0.4, 1., 2.      előkészítés, vonalháló, cellák
    v3_topologia  R., 1.3, 3.2, 6.     R. rutin, éltábla, T-feldarabolás
    v3_sliver     3.                    sliver-tisztítás
    v3_savok      4., 5.                sávosítás, sarokcellák
    v3_felosztas  7., 8.                besorolás, vegyes cellák felosztása
    v3_besorolas  9., 10., 12.          üres cellák, lezárás, validáció, mutatók

Használat egy településre (debug GPKG-vel, az adatok/ mappába):
    cd szburkolas && ../.venv/bin/python burkolas_v3.py
Kötegelt futtatás: burkolas_multiple.py.
"""

import time
from dataclasses import dataclass, field

import geopandas as gpd
import shapely

import v3_param as P
import v3_io
import v3_topologia as T
import v3_vonalhalo as V
import v3_sliver as S
import v3_savok as SV
import v3_felosztas as F
import v3_besorolas as B


@dataclass
class BurkolasEredmeny:
    """Egy település burkolásának eredménye (helyi UTM-vetületben)."""
    varos: str
    status: str                              # 'ok' | 'nincs' (nincs lakott terület / nincs cím) | 'ures'
    korzetek: gpd.GeoDataFrame | None = None   # szkid, jelzes, szin, geometry
    cimek: gpd.GeoDataFrame | None = None      # szkid, utca, hazszam, irsz, jelzes, szin, geometry
    naplo: dict = field(default_factory=dict)  # 12. fejezet mutatói
    validacio_hibak: list = field(default_factory=list)
    uzenet: str = ''


class _Ido:
    """Lépésidők kiírása (csak debug futásnál)."""

    def __init__(self, aktiv):
        self.aktiv = aktiv
        self.t0 = time.time()

    def __call__(self, cimke):
        if self.aktiv:
            print(f'  [{time.time() - self.t0:6.1f}s] {cimke}')


def _szin_oszlop(korzetek, cimek, debug_iro):
    """11. kimenet: a körzetek és a címek `szin` oszlopa a kategória (szkid) palettájából – a körzet
    és a hozzá tartozó címek azonos színűek, QGIS-ben adatvezérelt kitöltéssel egyből olvasható."""
    korzetek = korzetek.copy()
    cimek = cimek.copy()
    korzetek['szin'] = [debug_iro.kategoria_szin(s) for s in korzetek['szkid']]
    cimek['szin'] = [debug_iro.kategoria_szin(s) for s in cimek['szkid']]
    return korzetek, cimek


def burkol_telepules(varos, cimek_wgs, debug=False, debug_dir=v3_io.ADATOK_DIR):
    """A B egység teljes folyamata egy településre (Budapesten kerületre).

    varos      a település neve (a `telepules` oszlop értéke)
    cimek_wgs  az A egység címpontjai (WGS84), legalább a település sorai
    debug      True → `<debug_dir>/<varos>_debug.gpkg` minden lépés minden rétegével (11.1)
    """
    ido = _Ido(debug)
    if cimek_wgs is None or len(cimek_wgs) == 0:
        return BurkolasEredmeny(varos, 'nincs', uzenet='nincs címpont a településen')

    # ── Bemenetek ──────────────────────────────────────────────────────────────
    try:
        bemenet = v3_io.betolt_telepules(varos, cimek_wgs)
    except v3_io.NincsLakottTerulet as ex:
        return BurkolasEredmeny(varos, 'nincs', uzenet=str(ex))
    cimek = bemenet.cimek
    if cimek.empty:
        return BurkolasEredmeny(varos, 'nincs', uzenet='nincs címpont a településen')
    debug_iro = v3_io.DebugIro(debug_dir / f'{varos}_debug.gpkg' if debug else None, bemenet.crs)
    debug_iro.paletta = P.kategoria_paletta(cimek['szkid'])
    ido(f'betöltve: {len(cimek)} cím, {cimek["szkid"].nunique()} kategória, {len(bemenet.vonalak_nyers)} vonal')

    # ── 0.2 Lakott terület vágása a közigazgatási határra ─────────────────────
    try:
        lakott_vagott = V.lepes_0_2_lakott_vagas(bemenet, debug_iro)
    except v3_io.NincsLakottTerulet as ex:
        return BurkolasEredmeny(varos, 'nincs', uzenet=str(ex))

    # ── 0.1 Egykategóriás település (rövidített út) ───────────────────────────
    if cimek['szkid'].nunique() == 1:
        szkid = int(cimek['szkid'].iloc[0])
        korzet = shapely.unary_union(list(lakott_vagott), grid_size=P.RACS)
        korzetek = gpd.GeoDataFrame({'szkid': [szkid], 'jelzes': ['']}, geometry=[korzet], crs=bemenet.crs)
        korzetek, cimek = _szin_oszlop(korzetek, cimek, debug_iro)
        debug_iro.reteg('10_2_korzetek', '10.2', 'körzet', korzetek.geometry, [debug_iro.kategoria_szin(szkid)],
                        szkid=[szkid], jelzes=[''])
        naplo = {'n_cella': 1, 'n_korzet': 1, 'n_kategoria': 1, 'egykategorias': True,
                 'n_cim_hataron_kivul': bemenet.n_cim_hataron_kivul}
        ido('0.1 egykategóriás település: kész')
        return BurkolasEredmeny(varos, 'ok', korzetek, cimek, naplo)

    # Ha a cache-ben nincs utcahálózat a településhez (letöltési hiba vagy valóban nincs), a folyamat
    # a 0.4 szerint minden foltot utca nélküliként, egy-egy cellaként kezel. Többkategóriás településen
    # ez szinte biztosan hiányzó adat: a napló jelzi, hogy a letöltést meg kell ismételni.
    nincs_utcahalozat = bemenet.vonalak_nyers.empty
    if nincs_utcahalozat:
        print(f'  [0.3] FIGYELEM ({varos}): nincs utcahálózat a cache-ben (utcak_all_edges.parquet) – '
              f'ellenőrizd az osm_letoltes_hibak.txt-t')

    # ── 0.3 Vonalhálózat vágása a lakott területre ────────────────────────────
    vonalak_vagott, qa_foutak_kivul = V.lepes_0_3_vonalak_vagasa(bemenet.vonalak_nyers, lakott_vagott, debug_iro)

    # ── 0.4 Foltok szétválasztása (hálózatos / utca nélküli) + QA-jelzés ──────
    foltok = V.lepes_0_4_foltok(lakott_vagott, vonalak_vagott, cimek, qa_foutak_kivul, debug_iro)
    ido(f'0. előkészítés: {len(vonalak_vagott)} vonal, {len(foltok.utca_nelkuli_foltok)} utca nélküli folt, '
        f'QA címek kívül {foltok.qa_cimek_kivul:.1%}, főutak kívül {qa_foutak_kivul:.1%}')

    # ── 1. A vonalháló ────────────────────────────────────────────────────────
    vonalhalo = V.lepes_1_vonalhalo(foltok, vonalak_vagott, debug_iro)
    ido(f'1. vonalháló: {len(vonalhalo.tabla)} darab, rang 3 törölt {vonalhalo.n_rang3_torolt}, '
        f'narancs {vonalhalo.n_narancs}, lógó/cut törölt {vonalhalo.n_logo_torolt}')

    # ── 2. Cellák képzése ─────────────────────────────────────────────────────
    cellak, eltabla, cimek = V.lepes_2_cellak(vonalhalo, foltok, cimek, debug_iro)
    if cellak.empty:
        return BurkolasEredmeny(varos, 'ures', uzenet='a poligonizálás nem adott cellát')
    referencia_terulet = float(cellak.area.sum())   # a 10.3 lefedés-ellenőrzés alapja
    ido(f'2. cellák: {len(cellak)}, élek {len(eltabla)}')

    # ── 3. Sliver-tisztítás ───────────────────────────────────────────────────
    cellak, eltabla, cimek = S.lepes_3_sliver_tisztitas(cellak, eltabla, cimek, debug_iro)
    tombok, eltabla_tombok = cellak.copy(), eltabla.copy()   # a 4. és 5. lépés a tömbökből dolgozik
    ido(f'3. sliver-tisztítás: {len(cellak)} cella')

    # ── 4. Tömbök felbontása utcafront-sávokra ────────────────────────────────
    cellak, eltabla, cimek = SV.lepes_4_savositas(cellak, eltabla, cimek, vonalhalo.utca_vonalak, debug_iro)
    ido(f'4. sávosítás: {len(cellak)} cella')

    # ── 5. Sarokcellák ────────────────────────────────────────────────────────
    cellak, eltabla, cimek = SV.lepes_5_sarokcellak(cellak, eltabla, cimek, tombok, eltabla_tombok, debug_iro)
    ido(f'5. sarokcellák: {len(cellak)} cella')

    # ── 6. Üres cellák T-feldarabolása ────────────────────────────────────────
    kategoriak = T.cella_kategoriak(cimek)
    ures_idk = {c for c in cellak['cella_id'] if c not in kategoriak}
    cellak, t_pontok, t_vonalak = T.t_feldarabolas(cellak, eltabla, ures_idk)
    debug_iro.reteg('6_T_csomopontok', '6', 'T-csomópont', t_pontok, P.SZIN['T-vágás'])
    debug_iro.reteg('6_T_vagasok', '6', 'T-vágóvonal', t_vonalak, P.SZIN['T-vágás'])
    cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek)
    debug_iro.cellak('6_R_cellak', '6.R', cellak)
    ido(f'6. üres cellák T-feldarabolása: {len(cellak)} cella, {len(t_vonalak)} vágás')

    # ── 7. Besorolás ──────────────────────────────────────────────────────────
    cellak, cimek, vegyes_idk, n_azonos_pont = F.lepes_7_besorolas(cellak, cimek, debug_iro)
    cellak_7, cimek_7 = cellak.copy(), cimek.copy()   # a 12. mutatókhoz (vegyes arány a besorolás előtt)
    ido(f'7. besorolás: {len(vegyes_idk)} vegyes cella, {n_azonos_pont} azonos-pont cím')

    # ── 8. Vegyes cellák felosztása ───────────────────────────────────────────
    cellak, eltabla, cimek, vagas_tipusok = F.lepes_8_vegyes_felosztas(
        cellak, eltabla, cimek, vegyes_idk, vonalhalo.utca_vonalak, debug_iro)
    ido(f'8. felosztás: {len(cellak)} cella, vágások {dict(vagas_tipusok)}')

    # ── 9. Üres cellák besorolása ─────────────────────────────────────────────
    cellak, eltabla, cimek = B.lepes_9_ures_besorolas(cellak, eltabla, cimek, vonalhalo.utca_vonalak, debug_iro)
    ido(f'9. üres cellák: {int(cellak["szkid"].isna().sum())} besorolatlan maradt')

    # ── 10.1 Hibás, egycímes szigetek → újra 9. lépés rájuk ───────────────────
    cellak, cimek, gyanus = B.lepes_10_1_szigetek(cellak, cimek, debug_iro)
    if gyanus:
        cellak, eltabla, cimek = B.lepes_9_ures_besorolas(cellak, eltabla, cimek, vonalhalo.utca_vonalak,
                                                          debug_iro, lepes_elotag='10_1_9', folytonossag=False)
    ido(f'10.1 szigetek: {len(gyanus)} gyanús cella')

    # ── 10.2 Egyesítés kategóriánként ─────────────────────────────────────────
    cellak, eltabla, cimek = T.topologia_frissites(cellak, eltabla, cimek)
    korzetek = B.lepes_10_2_egyesites(cellak, bemenet.crs)

    # ── 10.3 Validáció és napló ───────────────────────────────────────────────
    validacio_ok, hibak = B.lepes_10_3_validacio(korzetek, referencia_terulet, cimek, debug_iro)
    if not validacio_ok:
        korzetek['jelzes'] = [F.jelzes_hozzaad(j, P.JELZES_VALIDACIO) for j in korzetek['jelzes']]
        print(f'  [10.3] VALIDÁCIÓS HIBA ({varos}): ' + ' | '.join(hibak))
    debug_iro.reteg('10_2_korzetek', '10.2', 'körzet', korzetek.geometry,
                    [debug_iro.kategoria_szin(s) for s in korzetek['szkid']],
                    szkid=korzetek['szkid'].tolist(), jelzes=korzetek['jelzes'].tolist())
    naplo = B.szerkezeti_mutatok(cellak_7, cimek_7, vagas_tipusok, korzetek, vonalhalo.n_rang3_torolt,
                                 vonalhalo.n_narancs, foltok.qa_cimek_kivul, qa_foutak_kivul, foltok.lakott_gyanus,
                                 n_azonos_pont, int(cimek['jelzes'].str.contains(P.JELZES_HIBAS_SZIGET).sum()),
                                 validacio_ok)
    naplo['nincs_utcahalozat'] = nincs_utcahalozat
    naplo['n_cim_hataron_kivul'] = bemenet.n_cim_hataron_kivul
    korzetek, cimek = _szin_oszlop(korzetek, cimek, debug_iro)
    ido(f'10. kész: {naplo["n_korzet"]} körzet / {naplo["n_kategoria"]} kategória a címekben, '
        f'validáció {"rendben" if validacio_ok else "HIBA"}')
    return BurkolasEredmeny(varos, 'ok', korzetek, cimek, naplo, hibak)


if __name__ == '__main__':

    # Egy település kézi futtatása debug GPKG-vel. A bemenet az A egység összekapcsolt gpkg-je.
    CIMEK_GPKG = v3_io.ROOT / 'data' / 'work_data' / 'osszekapcsolt_pontok_es_cimek_2026.gpkg'
    VAROS = 'Budapest XX. kerület'

    cimek_wgs = gpd.read_file(CIMEK_GPKG, where=f"telepules = '{VAROS}'")
    eredmeny = burkol_telepules(VAROS, cimek_wgs, debug=True)
    print(f'{VAROS}: {eredmeny.status} {eredmeny.uzenet}')
    if eredmeny.status == 'ok':
        for kulcs, ertek in eredmeny.naplo.items():
            print(f'  {kulcs}: {ertek}')
        v3_io.ir_eredmeny(v3_io.ADATOK_DIR / f'{VAROS}_korzetek.gpkg', eredmeny.korzetek, eredmeny.cimek, VAROS)
        print(f'  kimenet: {v3_io.ADATOK_DIR / (VAROS + "_korzetek.gpkg")}')
