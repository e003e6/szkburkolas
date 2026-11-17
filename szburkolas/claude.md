# szburkolas – B. burkoló egység (V3)

A szavazóköri (kategória-) körzetek poligonjainak előállítása településenként a címpontokból,
a lakott területből, a közigazgatási határból és az OSM vonalhálózatból.

**A működés teljes leírása (a dolgozat B-fejezete és egyben a kód specifikációja):**
`/Users/klapkazsombor/Desktop/egyetemip/IDK/B-burkolas-v3-mukodes.md`. A kód a leírást lépésről
lépésre követi; a kommentekben a leírás fejezetszámai szerepelnek. Eltérés csak a bemeneti adatok
miatt van (lásd `v3_io.py` és `v3_param.py` megjegyzéseit: `szavazokorid → szkid`, lista-értékű OSM
címkék, ismeretlen highway-értékek rangja, lyukas lakott foltok, azonos koordinátán több kategória).

## Modulok

| Fájl | Leírás-fejezet | Tartalom |
|---|---|---|
| `burkolas_v3.py` | összes | `burkol_telepules(varos, cimek_wgs, debug)` – a lépések sorban; `__main__`: egy település debug GPKG-vel |
| `burkolas_multiple.py` | 11., 12. | többmagos kötegelt futtató: gyűjtő GPKG (`poligonok`, `cimek`), `_naplo.csv`, `_log.txt`, folytatás |
| `v3_param.py` | 0.4, 0.5, 11.1 | a 4 paraméter, rangtábla, cellatípusok, jelzések, színek |
| `v3_io.py` | Bemenetek, 1.1, 11. | parquet-cache betöltés, UTM, `utca_id`, `DebugIro`, kimenet |
| `v3_vonalhalo.py` | 0.2–0.4, 1., 2. | lakott terület, vonalak vágása, vonalháló, cellák |
| `v3_topologia.py` | R., 1.3, 3.2, 6. | R. rutin, éltábla, attribútum-visszaillesztés, T-feldarabolás |
| `v3_sliver.py` | 3. | sliver-tisztítás |
| `v3_savok.py` | 4., 5. | utcafront-sávok (Voronoi a határpontokból), sarokcellák |
| `v3_felosztas.py` | 7., 8. | besorolás; vegyes cellák: merőleges / egyeztetett / párhuzamos / Voronoi vágás |
| `v3_besorolas.py` | 9., 10., 12. | üres cellák, szigetek, egyesítés, validáció, mutatók |
| `teszt_v3_szintetikus.py` | – | lépésenkénti assert-ek egy mesterséges településen |

## Bemenetek (A-fázis, `data/`)

- `data/work_data/osszekapcsolt_pontok_es_cimek_<év>.gpkg` – címpontok (`szavazokorid`, utca, hazszam, telepules, irsz)
- `data/nyers_data/varos_lakottterulet_hatar.parquet`, `varos_kozigazgatasi_hatar.parquet`
- `data/nyers_data/utcak_all_edges.parquet`, `vasut_folyo.parquet` – `adatstruktua/osm_letoltes.py: osm_letoltes_v3()`

## Futtatás

```
cd szburkolas
../.venv/bin/python teszt_v3_szintetikus.py      # szintetikus teszt
../.venv/bin/python burkolas_v3.py               # egy település (VAROS konstans), adatok/<VAROS>_debug.gpkg
../.venv/bin/python burkolas_multiple.py         # minden település → data/kesz_data/<év>_v3.gpkg
```

A debug GPKG minden rétege `lepes`, `tipus`, `szin` oszlopot kap: QGIS-ben egyetlen adatvezérelt
stílussal (szín ← `szin`) a leírás 11.1 színrendszerében jelenik meg.

## Kódolási elvek

Minden lépés kommentben a leírás sorszámával; olvashatóság a rövidség és a sebesség előtt;
elhagyott funkció törlése, nem kikommentelése; vonalat nem mozgatunk, cellát nem javítunk
(`make_valid`), a kerekítés egyszer, a vonalakon történik (RÁCS).
