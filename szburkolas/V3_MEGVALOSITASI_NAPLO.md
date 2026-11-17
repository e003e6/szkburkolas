# Burkolás V3 – megvalósítási napló

**Mi ez:** a `B-burkolas-v3-mukodes.md` leírás (a továbbiakban: *leírás*) technikai megvalósításának
lezáró naplója. Lépésenként rögzíti, mi készült el, milyen konkrét függvénnyel, milyen
shapely/geopandas-művelettel, milyen értékekkel, és hol kellett a leírás pszeudokódja mögé
olyan döntést tenni, amit a leírás nem mond ki. Az algoritmus tervezőjének szól: ebből látszik,
hogy a terv minden pontja hogyan lett kód, és hol tér el tőle vagy egészíti ki a megvalósítás.

- Kód: `szburkolas/` (V3 modulok) és `adatstruktua/osm_letoltes.py` (V3 letöltő ág)
- Leírás (spec): `/Users/klapkazsombor/Desktop/egyetemip/IDK/B-burkolas-v3-mukodes.md`
- Lezárás dátuma: 2026-10-31; kiegészítve 2026-11-17 (A–B. melléklet: a forráskódból generált teljes
  függvény- és konstans-referencia; az OSM-letöltés állapotának frissítése)
- Környezet: Python 3.13, shapely 2.1.2, geopandas 1.1.1, osmnx 2.0.7, pyogrio, pyarrow

A jelölések: **[LEÍRÁS]** = a leírás szerint, változtatás nélkül; **[KIEGÉSZÍTÉS]** = a leírás
nem rendelkezik róla, a megvalósítás döntése; **[BEMENETI ELTÉRÉS]** = a valós bemeneti adat miatt
szükséges kezelés; **[ELTÉRÉS]** = tudatos eltérés a leírás szövegétől (indoklással).

---

## Tartalom

1. [Áttekintés: modulok, adatszerkezetek, futási sorrend](#1-áttekintés)
2. [Bemenetek és az OSM-letöltő bővítése](#2-bemenetek-és-az-osm-letöltő-bővítése)
3. [Paraméterek és konstansok](#3-paraméterek-és-konstansok)
4. [0. lépés – előkészítés](#4-0-lépés--előkészítés)
5. [1. lépés – vonalháló](#5-1-lépés--vonalháló)
6. [2. lépés – cellák](#6-2-lépés--cellák)
7. [R. rutin – topológia-frissítés](#7-r-rutin--topológia-frissítés)
8. [3. lépés – sliver-tisztítás](#8-3-lépés--sliver-tisztítás)
9. [4. lépés – sávosítás](#9-4-lépés--sávosítás)
10. [5. lépés – sarokcellák](#10-5-lépés--sarokcellák)
11. [6. lépés – üres cellák T-feldarabolása](#11-6-lépés--üres-cellák-t-feldarabolása)
12. [7. lépés – besorolás](#12-7-lépés--besorolás)
13. [8. lépés – vegyes cellák felosztása](#13-8-lépés--vegyes-cellák-felosztása)
14. [9. lépés – üres cellák besorolása](#14-9-lépés--üres-cellák-besorolása)
15. [10. lépés – lezárás, egyesítés, validáció](#15-10-lépés--lezárás-egyesítés-validáció)
16. [11. Kimenetek és debug GPKG](#16-11-kimenetek-és-debug-gpkg)
17. [12. Napló-mutatók](#17-12-napló-mutatók)
18. [Kötegelt futtatás](#18-kötegelt-futtatás)
19. [Validáció és tesztek – eredmények](#19-validáció-és-tesztek--eredmények)
20. [Az összes eltérés és kiegészítés egy táblában](#20-az-összes-eltérés-és-kiegészítés-egy-táblában)
21. [Ismert korlátok, nyitott pontok](#21-ismert-korlátok-nyitott-pontok)
22. [Törölt V2 kód](#22-törölt-v2-kód)
- [A. melléklet – teljes függvény-referencia](#a-melléklet--teljes-függvény-referencia)
- [B. melléklet – minden modulszintű konstans értékkel](#b-melléklet--minden-modulszintű-konstans-értékkel)

---

## 1. Áttekintés

### 1.1 Modulok

| Fájl | Sor | Leírás-fejezet | Tartalom |
|---|---:|---|---|
| `v3_param.py` | 201 | 0.4, 0.5, 11.1 | a 4 paraméter, technikai konstansok, típusok, rangok, rangtábla, jelzések, színek, paletta |
| `v3_io.py` | 355 | Bemenetek, 1.1, 11., 11.1 | parquet-betöltés, UTM, `utca_id`, `DebugIro`, kimenet-írás |
| `v3_vonalhalo.py` | 449 | 0.2–0.4, 1., 2. | lakott terület, vonalvágás, QA, vonalháló, cellák, éltábla |
| `v3_topologia.py` | 567 | R., 1.3, 3.2, 6. | R. rutin, éltábla-építés, attribútum-visszaillesztés, T-csomópontok, cellavágás |
| `v3_sliver.py` | 125 | 3. | sliver felismerés, T-feldarabolás, beolvasztás, háromszög-jelzés |
| `v3_savok.py` | 389 | 4., 5. | sávosítás (Voronoi a határpontokból), sarokcellák |
| `v3_felosztas.py` | 712 | 7., 8. | besorolás, 8.1–8.5 vágások |
| `v3_besorolas.py` | 342 | 9., 10., 12. | üres cellák, szigetek, egyesítés, validáció, mutatók |
| `burkolas_v3.py` | 221 | összes | `burkol_telepules()` – a lépések sorrendben |
| `burkolas_multiple.py` | 212 | 11., 12. | többmagos kötegelt futtató, gyűjtő GPKG, napló CSV |
| `teszt_v3_szintetikus.py` | 159 | – | mesterséges település, lépésenkénti assert-ek |
| `adatstruktua/osm_letoltes.py` | 570 | Bemenetek | V3-ág: `all` hálózat + vasút/folyó cache |

Minden lépés a kódban kommentben a leírás sorszámával szerepel (pl. `# 1.5 Zsákutcák lezárása –
narancs segédvonal`), a függvények docstringje a leírás pszeudokódját idézi.

### 1.2 Adatszerkezetek (a teljes folyamaton át)

**`cellak`** (GeoDataFrame, helyi UTM) – oszlopok (`v3_topologia.CELLA_OSZLOPOK`):

| oszlop | típus | jelentés |
|---|---|---|
| `cella_id` | int | egyedi, monoton nő; új darab mindig új id-t kap, törölt id nem kerül újrakiosztásra |
| `cellatipus` | str | `tömb` / `folt` / `sáv` / `sarok` |
| `tomb_id` | int | a sáv/sarok szülő-tömbjének `cella_id`-je (4. lépéstől) |
| `utca_id` | str | a sáv utcája |
| `utca_vonal_idx` | int | a sáv U-vonalának komponens-indexe az `utca_vonalak[utca_id]` listában |
| `szkid` | int / None | kategória |
| `szabaly` | str | `címes` / `folytonosság` / `rang` / `legközelebbi` |
| `jelzes` | str | `;`-vel fűzött jelzések, ismétlés nélkül |
| `sliver` | bool | csak a 3. lépésben használt |
| `geometry` | Polygon | mindig egyszerű Polygon (MultiPolygon-t az R. rutin lapokra bont) |

**`eltabla`** (GeoDataFrame) – `ELTABLA_OSZLOPOK`: `el_id, tipus, rang, utca_id, cella_bal,
cella_jobb, hossz, geometry`. `cella_bal/jobb = -1` (`KULVILAG`) a lakott határ túloldalán.

**`cimek`** (GeoDataFrame, UTM): `szkid, utca, hazszam, irsz, jelzes, cella_id, geometry`.
A `cella_id`-t minden R. rutin újraszámolja.

**`Vonalhalo`** (dataclass, 1. lépés kimenete): `tabla` (`vonal_id, tipus, rang, utca_id, geometry`),
`utca_vonalak` (`{utca_id: [LineString, …]}`), `n_rang3_torolt`, `n_narancs`, `n_logo_torolt`.

**`utca_vonalak`** – egy utca vonalháló-darabjainak `linemerge`-e komponensenként; ez az **U**
vonal a 8.1, 8.2, 8.3 és 9.1 lépésben. Az utcák a folyamat során nem mozdulnak, ezért egyszer,
az 1.7 után számoljuk (`v3_vonalhalo.utca_vonalak_epites`).

**`tombok`, `eltabla_tombok`** – a 3. lépés utáni (sávosítás előtti) állapot másolata; az 5. lépés
ebből veszi a tömb határát és utcáit.

### 1.3 Futási sorrend (`burkolas_v3.burkol_telepules`)

```
betöltés (v3_io.betolt_telepules)
 → 0.2 lakott vágás → [0.1 egykategóriás rövid út: kész]
 → 0.3 vonalvágás + QA(b) → 0.4 foltok + QA(a)
 → 1. vonalháló (1.1–1.7)
 → 2. cellák + éltábla + címek          (referencia_terulet = a 2. lépés celláinak összterülete)
 → 3. sliver-tisztítás (+R)              (tombok, eltabla_tombok másolat)
 → 4. sávosítás (+R, vázvonal típussal)
 → 5. sarokcellák (+R, vágóvonal típussal)
 → 6. üres cellák T-feldarabolása (+R)
 → 7. besorolás
 → 8. vegyes cellák felosztása (+R)
 → 9. üres cellák besorolása 9.1→9.2→9.3 (+R)
 → 10.1 szigetek → ha volt gyanús: 9. újra (9.1 NÉLKÜL) (+R)
 → R → 10.2 egyesítés → 10.3 validáció → napló → szín oszlop
```

Visszatérés: `BurkolasEredmeny(varos, status, korzetek, cimek, naplo, validacio_hibak, uzenet)`,
`status ∈ {ok, nincs, ures}`; `nincs` = nincs címpont / nincs lakott terület; `ures` = a
poligonizálás nem adott cellát.

---

## 2. Bemenetek és az OSM-letöltő bővítése

### 2.1 A letöltő V3-ága (`adatstruktua/osm_letoltes.py`)

A régi 4 parquet (`varos_kozigazgatasi_hatar`, `varos_lakottterulet_hatar`, `utcak_nodes`,
`utcak_edges` – `drive` hálózat) érintetlen; két új cache-fájl készült:

| Fájl | Tartalom | Oszlopok |
|---|---|---|
| `data/nyers_data/utcak_all_edges.parquet` | `ox.graph_from_place(query, network_type='all')` élei | `u, v, key, osmid, highway, name, service, geometry, telepules` |
| `data/nyers_data/vasut_folyo.parquet` | `ox.features_from_place(query, tags={'railway':'rail','waterway':['river','stream','canal']})` vonalai | `tipus ('vasút'/'folyó'), geometry, telepules` |

Függvények:

- `_all_edges_gdf(G, telepules)` – az élek a fenti 8 oszlopra szűkítve; hiányzó oszlop (pl.
  `service` kis faluban) `None`-nal pótolva, hogy a parquet-séma településenként azonos legyen.
- `_vasut_folyo_sorok(feat, telepules, vago_poligon=None)` – csak LineString/MultiLineString;
  poligon (folyóterület) eldobva; MultiLineString darabolva; `tipus` = `vasút`, ha
  `railway == 'rail'`, egyébként `folyó`. Budapesten `vago_poligon` = kerülethatár.
- `_ujraprobalva(hivas, probak=4, varakozas=30)` **[KIEGÉSZÍTÉS]** – Overpass 502/504/429/timeout
  esetén exponenciális várakozás (30, 60, 120 s); más hibát azonnal továbbad.
- `osm_letoltes_v3(varosok=None, append=None)` – településenként a két lekérdezés; hiba → a
  közös `osm_letoltes_hibak.txt`-be `utcahálózat_all` / `vasut_folyo` rétegnévvel. **Üres
  vasút/folyó eredmény (`InsufficientResponseError`) nem hiba** – sok településen nincs vasút.
- `osm_letoltes_v3_adagokban(varosok, adag=50)` – országos futáshoz 50-es adagokban tölt és ment
  (append-merge), egy megszakadás legfeljebb egy adagot visz el; a budapesti neveket kihagyja,
  ha mind a 23 kerület már a cache-ben van.
- `hianyzo_varosok_v3()` – `varosok.txt` − a cache-ben meglévők − a hibafájlban
  `utcahálózat_all` hibával szereplők (hálózat nélküli aprófalvak ne töltődjenek újra minden
  futásnál). Sorrendtartó.
- **Budapest-ág** `budapest_keruletek_letoltes_v3()`: egész-városi `all` gráf egy letöltéssel,
  kerületenként `ox.truncate.truncate_graph_polygon(G, kerulet, truncate_by_edge=True)`;
  vasút/folyó egész városra, kerülethatárra vágva (`budapest_vasut_folyo`). Csak kerület-címkés
  sorok kerülnek a parquet-be. Külön `budapest_vasut_folyo_potlas()`, mert az egész-városi
  vasút/folyó lekérdezés 502-t adott – így a gráf újratöltése nélkül pótolható (sikeres pótlás:
  3452 vonal).
- `__main__` és `0_pipeline.py`: az `osm_letoltes()` után `osm_letoltes_v3…` hívás.

**Az országos letöltés állapota (2026-11-17):** a háttérfolyamat **leállt, nem fejeződött be**: a log
(`data/nyers_data/osm_letoltes_v3.log`) utolsó bejegyzése 2026-11-03 01:14, a 63 adagból a 21.
adag 34. településénél (Homrogd); az `ORSZAGOS LETOLTES KESZ` sor nincs benne. A cache-ben
1043 település `all` hálózata (1 408 287 él) és 1011 település vasút/folyó rétege (22 688 vonal) van
a 3131-ből. A folytatás a meglévőket kihagyja (`hianyzo_varosok_v3`):
`cd adatstruktua && ../.venv/bin/python osm_letoltes.py`.

### 2.2 Betöltés településenként (`v3_io.betolt_telepules`)

1. **Lakott foltok** – `gpd.read_parquet(..., filters=[('telepules','==',varos)])` (pyarrow
   predikátum-szűrés, nem olvassa be az országot). Üres → `NincsLakottTerulet` kivétel → a
   település `nincs` státuszt kap; **a közigazgatási határral soha nem helyettesítjük** [LEÍRÁS 0.1].
2. **Vetület** – `lakott.estimate_utm_crs()` (a lakott foltokból becsült helyi UTM-zóna); minden
   bemenet ide vetítve. A kimenet EOV (EPSG:23700).
3. **Lakott foltok** MultiPolygon → Polygon darabok (`lakott_nyers`).
4. **Közigazgatási határ** – `shapely.make_valid(...)` az első sorra. Hiány → `RuntimeError`.
5. **Utcák** – `_json_visszafejtes`: a letöltő JSON-stringgé alakított lista-oszlopait
   (`osmid`, `highway`, `name`) visszafejti; ami nem JSON (pl. „Petőfi utca”), marad.
   - **Kétirányú élek dedupe-ja** [BEMENETI ELTÉRÉS]: az `all` gráfban (u,v,key) és (v,u,key)
     azonos geometriával szerepel → a rendezetlen `(min(u,v), max(u,v), key)` kulcs szerint
     egyszer tartjuk meg.
   - `utca_id` (1.1): `utca_id_kepzes(nevek, osmidk)` – az A-fázis `szkid_cim_kapcsolas.utca_fp_series`
     ujjlenyomat-függvénye (rövidítés-feloldás, ékezettelenítés, kisbetű, írásjel-törlés, ugyanaz,
     mint a címeknél). **Név nélküli él: `osm_<osmid>`** [KIEGÉSZÍTÉS].
   - Lista-értékű `name`/`osmid` → első elem; lista-értékű `highway` → **a legmagasabb rang**
     [BEMENETI ELTÉRÉS].
   - Minden él: `tipus='utca'`, `rang = highway_rang(highway)`.
6. **Vasút/folyó** – ha a parquet létezik és van sor: `tipus` a parquet-ből, `rang = 6`,
   `utca_id = None`.
7. MultiLineString → `explode`, csak LineString marad.
8. **Címek** (`cimek_elokeszites`): szűrés a településre; **`szavazokorid → szkid` átnevezés**
   [BEMENETI ELTÉRÉS]; oszlopok `szkid, utca, hazszam, irsz, geometry`; hiányzó oszlop `None`;
   `jelzes = ''`; UTM; `szkid` int64.
9. **Hibás geokódolású címek eldobása** [BEMENETI ELTÉRÉS, a V2 viselkedése visszaállítva]: a
   közigazgatási határt nem metsző (`intersects`) címpont – a koordinátája másik településre esik
   (pl. halásztelki cím Kecelen) – eldobva; száma a naplóban `n_cim_hataron_kivul`, a konzolon
   `[bemenet] N címpont a közigazgatási határon kívül (hibás koordináta) – eldobva`.

Visszatérés: `Bemenet(varos, crs, cimek, lakott_nyers, kozig_hatar, vonalak_nyers, n_cim_hataron_kivul)`.

---

## 3. Paraméterek és konstansok

### 3.1 A négy nevesített paraméter [LEÍRÁS 0.5] (`v3_param.py`)

| Név | Érték | Hol hat |
|---|---:|---|
| `TELEKSZELESSEG` | 6.0 m | 3.1 sliver-küszöb (2r < 6 m), 3.4 háromszög-jelzés |
| `SAROKMERET` | 30.0 m | 5. `d = min(30, lánchossz/2)` |
| `VAZFELBONTAS` | 2.0 m | 4.3 határpont-sűrűség (`segmentize`) és a vázvonal `simplify` tűrése |
| `RACS` | 0.01 m | koordináta-rács; minden `set_precision`, `grid_size`, `dwithin` tűrés |

### 3.2 Technikai konstansok (a leírás mondja ki az értéket, itt csak névvel)

| Név | Érték | Hol |
|---|---:|---|
| `NARANCS_SAJAT_TAVOLSAG` | 0.25 m | 1.5: ennél közelebbi sugár-találat a kiindulópont maga |
| `VAGOVONAL_TULNYULAS` | 1.0 m | minden szerkesztett vágóvonal túlnyúlása (3.2, 5., 6., 8.) |
| `VORONOI_KERESES_LEPES` | 18 | 8.4 bináris keresés lépésszáma |
| `ERINTO_FELHOSSZ` | 0.5 m | 8.1 érintő iránya `U.interpolate(t ± 0,5)` pontokból [KIEGÉSZÍTÉS] |
| `ATFEDES_TURES_M2` | 0.001 m² | 10.3 átfedés |
| `LEFEDES_TURES_ABS_M2` / `_REL` | 5 m² / 0,01 % | 10.3 lefedés: `max(5, 0.0001·A)`; ugyanez a területvédelem 4.3-ban és 8.4-ben |
| `QA_CIMEK_KIVUL_KUSZOB` | 0.10 | 0.4 QA (a) |
| `QA_FOUTAK_KIVUL_KUSZOB` | 0.30 | 0.4 QA (b) |
| `KOLLINEARIS_SZOG_FOK` (`v3_topologia`) | 5° | T-csomópont kollinearitás-tűrés [KIEGÉSZÍTÉS, lásd 8.2] |

### 3.3 Típusok és rangok [LEÍRÁS 0.4]

| Rang | Típus(ok) |
|---:|---|
| 1 | `vázvonal`, `vágóvonal` (a rendszer szerkeszti) |
| 2 | `segédvonal` (narancs) |
| 3 | footway, path, pedestrian, steps, service, cycleway, track |
| 4 | residential, living_street, unclassified, tertiary, tertiary_link |
| 5 | primary, secondary, trunk, motorway + `_link` változataik |
| 6 | `vasút`, `folyó` |
| 7 | `lakott határ` |

**[BEMENETI ELTÉRÉS]** Az `all` hálózatban a táblán kívüli highway-értékek is vannak (`road`,
`busway`, `bridleway`, `corridor`, …): `road` → rang 4 (`HIGHWAY_RANG_ROAD`), minden más
ismeretlen → rang 3 (`RANG_ISMERETLEN_HIGHWAY`) – így lógó végként törlődik, ha nem kapcsolódik.

### 3.4 Értékkészletek

- Cellatípus: `tömb`, `folt`, `sáv`, `sarok`; `KULVILAG = -1`.
- Szabály: `címes`, `folytonosság`, `rang`, `legközelebbi`.
- 7. állapot: `egykategóriás`, `vegyes`, `üres`.
- Vágástípus (12.): `merőleges-T`, `merőleges-közép`, `közös-vonal` (8.2), `párhuzamos`, `Voronoi`.
- Jelzések: `eltérő-folytonosság`, `egycímes-háromszög`, `validáció-hiba`, `azonos-pont`
  [KIEGÉSZÍTÉS], `hibás-sziget`, `besorolatlan`, `lakott terület gyanús`.

### 3.5 Színek [LEÍRÁS 11.1]

`SZIN` szótár a 11.1 táblázat szerint (pl. lakott határ `#000000`, rang 4 `#1F77B4`, rang 5
`#08306B`, vasút `#8C564B`, folyó `#17BECF`, narancs `#FF7F0E`, vázvonal `#9467BD`,
T-vágóvonal `#2CA02C`, közép `#98DF8A`, Voronoi `#D62728`, vegyes cella `#FFBB78`, üres
`#C7C7C7`, sliver `#F7B6D2`, sarok `#F2C6E6` …). `rang_szin(rang, tipus)` a vonalak színe.

**Kategória-paletta** (`kategoria_paletta`) [KIEGÉSZÍTÉS]: a rendezett `szkid` lista i-edik eleme
HSV színt kap: `hue = (i · 0,618034) mod 1` (aranymetszés-léptetés a színkörön), telítettség
0,62, érték felváltva 0,92 / 0,78. Determinisztikus (ugyanaz a település mindig ugyanazt a
palettát kapja), és a szomszédos indexek távoli árnyalatot kapnak.

---

## 4. 0. lépés – előkészítés

### 0.1 Egykategóriás település – rövidített út (`burkolas_v3.py`)

[LEÍRÁS] Ha a címekben egyetlen `szkid` van: a körzet = **a 0.2 után vágott lakott foltok uniója**
(`unary_union(..., grid_size=RACS)`), egy sor, `jelzes=''`. Napló: `n_cella=1, n_korzet=1,
n_kategoria=1, egykategorias=True, n_cim_hataron_kivul`. A 0.1 ellenőrzés a 0.2 *után* fut (a
0.2 adja a geometriát, és ha nincs lakott folt a határon belül, a település `nincs`).

### 0.2 Lakott terület vágása a közigazgatási határra (`lepes_0_2_lakott_vagas`)

1. [LEÍRÁS] Minden folt: `make_valid(folt) ∩ közig_határ`, darabolva Polygonokra, üres eldobva.
   Üres eredmény → `NincsLakottTerulet`.
2. **[BEMENETI ELTÉRÉS] Átfedő OSM lakott poligonok:** az OSM-ben egymásba rajzolt
   `landuse=residential` területek átfedik egymást (Budapest XII.-ben emiatt −8724 m² lefedési
   hiba keletkezett). Megoldás: a vágott foltok **uniója** `grid_size=RACS`-szal, majd az unió
   összefüggő darabjai a foltok. Így egy terület egyszer számít, és az utca nélküli folt nem
   fedheti át a hálózatos foltot.
3. Debug: `0_2_lakott_nyers`, `0_2_kozig_hatar`, `0_2_lakott_vagott`.

### 0.3 Vonalhálózat vágása a lakott területre (`lepes_0_3_vonalak_vagasa`)

1. [LEÍRÁS] Minden vonal: `vonal ∩ unió(lakott foltok)`, attribútumok megmaradnak, darabolva.
2. A levágott rész (`vonal − unió`) külön táblában (debug).
3. **QA (b)** [KIEGÉSZÍTÉS a nevezőben]: `levágott rang 4–5 hossz a foltok convex_hull-ján belül /
   teljes rang 4–5 hossz a convex_hull-on belül`. A convex hull azért kell, hogy a településen
   átmenő, de a lakott területtől messze futó külterületi utak ne torzítsák az arányt.
4. Debug: `0_3_vonalak_nyers` (highway, name, rang), `0_3_vonalak_levagott`, `0_3_vonalak_vagott`.

### 0.4 Foltok szétválasztása (`lepes_0_4_foltok`)

1. [LEÍRÁS] Folt **hálózatos**, ha egy vágott vonallal vett metszetének hossza > 0
   (`STRtree.query(folt, 'intersects')`, majd `intersection(folt).length > 0`), különben
   **utca nélküli**.
2. `lakott_terulet` = a hálózatos foltok uniója; `hatar_vonalak` = a körvonala gyűrűnként.
3. **QA (a)**: a lakott területen (az összes vágott folt uniója) kívül eső címek aránya.
4. `lakott_gyanus = (a) > 10 % VAGY (b) > 30 %` – a kimenetet nem változtatja, csak a napló jelzi.
5. Visszatérés: `Foltok(lakott_terulet, hatar_vonalak, utca_nelkuli_foltok, lakott_osszes, qa_cimek_kivul, lakott_gyanus)`.
6. Debug: `0_4_foltok` (folt_tipus), `0_4_cimek_kivul`.

**[KIEGÉSZÍTÉS] Utcahálózat nélküli település:** ha a cache-ben nincs utcahálózat (letöltési hiba
vagy valóban nincs), a 0.4 szerint minden folt utca nélküli, egy-egy cellaként megy tovább.
Többkategóriás településen ez szinte biztos adathiány, ezért a konzol figyelmeztet, és a napló
`nincs_utcahalozat=True`. Ehhez az 1. és 2. lépés üres vonalhálóra is fel van készítve (korábbi
`ValueError: zero-size array` összeomlás javítása).

---

## 5. 1. lépés – vonalháló

### 1.1 Vonalak összegyűjtése

[LEÍRÁS] lakott határ (rang 7, a `hatar_vonalak` gyűrűi) + vágott utcák (rang 3/4/5) + vasút,
folyó (rang 6). Minden sor kap egy `vonal_id`-t. Üres tábla → üres `Vonalhalo` (lásd fent).

### 1.2 Kerekítés rácsra

1. [LEÍRÁS] `shapely.set_precision(geometriák, RACS)` – az egyetlen hely, ahol vonal kerekítődik.
2. **[KIEGÉSZÍTÉS] Snap a határhoz:** a lakott területre vágott utcavégek a határ szakaszán
   ülnek, de a külön-külön kerekítés után legfeljebb 1 cm-t eltérhetnek tőle. Ezért minden nem
   határ vonalat `shapely.snap(g, határ_unió, RACS)`-szal a határhoz illesztünk (RÁCS-on belül,
   vagyis a kerekítés része, vonal érdemben nem mozdul). Enélkül maradt Budapest XII.-ben
   „zsákutca-végnek nincs találata” eset.
3. `explode`, csak nem nulla hosszú LineString marad.

### 1.3 Első összefésülés és attribútum-visszaillesztés

1. `_osszefesules`: `shapely.unary_union(összes vonal, grid_size=RACS)` → minden
   kereszteződésben közös csúcs. **[KIEGÉSZÍTÉS] A `grid_size` (snap-rounding noding)
   kötelező:** külön kerekítés után mm-es lógó végek maradtak a határ mellett.
2. `attributum_visszaillesztes(darabok, eredeti_tabla)` [LEÍRÁS]:
   - `STRtree(eredeti).query(felezőpontok, predicate='dwithin', distance=RACS)`, vektorosan;
   - 1 találat → örököl; több → **a legmagasabb rangú** találat adja a típust és a rangot
     (a határ nyer az utca felett); az `utca_id` bármelyik utca-találaté (hogy a sáv
     csoportosítható maradjon);
   - 0 találat → `RuntimeError` (a leírás szerint nem fordulhat elő).
3. A `vonal_id` is visszakereshető ugyanígy (debug `1_3_darabok`: rang, utca_id, vonal_id).

### 1.4 Rang 3 lógó végek törlése (`_rang3_logo_vegek_torlese`)

[LEÍRÁS] Ismételve, amíg változik: rang 3 darab törlődik, ha valamelyik végpontjának fokszáma 1.
Fokszám = a darabok végpont-koordinátáinak számlálása (`collections.Counter` a RÁCS utáni
`(x, y)` tuple-ökön – egzakt). Debug: `1_4_rang3_torolt` (kör).

### 1.5 Zsákutcák lezárása – narancs segédvonal (`_narancs_vonalak`)

1. [LEÍRÁS] Zsákutca-vég = fokszám 1 végpont egy rang 4 vagy 5 darabon (a darab mindkét vége
   vizsgálva).
2. Irány: az utolsó szakasz iránya kifelé (`végpont − utolsó belső csúcs`, normálva).
3. **Sugár hossza** [KIEGÉSZÍTÉS a konkrét értékre]: az összes darab befoglaló téglalapjának
   átlója + 1 m (a „félegyenes” véges megvalósítása; mindig átér a hálón).
4. Jelöltek: `STRtree.query(sugár, 'intersects')`, **a saját darab kizárva**; a metszet minden
   pontja; egybeeső szakasz esetén a legközelebbi pontja (`nearest_points`); a ≤ 0,25 m-es
   találat nem számít; **a legközelebbi nyer**.
5. Narancs = `LineString([végpont, találat])`, `tipus='segédvonal'`, `rang=2`.
6. Ha nincs találat (elvileg lehetetlen, a határ megállítja): konzol-figyelmeztetés, nincs narancs.
7. Debug: `1_5_zsakutca_vegek` (rang), `1_5_narancs` (hossz, talalat_tipus), `1_5_talalatok`.

### 1.6 Második összefésülés

[LEÍRÁS] darabok + narancsok → `_osszefesules` (mint 1.3). Debug `1_6_darabok`.

### 1.7 Lógó végek törlése (`_logo_vegek_torlese`)

1. [LEÍRÁS] Ismételve: `polygonize_full_nev_szerint(darabok)`; ha `logo` és `cut` üres → kész.
2. **`polygonize_full_nev_szerint`**: a felső szintű `shapely.polygonize_full` visszatérési
   sorrendje **(polygons, cuts, dangles, invalid)**; egyetlen helyen, név szerint kicsomagolva
   `{'poligonok','cut','logo','ervenytelen'}` dict-be, hogy sorrend-hiba ne fordulhasson elő.
3. Egy darab akkor tartozik egy lógó/cut vonalhoz, ha a felezőpontja RÁCS-on belül rajta ül
   (`STRtree.query(felezők, 'dwithin', RACS)`); ezek törlődnek. Védelem: ha egy körben nincs
   megfeleltethető darab, a ciklus kilép.
4. Debug `1_7_logo_torolt` (ok: lógó/cut, kör), `1_7_vonalhalo` (a kész háló).
5. Ezután: `utca_vonalak_epites` (U-vonalak, lásd 1.2 fejezet).

---

## 6. 2. lépés – cellák

### 2.1 Poligonizálás (`lepes_2_cellak`)

1. [LEÍRÁS] `polygonize_full_nev_szerint(vonalháló)`; **lógó vagy érvénytelen ≠ ∅ → `RuntimeError`**,
   a település leáll (a kötegelt futásban `hiba` státusz).
2. **[BEMENETI ELTÉRÉS] Lyukas lakott foltok:** a lakott foltok lyukaiban is keletkezik lap. Csak
   az a lap lesz cella, amelynek `representative_point()`-ja a `lakott_terulet`-en belül van
   (`contains`). Ez a leírás 0.6/3. garanciájának (lefedés = lakott terület) feltétele.
3. Cellák: `cellatipus='tömb'`, `cella_id = 0..n−1`. Debug `2_1_tombok`.

### 2.2 Utca nélküli foltok

[LEÍRÁS] Minden utca nélküli folt egy cella (`cellatipus='folt'`), `set_precision(RACS)` után,
folytatólagos `cella_id`-vel. Debug `2_2_foltcellak`.

### 2.3 Éltábla és címek

1. Éltábla-vonalak = a vonalháló darabjai + **a foltcellák határgyűrűi rang 7 `lakott határ`-ként**
   [KIEGÉSZÍTÉS: így a foltcellák is szerepelnek az éltáblában, mindkét oldalukon külvilág/folt].
2. `eltabla_epites(vonalak, attributumok, cellak)` – lásd R. rutin 4. pont.
3. `cimek_cellaba` – lásd R. rutin 5. pont.
4. Debug `2_3_eltabla`, `2_3_cimek` (szkid, cella_id).
5. `referencia_terulet = cellak.area.sum()` – a 10.3 lefedés-ellenőrzés alapja.

---

## 7. R. rutin – topológia-frissítés

`v3_topologia.topologia_frissites(cellak, eltabla_elozo, cimek, uj_vonal_tipus='vágóvonal')`.
[LEÍRÁS] Az éltábla **soha nem frissül darabonként**, minden fázis végén nulláról újraszámolódik.
Hívási helyek: 3.2 után, 3.3 minden köre, 4., 5., 6., 8., 9., 10.1 utáni 9., 10.2 előtt.

1. **Összefésülés:** a cellák határai `set_precision(RACS)`, majd
   `unary_union(..., grid_size=RACS)` → LineString-darabok.
2. **[KIEGÉSZÍTÉS] Élek törése a régi csomópontoknál** (`_vonalak_torese_csomopontoknal`):
   a cellahatárok uniója csak ott ad csomópontot, ahol ≥ 3 cella találkozik. Ahol pl. egy utca-él
   és egy narancs-él 2-fokú csúcsban találkozik, a kettő egy láncba olvadna, és a felezőpontos
   visszaillesztés az egyik attribútumát (rangját) elveszítené. Ezért a láncokat elvágjuk minden
   belső csúcsuknál, amely az előző éltábla valamelyik élének végpontja. Vonal nem mozdul, csak
   több, rövidebb él lesz.
3. **Poligonizálás:** `polygonize_full_nev_szerint(vonalak)['poligonok']`.
4. **Attribútumok:** minden új lap annak a régi cellának az attribútumait kapja, amelyben a
   `representative_point()`-ja van (`STRtree.query(..., 'within')`).
   - Lap, amelynek nincs régi cellája → **lyuk a lakott területben → eldobva** [KIEGÉSZÍTÉS].
   - Ha egy régi cella több lapra esett (pl. a beolvasztás MultiPolygon-t adott, vagy pontban
     érintkező darabok), az első lap tartja az id-t, a többi **új `cella_id`-t** kap.
   - A cellák `cella_id` szerint rendezve.
5. **Új éltábla:** `attributum_visszaillesztes(vonalak, eltabla_elozo, hianyzo_tipus=uj_vonal_tipus,
   hianyzo_rang=1)` – a sehová nem illeszkedő darab az adott fázisban szerkesztett vonal: a 4.
   lépésben `vázvonal`, máshol `vágóvonal`, rang 1. Utána `eltabla_epites`:
   - `cella_bal` = a cella, amelyben `felezőpont + RACS · normál` van; `cella_jobb` =
     `felezőpont − RACS · normál`; `STRtree.query(..., 'within')`; nincs → −1.
   - **[KIEGÉSZÍTÉS] Második mintavétel:** zárt gyűrűnél (pl. foltcella határa) a felezőpont
     csúcsra eshet, és akkor mindkét oldalpont külvilágot ad. Ahol mindkét oldal −1, újra
     mintavételezünk **a leghosszabb szakasz felezőpontjánál** (mindig szakasz belsejében van).
   - `hossz = vonal.length`.
6. **Címek:** `cimek_cellaba` – `STRtree(cellak).query(címpontok, 'within')`; a cellahatáron ülő és
   a lakott területen kívüli címnek nincs `cella_id`-je (`None`) [LEÍRÁS 2.3].

Segédfüggvények (ugyanitt): `cella_elei`, `szomszed_id`, `szomszedok_osszesitve(eltabla, cella_id,
csak_cellak)` → `{szomszéd: (min rang, összhossz)}` (a külvilág nem szomszéd),
`nyertes_szomszed` → **(legkisebb rang, majd leghosszabb közös él, majd kisebb id)** [LEÍRÁS 0.4
+ determinisztikus döntetlen-feloldás], `cella_kategoriak` → `{cella_id: Counter(szkid)}` csak a
*számító* címekből (`szamito_cimek`: kihagyva a `hibás-sziget` és `azonos-pont` jelzésűek).

---

## 8. 3. lépés – sliver-tisztítás

`v3_sliver.lepes_3_sliver_tisztitas` = 3.1 → 3.2 → **R** → 3.3 → 3.4 → debug `3_R_cellak`.

### 3.1 Felismerés

[LEÍRÁS] `sliver = 2·r < TELEKSZÉLESSÉG ÉS a cellában nincs (számító) cím`, ahol
`r = shapely.maximum_inscribed_circle(cella).length` (az eredmény a középpontból a legközelebbi
határpontba húzott szakasz; tolerancia: GEOS alapértelmezett). Debug `3_1_sliverek` (beirt_atmero).

### 3.2 T-feldarabolás (`v3_topologia.t_feldarabolas`)

[LEÍRÁS] Minden sliver elvágva minden T-csomópontjánál, így minden darab egy tömb hosszú.

- **`vegpont_index(eltabla)`**: `{csúcs: [(kifelé mutató irány, cella_bal, cella_jobb)]}` minden
  él mindkét végére.
- **`t_csomopontok(cella_id, cella_geom, index)`**: a cella gyűrűinek azon csúcsai, ahol ≥ 3 él
  találkozik, és van olyan **idegen él** (egyik oldala sem a cella).
  **[KIEGÉSZÍTÉS] Kollinearitás-kivétel:** az idegen él nem számít, ha a cella saját élének
  egyenes folytatása (az iránya a saját él irányának ellentettje 5°-on belül: `dot < 0` és
  `|cross| < sin 5°`). Ok: a cella sarkában egyenesen továbbfutó utca miatt a merőleges vágás
  csak egy pár méteres, információ nélküli sarokháromszöget vágna le.
- **`t_vagovonal`**: a határ érintője a két szomszédos határszakasz átlagolt egységiránya
  (visszaforduló határnál a bejövő irány); erre merőleges a vágás; a belső irányt a csúcstól
  5·RÁCS-ra vett próbapont dönti el (`contains`); sugár hossza = a cella bbox-átlója + 1 m; a
  sugár ∩ cella metszetének **a csúcsot tartalmazó legrövidebb komponense** = az első kilépésig
  tartó szakasz; mindkét végén 1 m-rel meghosszabbítva.
- **`cella_felosztas(cella, vagovonalak)`**: a vágóvonalak **egyszerre** hatnak
  (`shapely.ops.split(cella, unary_union(vágóvonalak))`); ha 1 darab jön ki vagy hiba, a cella
  változatlan. Az első darab tartja az id-t, a többi új id-t kap.
- Debug `3_2_T_csomopontok`, `3_2_T_vagasok`. Utána R. rutin, hogy a beolvasztás a darabokat lássa.

### 3.3 Beolvasztás

[LEÍRÁS] Ismételve, amíg van sliver: minden sliver-darab a nem-sliver szomszédai közül
`nyertes_szomszed` szerint (legkisebb rangú közös él, egyenlőnél a leghosszabb) beolvad
(`unary_union`); körönként R. rutin.
- Ha egy sliver-nek csak sliver szomszédja van, abban a körben kimarad.
- **Ha egy körben semmi nem olvadt be:** a maradék `sliver=False`-t kap, és cím nélküli cellaként
  megy tovább (a 6. lépés kezeli) [LEÍRÁS].
- Debug `3_3_beolvasztas` (nyíl: reprezentatív ponttól reprezentatív pontig; nyertes_rang, kozos_hossz).

### 3.4 Ismert korlát – egycímes háromszög

[LEÍRÁS] Az egycímes (pontosan 1 számító cím), `2r < 6 m` cella `egycímes-háromszög` jelzést kap.

---

## 9. 4. lépés – sávosítás

`v3_savok.lepes_4_savositas(cellak, eltabla, cimek, utca_vonalak, debug)`. Csak `tömb` típusú
cellákra:

| A tömb határán lévő különböző `utca_id` | Teendő |
|---|---|
| 0 | marad `tömb` |
| 1 | **a tömb maga egy sáv** (`cellatipus='sáv'`, `tomb_id`=saját id, `utca_id`) [KIEGÉSZÍTÉS: nem fut Voronoi] |
| ≥ 2 | `tomb_savositas` (lent); ha `None`-t ad → marad tömb |

Minden sáv `utca_vonal_idx`-et kap (`_utca_vonal_index`): az utca azon `linemerge`-komponense,
amellyel a sáv határának **a leghosszabb a közös része**; ha egyik sem érinti, a legközelebbi
[KIEGÉSZÍTÉS: a leírás „az utca U vonala”-t írja, de egy utcanévnek több nem összefüggő
komponense lehet].

### 4.3 Egy tömb sávosítása (`tomb_savositas`)

1. **Határpontok:** a tömb külső és belső gyűrűi `shapely.segmentize(gyűrű, VAZFELBONTAS)`
   csúcsai, záró pont nélkül, ismétlődések nélkül (sorrendtartó). Minden pont csoportja annak az
   éltábla-élnek az `utca_id`-je, amelyen ül (`STRtree.query(pontok, 'dwithin', RACS)`, első
   találat – csúcsban két él is találhat, bármelyik jó); **nem-utca él (határ, vasút, folyó,
   segédvonal) pontja saját csoportot kap: `el_<el_id>`**. Ha < 2 utca-csoport van → `None`.
2. **Voronoi:** `shapely.voronoi_polygons(MultiPoint(pontok), extend_to=tömb, ordered=True)` – az
   `ordered=True` (shapely 2.1) adja a sejt ↔ pont megfeleltetést; minden sejt a tömbre vágva és
   `set_precision(RACS)`.
3. **Egyesítés csoportonként:** `unary_union(sejtek, grid_size=RACS)` → nyers sávok.
4. **Vázvonalak kiegyenesítése:**
   - `vázvonal_nyers = unary_union(nyers sávok határai) − tömbhatár` (mindkettő RÁCS-on);
   - **[KIEGÉSZÍTÉS] `linemerge` az egyszerűsítés előtt:** az unió szakaszonként adja a
     vonalakat, ezen a `simplify`-nak nincs hatása; a 2-fokú csúcsoknál összefűzve a teljes
     vázvonal-ágon fut, a ≥ 3-fokú csomópontok végpontok maradnak;
   - `shapely.simplify(MultiLineString, VAZFELBONTAS, preserve_topology=True)`.
   - Sávok = `polygonize(tömbhatár ∪ vázvonalak)` lapjai, amelyek reprezentatív pontja a tömbben
     van; a lap csoportja a nyers sáv, amelyben a reprezentatív pontja van (ha egyikben sem:
     `query_nearest`).
   - **[KIEGÉSZÍTÉS] Területvédelem:** ha az egyszerűsített vázvonalakkal kapott lapok
     összterülete a tömbétől `max(5 m², 0,01 %)`-nál jobban eltér (az egyszerűsített vonal
     kilépett a tömbből), a **nyers** vázvonalakkal poligonizálunk.
5. **Nem-utca sávok beolvasztása** [LEÍRÁS]: iteratívan minden `el_*` sáv abba a szomszédos
   utca-sávba olvad, amellyel a közös határa a leghosszabb. **[KIEGÉSZÍTÉS]** Ha egy nem-utca
   sáv csak nem-utca sávval érintkezik: a legnagyobb utca-sávba olvad; ha nincs utca-sáv →
   `None` (marad tömb).
6. **[KIEGÉSZÍTÉS]** A beolvasztás után az azonos utcájú sávok egyesülnek (egy utca = egy sáv a
   tömbön), több darab csak akkor marad, ha az utca a tömb két különböző helyén határolja.
7. Debug: `4_3_hatarpontok`, `4_3_voronoi_nyers`, `4_3_savok_nyers` (csoport; szín: utca-paletta),
   `4_3_vazvonalak_nyers`, `4_3_vazvonalak`, `4_3_nemutca_beolvasztas` (csoport, nyertes_utca).

Végül R. rutin **`uj_vonal_tipus='vázvonal'`**-lal: a vázvonalak itt kapják a `vázvonal` típust és
az 1-es rangot. Debug `4_R_savok`.

---

## 10. 5. lépés – sarokcellák

`v3_savok.lepes_5_sarokcellak(cellak, eltabla, cimek, tombok, eltabla_tombok, debug)` – a 3. lépés
utáni tömbökön és éltáblájukon.

1. **Sarok-csomópont** (`tomb_sarkai`) [LEÍRÁS]: a tömb utca-éleinek olyan végpontja, ahol
   **két különböző `utca_id`** találkozik (ugyanannak az utcának a kanyarja nem sarok).
   [KIEGÉSZÍTÉS] ≥ 3 utca esetén a rendezett első kettő; a sarkok koordináta szerint rendezve
   (determinizmus).
2. **Láncok:** U1 és U2 tömb menti éleinek `linemerge`-e, az a komponens, amelynek S végpontja
   (`_lanc_s_vegponttal`, RÁCS tűréssel).
3. `d_i = min(SAROKMÉRET, lánc_i hossza / 2)` [LEÍRÁS]; `d ≤ RÁCS` → kihagyás.
4. `P_i = lánc.interpolate(d)` S felől; `m_i` = a láncra P_i-ben merőleges, mindkét irányban
   „tömbátló + 1 m” hosszú szakasz (`_meroleges_egyenes`).
5. `X = m1 ∩ m2`; ha nem pont, vagy **nincs a tömbben** → nincs sarokcella (nagyon tompa szög,
   párhuzamos utcák) [LEÍRÁS]. `négyszög = Polygon([S, P1, X, P2])`, érvénytelen/0 területű → kihagyás.
6. **[ELTÉRÉS a vágóvonal hosszában]** A sávokat **nem a teljes m1/m2 egyenessel**, hanem csak a
   **P1→X és P2→X szakasszal** vágjuk (a négyszög két belső oldala), a P végén 1 m túlnyúlással
   (`_hosszabbit_eleje`), X-ben pontosan találkozva. Ok: a teljes húrral vágva a merőleges a sáv
   távolabbi részét is feldarabolta, és a reprezentatív-pont teszt hibás sarkot adott. Az
   eredmény pontosan a leírás négyszöge.
7. Az U1 és U2 sávjai (ugyanabban a tömbben) `cella_felosztas`-sal vágva; azok a darabok, amelyek
   reprezentatív pontja a négyszögben van → **sarokcella** (`cellatipus='sarok'`, `utca_id=None`,
   egyesítve `unary_union`-nal, darabonként új id); a többi darab a sáv attribútumaival, új id-vel marad.
8. Debug: `5_sarok_csomopontok` (utca_1, utca_2), `5_merolegesek` (d), `5_sarok_negyszogek`.
9. R. rutin (`vágóvonal`). Debug `5_R_cellak`.

---

## 11. 6. lépés – üres cellák T-feldarabolása

`burkolas_v3.py`: minden cím nélküli cella (bármely cellatípus) `T.t_feldarabolas`-sal elvágva a
T-csomópontjainál (ugyanaz a függvény és kollinearitás-szabály, mint 3.2-ben), majd R. rutin.
Debug `6_T_csomopontok`, `6_T_vagasok`, `6_R_cellak`.

---

## 12. 7. lépés – besorolás

`v3_felosztas.lepes_7_besorolas` [LEÍRÁS]: 0 számító cím → `üres`; egy kategória → `egykategóriás`
(`szkid`, `szabaly='címes'`); több → `vegyes` (a 8. lépés listája).

**[BEMENETI ELTÉRÉS / SPEC-HIÁNY] Azonos koordinátán több kategória** (`azonos_pont_jelzes`): az
A-fázis több-a-többhez párosítása miatt egy koordinátán több, eltérő `szkid`-ű cím ülhet. Ezt
semmilyen vágás (a Voronoi t=0 sem) nem tudja szétválasztani. Szabály: a vegyes cellák címeit
RÁCS-kulcs (`round(x/RACS), round(y/RACS)`) szerint csoportosítjuk; ha egy ponton ≥ 2 kategória
van, **a többségi marad** (döntetlennél a kisebb `szkid`), a kisebbségi címek `azonos-pont`
jelzést kapnak, a besorolásban és a validációban nem számítanak, a napló `n_azonos_pont`-ként
számolja. Ezután a kategóriák újraszámolva. **Ezt a leírásba javasolt felvenni.**

Debug `7_besorolas` (állapot szerinti színnel). A 12. mutatókhoz a 7. utáni cellák/címek másolata
(`cellak_7`, `cimek_7`) megmarad.

---

## 13. 8. lépés – vegyes cellák felosztása

`v3_felosztas.lepes_8_vegyes_felosztas(cellak, eltabla, cimek, vegyes_idk, utca_vonalak, debug)`.

**Döntési sorrend vegyes cellánként** (a `vegyes_idk` sorrendjében, a már kész cellák kihagyva):

```
SÁV (van U):
   8.2  ha a szemközti sáv is vegyes és még nem kész → egyeztetett közös vonal
   8.1  merőleges vágás az utca menti sor szerint
   8.3  párhuzamos vágás az utcától mért távolság szerint
   8.4  elő-/utófutam levágása + Voronoi a keveredő szakaszon
SAROK, TÖMB, FOLT (nincs U):
   8.4  Voronoi a teljes cellára
```

Közös segédfüggvények: `futamok` (rendezett kategória-sor → `[(kat, első, utolsó)]`), `tiszta`
(minden kategória egyetlen összefüggő futam), `erinto_irany(U, t)` (az `U.interpolate(t ± 0,5 m)`
pontok iránya – görbe utcánál is egyértelmű), `meroleges_egyenes`, `vago_a_cellaban` (az egyenes
cellán belüli, P-hez legközelebbi darabja, 1 m túlnyúlással), `darabok_kategoriai`
(`STRtree(címek).query(darab, 'contains')`), `ellenorzes` (minden darab ≤ 1 kategória),
`t_csomopontok_utcan(U, index)` (a ≥ 3 élű csomópontok, amelyek RÁCS-on belül U-n ülnek → t-értékek).

### 8.1 Merőleges vágás (`vagas_8_1`)

1. [LEÍRÁS] `t = U.project(cím)`; stabil rendezés t szerint; futamok. Nem tiszta → `None`.
2. Minden szomszédos futam-pár köze `(t_utolsó_előtte, t_első_utána)`.
3. **Vágási hely** (`_koz_vagas_t`): a közbe eső T-jelöltek közül a köz felezőpontjához
   legközelebbi (`merőleges-T`); ha nincs, a köz közepe (`merőleges-közép`).
4. Vágóvonal: P-ben (`U.interpolate(t)`) az érintőre merőleges egyenes ∩ sáv, a P-hez
   legközelebbi komponens, 1 m túlnyúlással.
5. **[KIEGÉSZÍTÉS] Próbasorrend és köz-ellenőrzés** (`_egy_koz_vagovonala`): az adott köz vágása
   akkor jó, ha egyetlen darabban sincs egyszerre előtte és utána lévő cím. Ha az első (T/közép)
   hely nem jó (görbe utca), a köz **¼ és ¾ pontja** próbálkozik; ha egyik sem → 8.1 bukik.
6. Az összes futam-határ vágóvonala **egyszerre** hat (`cella_felosztas`), végül `ellenorzes`.
7. A cella vágástípusa: `merőleges-T`, ha bármelyik vágás T-nél volt, különben `merőleges-közép`.

### 8.2 Egyeztetés a szemközti sávval (`vagas_8_2`)

1. **Szemközti sáv** (`_szemkozti_sav`): `sáv` típusú cella **ugyanabban a tömbben**, más
   utcával, amellyel közös `vázvonal` típusú éle van; több jelölt közül a leghosszabb közös
   vázvonalú. A közös vázvonal = a közös élek `linemerge`-e (ha nem egy LineString, a leghosszabb
   komponense).
2. Feltétel [LEÍRÁS]: mindkét sáv tiszta és **pontosan két futamú, ugyanazzal a két
   kategóriával** (`_futam_koz`).
3. A két köz két szélső t-jénél húzott merőleges hol metszi a vázvonalat → `vázvonal.project` →
   `I1`, `I2` intervallum; nincs átfedés → `None`.
4. V helye a vázvonalon: azon T-jelöltek (bármelyik utcáé), amelyek merőlegese az átfedésbe
   metszi a vázvonalat, közülük a középhez legközelebbi; ha nincs, az átfedés közepe.
5. Vágás sávonként: `V → U.interpolate(U.project(V))` (a V-ből az utcára bocsátott merőleges
   talppontja), 1 m túlnyúlással. Párhuzamos utcáknál egy egyenes, egyébként V-ben törik.
6. Bármelyik sáv ellenőrzése bukik → `None`, és **mindkét sáv a saját 8.1-ével** megy (lépcső).
7. Siker: mindkét cella `kész`, vágástípus `közös-vonal` (+2 a statisztikában); debug
   `8_2_kozos_vonal` (v, tores_szog = a két vágásirány szöge).

### 8.3 Párhuzamos vágás (`vagas_8_3`)

1. [LEÍRÁS] `d = U.distance(cím)`; rendezés d szerint; nem tiszta → `None`.
2. Futam-páronként `d_vágás = (d_utolsó + d_első) / 2`.
3. `U.offset_curve(±d_vágás)`: **az első előjel (+, majd −), amelyiknél a görbe metszi a sávot**
   [KIEGÉSZÍTÉS – az oldal kiválasztása; a terv a reprezentatív ponthoz közelebbi oldalt írta,
   a kód a sávot metsző oldalt választja].
4. `görbe ∩ sáv` darabjai, mindegyik 1 m túlnyúlással; egyszerre vágva; ellenőrzés.
5. Debug `8_3_parhuzamos` (d).

### 8.4 Voronoi-vágás (`voronoi_vagas`, `vagas_8_4_sav`)

**Sávnál** (`vagas_8_4_sav`):
1. `_elo_es_utofutam` [KIEGÉSZÍTÉS a pontos feltételre]: előfutam = a sor első futama, ha > 2
   futam van és **a kategóriája máshol nem fordul elő**; ugyanígy az utófutam (ha mindkettő
   ugyanaz a kategória, csak az előfutam).
2. Ezeket a 8.1 szabályával (`_egy_koz_vagovonala`) levágjuk; a középső, még vegyes darab(ok)ra
   `voronoi_vagas`.

**Sarok, tömb, folt:** `voronoi_vagas` a teljes cellára.

**`voronoi_vagas(cella, címek)`** [LEÍRÁS, a pontos megvalósítással]:
1. Magok: a címek, **RÁCS-kulcs szerint egyedítve** (azonos koordinátájú magokból egy) [KIEGÉSZÍTÉS:
   a GEOS Voronoi duplikált magra nem ad külön sejtet]. < 2 kategória → az egész cella egy kategória.
2. `voronoi_polygons(MultiPoint, extend_to=cella, ordered=True)`, sejtek a cellára vágva,
   `set_precision(RACS)`; kategóriánként `unary_union` → nyers al-cellák.
3. Nyers vágóháló = `unió(al-cellák határai) − cellahatár` (RÁCS-on), `linemerge`.
4. **Kiegyenesítés:** `vagas(t)` = a vágóháló `simplify(t, preserve_topology=True)`-val, a
   **cellahatárt érintő végek 1 m-rel meghosszabbítva** (`_hatart_erinto_vegek_hosszabbitasa`),
   `polygonize(cellahatár ∪ vágóvonalak)`, a cellán belüli lapok; ha a lapok összterülete
   `max(5 m², 0,01 %)`-nál jobban eltér → érvénytelen. **Bináris keresés 18 lépésben** a
   `[0, cella bbox-átló + 1 m]` intervallumon a legnagyobb t-re, amelynél a vágás érvényes és
   minden lap egykategóriás.
5. **[KIEGÉSZÍTÉS] Visszaesés:** ha már t = 0 sem érvényes, a nyers al-cellák darabjai a felosztás;
   ha azok sem tiszták → `None`.
6. Cím nélküli lap → a legközelebbi mag kategóriája (`STRtree.query_nearest`).
7. Debug: `8_4_voronoi_sejtek` (szkid), `8_4_voronoi_nyers_vagohalo`, `8_4_voronoi_vagas` (t_tures).

### 8.5 A felosztás után

1. [LEÍRÁS] Minden darab a benne lévő címek kategóriáját kapja; cím nélküli → üres (`szkid=None`);
   a cellán belül az azonos kategóriájú darabok egyesülnek (`unary_union`, RÁCS); az első darab
   tartja az id-t, a többi új id; `szabaly='címes'`, ha van kategória.
2. **[KIEGÉSZÍTÉS] Szétválaszthatatlan vegyes maradék** (a Voronoi sem tudta): a darab a
   **többségi kategóriát** kapja, a kisebbségi címek `azonos-pont` jelzést (ugyanaz a mechanizmus,
   mint a 7. lépésben).
3. R. rutin (`vágóvonal`). Debug `8_1_vetitesek` (t, szkid), `8_1_sorok` (t, futam, tiszta),
   `8_1_vagas_T`, `8_1_vagas_kozep`, `8_R_cellak` (vagas_tipus).
4. Visszatérés a vágástípus-statisztikával (`Counter`) a 12. mutatókhoz.

---

## 14. 9. lépés – üres cellák besorolása

`v3_besorolas.lepes_9_ures_besorolas(..., lepes_elotag='9', folytonossag=True)` = 9.1 → 9.2 → 9.3 → R.

### 9.1 Utca menti folytonosság (`lepes_9_1_folytonossag`)

1. [LEÍRÁS] Minden üres sávra: jelöltek = **ugyanazon utca ugyanazon U-komponensének** (`utca_id`
   és `utca_vonal_idx`) `címes` szabályú sávjai.
2. **Ugyanazon oldal** [KIEGÉSZÍTÉS a tesztre]: a két sáv reprezentatív pontját összekötő szakasz
   nem keresztezi U-t (`LineString([rp1, rp2]).crosses(U)` hamis).
3. `t = U.project(reprezentatív pont)`; a két irányban a legközelebbi jelölt. Egyező kategória
   vagy csak egy oldal → az; eltérő → a közelebbi, és `eltérő-folytonosság` jelzés.
4. **Egy menetben, csak címes sávokból örököl** (az ebben a lépésben besorolt sáv nem forrás) –
   determinisztikus.
5. Debug `9_1_folytonossag` (nyíl, irány: kisebb t / nagyobb t, jelzés).

### 9.2 Rang-szabály (`lepes_9_2_rang`)

[LEÍRÁS] Ismételve, amíg változik: a még üres cellák `cella_id` sorrendben; minden üres a besorolt
szomszédai közül `nyertes_szomszed` szerint örököl; **ugyanabban a körben a frissen besoroltat a
többi már látja**. A rang 7 élek túloldala −1, így lakott határon soha nem öröklődik. Debug
`9_2_rang` (nyertes_rang).

### 9.3 Legközelebbi cella (`lepes_9_3_legkozelebbi`)

[LEÍRÁS] A még üres cellák (pl. elszigetelt utca nélküli folt) a legközelebbi besorolt cella
kategóriáját kapják (`STRtree.query_nearest`). Ha nincs egyetlen besorolt cella sem → üres marad,
`besorolatlan` jelzéssel. Debug `9_3_legkozelebbi` (tav).

Végül R. rutin, debug `9_R_cellak` (szabaly).

---

## 15. 10. lépés – lezárás, egyesítés, validáció

### 10.1 Hibás, egycímes szigetek (`lepes_10_1_szigetek`)

1. [LEÍRÁS] Kategóriánként a cellák uniója (RÁCS) összefüggő darabokra; ha > 1 darab: a ≤ 1
   számító címet tartalmazó darab **gyanús**. **Biztonság:** ha minden darab gyanús, a
   legnagyobb területű marad.
2. A gyanús darabok cellái (amelyek reprezentatív pontja a darabban van) → `szkid=None`; a bennük
   lévő cím `hibás-sziget` jelzést kap (a besorolásban és a validációban nem számít; napló
   `n_hibas_sziget`).
3. Ha volt gyanús: **9. lépés újra, 9.1 NÉLKÜL** (`folytonossag=False`, rétegek `10_1_9_*`).
   **[ELTÉRÉS, indoklással]:** az utca menti folytonosság ugyanazt a (nem szomszédos sávtól
   örökölt) szigetet hozná létre újra; a rang-szabály és a legközelebbi cella csak szomszédtól
   örököl, így a sziget a környezetébe olvad. Ezt a leírásba javasolt felvenni.
4. Debug `10_1_szigetek` (cimek_szama).

### 10.2 Egyesítés (`lepes_10_2_egyesites`)

R. rutin, majd kategóriánként a cellák `unary_union`-ja (RÁCS) = a körzet (MultiPolygon
megengedett); a körzet `jelzes`-e a cellái jelzéseinek rendezett uniója. **[KIEGÉSZÍTÉS] A
besorolatlan cellák (`szkid=None`) külön sorba** kerülnek `besorolatlan` jelzéssel, hogy a lefedés
teljes maradjon. Debug `10_2_korzetek` (szkid, jelzes).

### 10.3 Validáció (`lepes_10_3_validacio`)

Mind teljesülnie kell; bukásnál **minden körzet `validáció-hiba` jelzést kap**, a konzol/log
`VALIDÁCIÓS HIBA`-t ír a hibaüzenetekkel, **a kimenet mégis kiíródik**:

| # | Feltétel | Megvalósítás |
|---|---|---|
| 1 | nincs átfedés | `STRtree.query(körzetek, 'intersects')`, páronként `intersection.area > 0,001 m²` → hiba |
| 2 | lefedés = a 2. lépés celláinak uniója | `|Σ körzetterület − referencia_terulet| ≤ max(5 m², 0,01 %)` |
| 3 | nincs lógó vonal | a körzethatárok uniója (RÁCS) `polygonize_full`: lógó és érvénytelen üres |
| 4 | minden (számító) cím a saját körzetében | `STRtree.query(címek, 'intersects')` – **intersects**, mert a két körzet közös határán ülő cím rendben van, ha az egyik körzet a sajátja |
| 5 | **körzetek száma = kategóriák száma** [a felhasználó kérésére, a V2 ellenőrzése] | a bemeneti címek `szkid`-halmaza = a körzetek `szkid`-halmaza (hiányzó kategória → hiba; körzet a címekben nem szereplő kategóriával → hiba); egy kategóriához pontosan egy körzet-sor (különben hiba) |

A `besorolatlan` (kategória nélküli) poligon a leírás 9.3 szerint megengedett kimenet, **nem**
validációs hiba, csak jelzés (napló `jelzes_besorolatlan`). Debug `10_3_validacio` (ok: átfedés /
lógó / átkerült cím).

Konzol-kimenet a végén: `10. kész: 8 körzet / 8 kategória a címekben, validáció rendben`.

---

## 16. 11. Kimenetek és debug GPKG

### 16.1 Kimeneti séma (`v3_io.py`)

| Réteg | Oszlopok | Vetület |
|---|---|---|
| `poligonok` | `szkid, telepules, jelzes, szin, geometry` | EOV (EPSG:23700) |
| `cimek` | `szkid, utca, hazszam, telepules, irsz, szin, geometry` | EOV |

- **`szin` oszlop** [a V2 viselkedése visszaállítva, a felhasználó kérésére]: a körzet és a címei
  ugyanazt a hex színt kapják a kategória-palettából (`_szin_oszlop`), így QGIS-ben adatvezérelt
  kitöltéssel (szín ← `szin`) a körzetek jól elkülönülnek, és látszik, melyik cím melyik körzetbe
  tartozik. Besorolatlan → `#C7C7C7`.
- Egy település kézi futtatása: `ir_eredmeny` → `szburkolas/adatok/<település>_korzetek.gpkg`.
- A V2 `color` oszlopa helyett `szin`, és bekerült a `jelzes`.

### 16.2 Debug GPKG (`DebugIro`) [LEÍRÁS 11.1]

- Fájl: `szburkolas/adatok/<település>_debug.gpkg`; **induláskor a régi fájl törlődik**, hogy régi
  futás rétegei ne maradjanak benne. Kötegelt futásban inaktív (`utvonal=None`, minden hívás
  azonnal visszatér).
- Minden réteg közös oszlopai: **`lepes`, `tipus`, `szin`** + a réteg saját attribútumai; üres
  réteg nem íródik (konzol-sor jelzi).
- `cellak(...)`: a 11.1 színszabály – sliver rózsaszín, vegyes narancs, egykategóriás a kategória
  színe, besorolatlan sarok halványmagenta, üres szürke; oszlopok `cella_id, cellatipus, tomb_id,
  utca_id, szkid` (+ sajátok).
- `eltabla(...)`: `el_id, rang, tipus_el, utca_id, cella_bal, cella_jobb, hossz`, szín rang/típus szerint.
- Nyilak (3.3, 9.x): `LineString` a két geometria reprezentatív pontja között.

**A teljes rétegsor:** `0_2_lakott_nyers, 0_2_kozig_hatar, 0_2_lakott_vagott, 0_3_vonalak_nyers,
0_3_vonalak_levagott, 0_3_vonalak_vagott, 0_4_foltok, 0_4_cimek_kivul, 1_1_hatar, 1_3_darabok,
1_4_rang3_torolt, 1_5_zsakutca_vegek, 1_5_narancs, 1_5_talalatok, 1_6_darabok, 1_7_logo_torolt,
1_7_vonalhalo, 2_1_tombok, 2_2_foltcellak, 2_3_eltabla, 2_3_cimek, 3_1_sliverek,
3_2_T_csomopontok, 3_2_T_vagasok, 3_3_beolvasztas, 3_R_cellak, 4_3_hatarpontok,
4_3_voronoi_nyers, 4_3_savok_nyers, 4_3_vazvonalak_nyers, 4_3_vazvonalak, 4_3_nemutca_beolvasztas,
4_R_savok, 5_sarok_csomopontok, 5_merolegesek, 5_sarok_negyszogek, 5_R_cellak, 6_T_csomopontok,
6_T_vagasok, 6_R_cellak, 7_besorolas, 8_1_vetitesek, 8_1_sorok, 8_1_vagas_T, 8_1_vagas_kozep,
8_2_kozos_vonal, 8_3_parhuzamos, 8_4_voronoi_sejtek, 8_4_voronoi_nyers_vagohalo,
8_4_voronoi_vagas, 8_R_cellak, 9_1_folytonossag, 9_2_rang, 9_3_legkozelebbi, 9_R_cellak,
10_1_szigetek, 10_1_9_2_rang, 10_1_9_3_legkozelebbi, 10_1_9_R_cellak, 10_2_korzetek,
10_3_validacio`.

---

## 17. 12. Napló-mutatók

`v3_besorolas.szerkezeti_mutatok` + `burkolas_v3.py`; településenként egy sor a
`<gyűjtő>_naplo.csv`-ben:

| Mező | Jelentés |
|---|---|
| `n_cella` | cellák száma a 7. lépésben |
| `n_vegyes`, `vegyes_arany` | vegyes cellák száma / aránya a 7. lépésben |
| `ures_terulet_arany` | cím nélküli cellák területaránya a 7. lépésben |
| `vagas_T`, `vagas_kozep`, `vagas_kozos`, `vagas_parhuzamos`, `vagas_voronoi` | vágástípusok (8.) |
| `jelzes_folytonossag`, `jelzes_haromszog`, `jelzes_besorolatlan` | jelzések a körzeteken |
| `jelzes_validacio` | 1, ha a 10.3 bukott |
| `n_rang3_torolt`, `n_narancs` | 1.4 és 1.5 |
| `qa_cimek_kivul`, `qa_foutak_kivul`, `lakott_gyanus` | 0.4 QA |
| `n_azonos_pont`, `n_hibas_sziget` | kihagyott címek |
| `n_korzet`, `n_kategoria` | létrejött körzetek / kategóriák a címekben |
| `egykategorias` | 0.1 rövid út |
| `nincs_utcahalozat` | a cache-ben nincs utcahálózat |
| `n_cim_hataron_kivul` | a közigazgatási határon kívüli, eldobott címek |

---

## 18. Kötegelt futtatás

`burkolas_multiple.burkolas_multiple(cimek_gpkg, out_gpkg, varosok=None, n_workers=None)`:

- `multiprocessing` **spawn** kontextus, `Pool(processes=cpu_count−2, maxtasksperchild=25)`
  (a többnapos futásnál lapos memória; `Pool`, nem `ProcessPoolExecutor`, mert az a kilépő
  workereket nem pótolja megbízhatóan), `imap_unordered(chunksize=1)`.
- A workerek csak számolnak (`burkol_telepules` + EOV-reprojekció), a stdout-jukat
  `redirect_stdout`-tal elkapjuk és a logba írjuk; **a fő processz az egyetlen író** (az sqlite nem
  bírja a párhuzamos írást): településenként `cimek`, majd `poligonok` réteghez fűz (`mode='a'`).
- A bemeneti pontokat egyszer olvassa be, településenként előszűrve adja át.
- **Folytatás:** a gyűjtő `poligonok` rétegében már meglévő települések kihagyva.
- Státuszok: `ok`, `skip`, `nincs`, `ures`, `hiba` (kivétel → traceback a logba).
- Kimenet: `data/kesz_data/2026_v3.gpkg` + `2026_v3_naplo.csv` + `2026_v3_log.txt` (fájlba
  INFO+, konzolra WARNING+).

---

## 19. Validáció és tesztek – eredmények

### 19.1 Szintetikus teszt (`teszt_v3_szintetikus.py`) – **zöld**

Mesterséges település UTM 34N-ben: lakott folt 0..320 × 0..200 lyukkal (120..140 × 80..100), a
közigazgatási határ x = 300-nál vág; külön utca nélküli folt 400..450 × 0..50; Fő út (rang 5,
y = 100), Petőfi (x = 100) és Kossuth (x = 200) utca, Zsák utca (zsákutca), lógó bejáró (rang 3),
két utcát összekötő köz (rang 3), Határ menti utca y = 197-en (3 m-es sliver), vasút y = 20; két
kategória. Lépésenkénti assert-ek:

| Lépés | Ellenőrzés |
|---|---|
| 0.2 | 2 folt, terület = 300·200 − 20·20 + 50·50 |
| 0.4 | 1 utca nélküli folt; hálózatos terület = 300·200 − 400 |
| 1.4 / 1.5 / 1.7 | 1 rang 3 lógó törölve; 1 narancs; a köz és a zsákutca a hálóban; nincs lógó |
| 2. | 1 foltcella; teljes lefedés lyuk nélkül; nincs mindkét oldalon külvilágú él; a lyukba eső cím (130, 90) nincs cellában |
| 3. | nincs sliver, terület változatlan, pontosan 1 sliver olvadt be |
| 4. | a téglalap-tömb 4 sávra bomlik (fout, petofi, kossuth, koz); vázvonalak ≤ 12 csúcs (egyenes); terület változatlan |
| 5. | 4 sarokcella, területük [250, 750, 750, 750] m² (±5); terület változatlan |
| 7. / 8. | nincs azonos pont; nincs vegyes cella a 8. után; terület változatlan |
| 9. | minden cella besorolva |
| 10. | validáció rendben; két körzet {1, 2}; a körzetek a várt helyen |

### 19.2 Valós teszt-települések

| Település | Eredmény | Idő |
|---|---|---|
| **Halásztelek** | 3289 cella, 24 vegyes; vágások: T 1, közép 7, párhuzamos 6, Voronoi 10; 21 narancs, 28 rang 3 törölve; 8 hibás sziget; **8 körzet / 8 kategória, validáció rendben**; determinisztikus (két futás azonos) | ~12 s |
| **Budapest XII. kerület** | **55 körzet, validáció rendben** (a −8724 m²-es lefedési hibát az átfedő OSM lakott poligonok okozták → 0.2 unió) | ~65 s |
| **Ibafa** | egykategóriás rövid út (0.1), 1 körzet | < 1 s |
| **Tata** | nincs V3 utcahálózat a cache-ben → csak foltcellás út, összeomlás nélkül, `nincs_utcahalozat` jelzés | – |

V2–V3 összevetés (Halásztelek): a V3 összterülete kisebb – a különbség (≈ 257 000 m²) a lakott
foltok lyukaiból adódik, amelyeket a V3 (helyesen) nem burkol.

### 19.3 Kötegelt próbafutás (24 település)

22 `ok`, 2 `nincs` (Kétvölgy, Simaság – nincs `landuse=residential` a cache-ben), **0 hiba, 0 validációs hiba**;
a folytatás működik (második indítás mindent kihagy); a napló CSV kitöltve. Néhány sor:

| Település | n_cella | n_korzet | n_vegyes | T / közép / közös / párh. / Voronoi | hibás sziget |
|---|---:|---:|---:|---|---:|
| Ajka | 10599 | 29 | 23 | 7 / 7 / 0 / 3 / 6 | 4 |
| Abony | 3376 | 16 | 41 | 3 / 14 / 2 / 7 / 15 | 8 |
| Albertirsa | 2467 | 16 | 54 | 8 / 13 / 0 / 6 / 27 | 16 |
| Ács | 1835 | 8 | 24 | 2 / 10 / 0 / 1 / 11 | 0 |
| Alsóörs | 3822 | 2 | 18 | 1 / 9 / 2 / 3 / 3 | 2 |

Megfigyelés: a vegyes cellák aránya 0–2,2 %; a Voronoi a vegyes cellák kb. harmadánál-felénél
kell; a `lakott_gyanus` jelzés több településen a QA (b) > 30 % miatt jön (a lakott területen
kívül futó főutak).

---

## 20. Az összes eltérés és kiegészítés egy táblában

| # | Hol | Típus | Mit csinál a kód | Miért |
|---|---|---|---|---|
| 1 | Bemenet | BEMENETI ELTÉRÉS | `szavazokorid → szkid` | az A-fázis oszlopneve |
| 2 | Bemenet | BEMENETI ELTÉRÉS | lista-értékű `name/osmid` → első elem; `highway` lista → legmagasabb rang | OSM-adat |
| 3 | 0.4 rangtábla | BEMENETI ELTÉRÉS | `road` → 4, egyéb ismeretlen highway → 3 | az `all` hálózatban táblán kívüli értékek |
| 4 | Bemenet | BEMENETI ELTÉRÉS | kétirányú élek dedupe (rendezetlen u,v,key) | osmnx gráf |
| 5 | 1.1 | KIEGÉSZÍTÉS | név nélküli él `utca_id = osm_<osmid>` | a leírás nem rendelkezik |
| 6 | Bemenet | BEMENETI ELTÉRÉS | közig. határon kívüli címek eldobva, számlálva | hibás geokódolás (V2-ben is így volt) |
| 7 | 0.2 | BEMENETI ELTÉRÉS | a vágott foltok uniója, összefüggő darabokra | átfedő OSM lakott poligonok |
| 8 | 0.3 QA (b) | KIEGÉSZÍTÉS | nevező: rang 4–5 hossz a foltok convex hull-ján belül | a pontos nevező nincs kimondva |
| 9 | 0.4 | KIEGÉSZÍTÉS | utcahálózat nélküli település: minden folt foltcella + `nincs_utcahalozat` | hiányzó cache |
| 10 | 1.2 | KIEGÉSZÍTÉS | nem határ vonalak `snap`-je a határhoz RÁCS-on belül | külön kerekítés után 1 cm-es elcsúszás |
| 11 | 1.3, 1.6, R | KIEGÉSZÍTÉS | `unary_union(grid_size=RACS)` (snap-rounding noding) | mm-es lógó végek nélküle |
| 12 | 1.5 | KIEGÉSZÍTÉS | sugár = bbox-átló + 1 m; egybeeső szakasznál a legközelebbi pont | a „félegyenes” véges alakja |
| 13 | 2.1, R | BEMENETI ELTÉRÉS | lyukban keletkező lap eldobva (rep. pont a lakott területen kívül) | lyukas lakott foltok |
| 14 | 2.3 | KIEGÉSZÍTÉS | a foltcellák gyűrűi rang 7 élként az éltáblában | a foltcellák is éltábla-tagok |
| 15 | R | KIEGÉSZÍTÉS | láncok törése az előző éltábla végpontjainál | attribútum-vesztés 2-fokú csúcsban |
| 16 | R | KIEGÉSZÍTÉS | bal/jobb második mintavétel a leghosszabb szakasz közepén | zárt gyűrű felezőpontja csúcsra eshet |
| 17 | R | KIEGÉSZÍTÉS | több lapra eső régi cella: a többi lap új id | a leírás nem rendelkezik |
| 18 | 0.4 / R | KIEGÉSZÍTÉS | nyertes szomszéd döntetlenje: kisebb id | determinizmus |
| 19 | 3.2, 6. | KIEGÉSZÍTÉS | T-csomópont: a saját él 5°-on belüli egyenes folytatása nem idegen él | információ nélküli sarokháromszögek |
| 20 | 3.2, 6. | KIEGÉSZÍTÉS | T-vágás iránya: a két határszakasz átlagolt érintőjére merőleges, befelé | a pontos irány nincs kimondva |
| 21 | 3.3 | LEÍRÁS + pontosítás | beolvadni nem tudó sliver: `sliver=False`, a 6. kezeli | – |
| 22 | 4. | KIEGÉSZÍTÉS | egy utcájú tömb = egy sáv Voronoi nélkül; utca nélküli tömb marad tömb | triviális eset |
| 23 | 4. | KIEGÉSZÍTÉS | `utca_vonal_idx`: a leghosszabb közös határú U-komponens | egy utcanév több komponense |
| 24 | 4.3 | KIEGÉSZÍTÉS | nem-utca él pontjai `el_<id>` saját csoport | a beolvasztáshoz |
| 25 | 4.3 | KIEGÉSZÍTÉS | `linemerge` a `simplify` előtt | az egyszerűsítés különben hatástalan |
| 26 | 4.3 | KIEGÉSZÍTÉS | területvédelem: rossz egyszerűsítésnél a nyers vázvonal | az egyszerűsített vonal kiléphet |
| 27 | 4.3 | KIEGÉSZÍTÉS | csak nem-utca szomszédú nem-utca sáv → legnagyobb utca-sáv; azonos utcájú sávok egyesítése | a leírás nem rendelkezik |
| 28 | 5. | ELTÉRÉS | vágás csak a P→X szakasszal (a négyszög belső oldalai), nem a teljes merőlegessel | a teljes húr a sáv távolabbi részét is vágta |
| 29 | 5. | KIEGÉSZÍTÉS | ≥ 3 utcás csúcsban az első két utca; sarkok koordináta-sorrendben | determinizmus |
| 30 | 7., 8.5 | SPEC-HIÁNY | azonos koordinátán több kategória → többségi + `azonos-pont` jelzés | szétválaszthatatlan |
| 31 | 8.1 | KIEGÉSZÍTÉS | érintő `t ± 0,5 m`-ből; köz-ellenőrzés; ¼, ¾ próbák | görbe utca |
| 32 | 8.2 | KIEGÉSZÍTÉS | szemközti sáv = ugyanaz a tömb + közös vázvonal-él; csak két-futamú, azonos kategóriapárú sávok | a pontos feltétel |
| 33 | 8.3 | KIEGÉSZÍTÉS | offset oldal: az első előjel, amelynél a görbe metszi a sávot | az oldal kiválasztása |
| 34 | 8.4 | KIEGÉSZÍTÉS | elő-/utófutam: > 2 futam és a kategória máshol nem fordul elő | a pontos feltétel |
| 35 | 8.4 | KIEGÉSZÍTÉS | magok RÁCS-egyedítése; t = 0 bukásnál a nyers al-cellák; területvédelem | GEOS duplikált mag, robusztusság |
| 36 | 9.1 | KIEGÉSZÍTÉS | ugyanazon oldal = a rep. pontokat összekötő szakasz nem keresztezi U-t; egy menet, csak címes forrás | determinizmus |
| 37 | 10.1 | ELTÉRÉS | az újra-besorolás 9.1 nélkül | a 9.1 a szigetet visszahozná |
| 38 | 10.2 | KIEGÉSZÍTÉS | besorolatlan cellák külön `besorolatlan` sorban | teljes lefedés |
| 39 | 10.3 | KIEGÉSZÍTÉS | körzetszám = kategóriaszám ellenőrzés; a cím-ellenőrzés `intersects`-szel | a V2 ellenőrzése; határon ülő címek |
| 40 | 11. | KIEGÉSZÍTÉS | `szin` oszlop a körzeteken és a címeken (aranymetszés-paletta) | QGIS-megjelenítés, V2-ben is volt |

---

## 21. Ismert korlátok, nyitott pontok

1. **Az országos V3 OSM-letöltés félbemaradt** (2026-11-03-án leállt a 21/63. adagnál; 1043/3131
   település van a cache-ben, lásd 2.1). Teendő: a letöltés folytatása, majd az országos burkolás: `cd szburkolas && ../.venv/bin/python burkolas_multiple.py`.
2. **Javasolt spec-kiegészítések** a tervezőnek: az azonos koordinátájú eltérő kategóriák szabálya
   (#30), a 10.1 utáni 9.1-kihagyás (#37), a sarokvágás P→X szakasza (#28), a besorolatlan sor
   (#38), a körzetszám-ellenőrzés (#39), valamint a R. rutin élek törése (#15).
3. **Debug-attribútum hiány:** a `8_1_vagas_T` és `8_1_vagas_kozep` rétegek `t` oszlopa jelenleg
   0,0 helyőrző (a vágás t-értéke nincs továbbadva a gyűjtőbe); a geometria helyes.
4. **Napló-duplikáció:** a `nincs` státuszú települések folytatáskor újra bekerülnek a napló
   CSV-be (a folytatás a `poligonok` réteg alapján dönt, ott ezek nem szerepelnek). Kozmetikai.
5. A 3.4 V-alakú csonk (egycímes háromszög) a leírás szerint ismert korlát: jelezve, nem javítva.
6. Az OSM/Nominatim közigazgatási határ tölti be a leírás „hivatalos határ” szerepét (nem
   állami/GISCO forrás).

---

## 22. Törölt V2 kód

`git rm`: `szburkolas/polir.py`, `polygon_fuggvenyek.py`, `poligon_szk_fuggvenyek.py`,
`burkolas_multiple_tobbmag.py`, `polygon_io.py`, `Untitled`. A V2 a git történetben megvan
(utolsó V2 commit: `906c079 v3 elott`). A `burkolas_multiple.py` és a `burkolas_v3.py` teljesen
újraírva; a V2 narancs+kék segédvonal, párhuzamos snap, V-zászló, tömb = cella és polír
eljárásai nincsenek a V3-ban.

---

## A. melléklet – teljes függvény-referencia

Gépi úton, a forráskódból (AST) generálva: minden modul minden függvénye és osztálya, a sor
számával és a docstring első bekezdésével. Így nincs olyan kódrész, ami a naplóból kimaradna.


### `szburkolas/v3_param.py` (201 sor)

> v3_param – a V3 burkolás paraméterei, él-típusai, rangjai, színei.

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 168 | `rang_szin(rang, tipus)` | Egy vonaldarab színe a rangja (és típusa) szerint a 11.1 táblázat alapján. |
| 191 | `kategoria_paletta(szkid_ertekek)` | A `szkid` szerinti paletta: minden kategória egy jól elkülönülő, determinisztikus színt kap. Aranymetszés-léptetés a színkörön, a kategóriák rendezett sorrendjében (így ugyanaz a település mindig ugyanazt a palettát kapja). |

### `szburkolas/v3_io.py` (355 sor)

> v3_io – a V3 burkolás bemeneteinek betöltése, a debug GPKG írója és a kimenetek írása.

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 46 | **class** `NincsLakottTerulet` | A településnek nincs lakott foltja (vagy a közigazgatási határra vágva nem marad belőle): a település kimarad – a határral SOHA nem helyettesítjük (0.1). |
| 55 | `_json_visszafejtes(df)` | A letöltő JSON-stringgé alakított lista-oszlopait (osmid, highway, name) visszafejti. Ami nem érvényes JSON (pl. a 'Petőfi utca' név), érintetlen marad. |
| 74 | `_elso_elem(v)` | Lista-értékű OSM-attribútum (pl. több név egy vonalon) → az első elem (terv #4). |
| 83 | `highway_rang(highway)` | OSM highway címke → rang (0.4 táblázat). Lista esetén a LEGMAGASABB rang nyer (terv #4); 'road' → rang 4; a táblán kívüli ismeretlen címke → rang 3 (terv #5). |
| 96 | `utca_id_kepzes(nevek, osmidk)` | 1.1 utca_id: az OSM `name` ujjlenyomata az A-fázis függvényével (rövidítés-feloldás, ékezettelenítés, kisbetű, írásjelek törlése). Név nélkül az osmid az azonosító. |
| 112 | **class** `Bemenet` | Egy település összes bemenete, helyi UTM-vetületben. |
| 123 | `_parquet_telepules(utvonal, varos)` | Egy település sorai a parquet-ből, pyarrow-szűréssel (nem olvassuk be az egész országot). |
| 130 | `cimek_elokeszites(cimek_wgs, varos, crs)` | A címpontok a településre szűrve, UTM-re vetítve, a leírás oszlopneveivel. Bemeneti eltérés (terv #2): az A-fázis oszlopa `szavazokorid`, a leírás `szkid`-et ír. |
| 148 | `betolt_telepules(varos, cimek_wgs)` | A település minden bemenetének betöltése és UTM-re vetítése (leírás: Bemenetek). |
| 232 | `olvas_varosok_txt()` | A feldolgozandó települések listája (varosok.txt, soronként egy név, sorrendtartó). |
| 241 | `nyil(geom_tol, geom_ig)` | Debug-nyíl: szakasz az egyik geometria reprezentatív pontjától a másikéig (3.3, 9.). |
| 246 | **class** `DebugIro` | A `<település>_debug.gpkg` írója. Minden réteg közös oszlopai: `lepes`, `tipus`, `szin` (hex), plusz a réteg saját attribútumai. A rétegneveket a leírás 11.1 táblázata adja. Induláskor a korábbi fájlt törli, hogy régi futás rétegei ne maradjanak benne. `utvonal=None` → inaktív író: minden hívás azonnal visszatér (kötegelt futás). |
| 252 | `DebugIro.__init__(self, utvonal, crs)` | Az író létrehozása; aktív írónál a meglévő `<VAROS>_debug.gpkg` törlődik (régi rétegek ne maradjanak), a kategória-paletta üresen indul. *(kézi leírás – nincs docstring)* |
| 262 | `DebugIro.aktiv(self)` | (property) igaz, ha van kimeneti útvonal; kötegelt futásnál hamis, így minden debug-hívás azonnal visszatér. *(kézi leírás)* |
| 265 | `DebugIro.kategoria_szin(self, szkid)` | A kategória színe a palettából; besorolatlan (None) → üres-cella szürke. |
| 271 | `DebugIro.reteg(self, nev, lepes, tipus, geometriak, szin, **attributumok)` | Egy réteg kiírása. `geometriak`: lista/GeoSeries; `szin`: egy hex kód vagy soronkénti lista; `attributumok`: a réteg saját oszlopai, skalár vagy soronkénti lista. |
| 294 | `DebugIro.cellak(self, nev, lepes, cellak, allapot, **attributumok)` | Cella-réteg a 11.1 színszabállyal: sliver rózsaszín, sarok (besorolatlan) halványmagenta, vegyes narancs, egykategóriás a kategória színe, üres szürke. |
| 319 | `DebugIro.eltabla(self, nev, lepes, eltabla)` | Éltábla-réteg: szín a rang/típus szerint, saját attribútumok a 11.1 szerint. |
| 343 | `ir_eredmeny(utvonal, korzetek, cimek, varos)` | Egy település eredménye külön GPKG-be (kézi futtatás): `poligonok` és `cimek` réteg, a gyűjtő sémájával és vetületével (EOV). |

### `szburkolas/v3_vonalhalo.py` (449 sor)

> v3_vonalhalo – 0. lépés (előkészítés), 1. lépés (vonalháló) és 2. lépés (cellák képzése).

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 27 | `lepes_0_2_lakott_vagas(bemenet, debug)` | MINDEN lakott foltRA: folt ← folt ∩ közigazgatási határ; HA üres → eldobom. Szabály: ha kérdéses, mindig a hivatalos közigazgatási határ a szél. Visszatérés: GeoSeries, soronként egy Polygon (a MultiPolygon eredmény darabolva). |
| 54 | `lepes_0_3_vonalak_vagasa(vonalak_nyers, lakott_vagott, debug)` | MINDEN vonalRA (utca, vasút, folyó): vonal ← vonal ∩ (lakott foltok uniója), az attribútumok megmaradnak; MultiLineString → darabok. A hálózaton más előkészítés nincs. |
| 103 | **class** `Foltok` | A 0.4 lépés kimenete. |
| 113 | `lepes_0_4_foltok(lakott_vagott, vonalak_vagott, cimek, qa_foutak_kivul, debug)` | MINDEN lakott foltRA: HA fut benne vonal (hálózat ∩ folt hossza > 0) → HÁLÓZATOS folt, KÜLÖNBEN → UTCA NÉLKÜLI folt. lakott_terület = hálózatos foltok uniója; határ = körvonala. |
| 162 | **class** `Vonalhalo` | Az 1. lépés kimenete: zárt vonalháló, minden darab attribútummal, és a napló-mutatók. |
| 171 | `_osszefesules(tabla)` | Összefésülés (noding): az összes vonal egyetlen lépésben egyesítve (unary_union) → minden kereszteződésben közös csúcs, minden vonal ott elvágva; utána attribútum-visszaillesztés (1.3). Az egyesítés a RÁCS rácson fut (grid_size): így a lakott határra vágott utcavég, amely a kerekítés után legfeljebb fél centire ül a határ szakaszától, biztosan csomópontot kap (snap-rounding noding) – enélkül mm-es lógó végek maradnának a határ mellett. Visszatérés: GeoDataFrame [tipus, rang, utca_id, geometry] a darabokról. |
| 184 | `_rang3_logo_vegek_torlese(darabok, debug)` | 1.4 ISMÉTELD, AMÍG változik: MINDEN rang 3 darabRA, HA valamelyik végpontjához egyetlen más darab sem csatlakozik (fokszám 1) → törlöm. Visszatérés: (darabok, törölt db). |
| 209 | `_narancs_vonalak(darabok, debug)` | 1.5 Zsákutcák lezárása – narancs segédvonal. |
| 278 | `_logo_vegek_torlese(darabok, debug)` | 1.7 ISMÉTELD: poligonok, lógó, cut, érvénytelen ← polygonize_full(darabok) név szerint; HA lógó üres ÉS cut üres → KÉSZ; darabok ← darabok − lógó − cut. A ciklus konvergál, mert minden körben fogy a vonal. Egy darab akkor tartozik egy lógó/cut vonalhoz, ha a felezőpontja rajta ül (RÁCS-on belül). |
| 311 | `utca_vonalak_epites(tabla)` | {utca_id: [LineString, ...]}: ugyanannak az utcának a vonalháló-darabjai komponensenként összefűzve (linemerge). Ez az U vonal a 8.1 vetítéshez, a 8.3 párhuzamoshoz és a 9.1 folytonossághoz; az utcák a folyamat során nem mozdulnak, ezért egyszer számoljuk. |
| 323 | `lepes_1_vonalhalo(foltok, vonalak_vagott, debug)` | 1. lépés – zárt vonalháló, amelynek minden hurka egy cella, egyetlen vonalvég sem lóg, és minden vonaldarab tudja, honnan jött. |
| 398 | `lepes_2_cellak(vonalhalo, foltok, cimek, debug)` | 2.1 poligonizálás (HA lógó ≠ ∅ VAGY érvénytelen ≠ ∅ → HIBA, a település leáll); 2.2 utca nélküli foltok egy-egy cellaként; 2.3 éltábla és címek (R. rutin 4–5. pontja). Innentől vonalhálót nem építünk: minden további művelet poligon-művelet. Visszatérés: (cellak, eltabla, cimek). |

### `szburkolas/v3_topologia.py` (567 sor)

> v3_topologia – az R. rutin (topológia-frissítés), az éltábla és a cellaszintű geometriai segédműveletek (T-csomópontok, T-feldarabolás, cella vágása vonallal).

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 34 | `polygonize_full_nev_szerint(vonalak)` | `shapely.polygonize_full` NÉV szerint kicsomagolva (leírás 1.7, 14.): a felső szintű függvény sorrendje (polygons, cuts, dangles, invalid). Egyetlen helyen, hogy a sorrend-hiba kizárt legyen. Minden érték lista. |
| 47 | `vonalak_darabokra(geometria)` | Egy (Multi)LineString / GeometryCollection → a benne lévő, nem üres LineString-ek listája. |
| 53 | `poligonok_darabokra(geometria)` | Egy (Multi)Polygon / GeometryCollection → a benne lévő, nem üres Polygon-ok listája. |
| 59 | `vegpontok(vonal)` | Egy LineString két végpontja (x, y) tuple-ként – RÁCS után egzaktan összehasonlítható. |
| 65 | `vegpont_fokszam(vonalak)` | Csomópont-fokszám: hány vonaldarab végződik az adott koordinátán (leírás 14.: a darabok végpont-koordinátáinak számlálása, RÁCS után egzakt). |
| 76 | `felezopont(vonal)` | A vonaldarab felezőpontja (1.3: csak egy eredeti vonalon ül, a végpontok nem). |
| 81 | `_irany_a_ponton(vonal, arany)` | A vonal érintő-irányvektora (egységnyi) az `arany` (0..1) normalizált helyen lévő pontban: annak a szakasznak az iránya, amelyen a pont ül. |
| 95 | `normalvektor(irany)` | Az irányvektorra merőleges egységvektor (bal oldal felé: +90°). |
| 100 | `hosszabbit(vonal, hossz)` | Egy LineString mindkét végét `hossz` méterrel meghosszabbítja a végszakasz irányában (a vágóvonalak túlnyúlása, hogy a split biztosan záródjon – leírás 8.4). |
| 116 | `attributum_visszaillesztes(darabok, eredeti_tabla, hianyzo_tipus, hianyzo_rang)` | A darabok (LineString-lista) attribútumai az eredeti vonaltáblából a felezőpont alapján. |
| 171 | `_leghosszabb_szakasz_kozepe(vonal)` | A vonal leghosszabb szakaszának felezőpontja normalizált (0..1) helyként – mindig egy szakasz belsejében van, soha nem csúcson (a zárt gyűrűk negyedelő pontjai mind csúcsok lennének). |
| 181 | `_oldalpontok(vonal, tavolsag, arany)` | A vonal `arany` helyén lévő pontból a normál mentén ±`tavolsag`-ra eltolt két pont (bal, jobb) a bal/jobb cella megkereséséhez. |
| 189 | `eltabla_epites(vonalak, attributumok, cellak)` | Éltábla (R. rutin 4. pont): minden vonaldarabhoz a két cella, amit elválaszt. |
| 238 | `cella_elei(eltabla, cella_id)` | Az éltábla sorai, amelyek az adott cellát határolják (bal vagy jobb oldalon). |
| 243 | `szomszed_id(el, cella_id)` | Egy él túloldali cellája az adott cella felől nézve. |
| 248 | `szomszedok_osszesitve(eltabla, cella_id, csak_cellak)` | Egy cella szomszédai az éltáblából: {szomszéd_id: (a közös élek legkisebb rangja, a közös élek összhossza)}. A külvilág (−1) nem szomszéd. `csak_cellak`: ha megadott, csak az ebben szereplő szomszédok számítanak (pl. csak besoroltak, csak nem-sliverek). |
| 265 | `nyertes_szomszed(osszesites)` | A beolvasztás szabálya (0.4): a legkisebb rangú közös él; egyenlő rangnál a leghosszabb. Visszatérés: (szomszéd_id, rang, hossz) vagy None. |
| 278 | `cimek_cellaba(cimek, cellak)` | Minden címpont a cellája `cella_id`-jét kapja (a cella BELSEJÉBEN lévő pontok; a határon ülők és a lakott területen kívüliek egyik cellához sem tartoznak → None). |
| 300 | `szamito_cimek(cimek)` | A besorolásban számító címek: amelyeknek nincs kihagyó jelzése. |
| 306 | `cella_kategoriak(cimek)` | Cellánként a benne lévő, számító címek kategóriái: {cella_id: Counter(szkid → db)}. |
| 320 | `uj_cellatabla(sorok, crs)` | Cellatábla a kötelező oszlopokkal; a hiányzó attribútumok alapértéket kapnak. |
| 331 | `kovetkezo_cella_id(cellak)` | Az első szabad cella_id (az id-k monoton nőnek, törölt id nem kerül újra kiosztásra). |
| 340 | `_vonalak_torese_csomopontoknal(vonalak, csomopontok)` | A láncokat elvágja minden olyan BELSŐ csúcsuknál, amely az előző éltábla valamelyik élének végpontja. Ok: a cellahatárok uniója csak ott ad csomópontot, ahol ≥3 cella találkozik; egy utca-él és egy narancs-él 2-fokú találkozásánál a két él egy láncba olvadna, és a felezőpontos visszaillesztés az egyik attribútumát elveszítené. Vonal nem mozdul. |
| 357 | `topologia_frissites(cellak, eltabla_elozo, cimek, uj_vonal_tipus)` | R. rutin – minden fázis végén. Biztosítja, hogy a cellák közös élei bájtra pontosan egybeessenek, és az éltábla a teljes aktuális állapotot tükrözze. |
| 425 | `vegpont_index(eltabla)` | {csúcs-koordináta: [(irány a csúcsból kifelé, cella_bal, cella_jobb), ...]} az éltábla minden élének mindkét végpontjára – a T-csomópontok kereséséhez. |
| 444 | `t_csomopontok(cella_id, cella_geom, index)` | A cella határának azon csúcsai, amelyekben a cella határához nem tartozó vonaldarab is végződik vagy áthalad (éltáblából: ≥ 3 él a csúcsban). Az idegen él nem számít, ha a cella saját élének egyenes folytatása (a csúcsból kifelé mérve az iránya a saját él irányának ellentettje; pl. a cella sarkában egyenesen továbbfutó utca): ott a határra merőleges vágás csak egy pár méteres sarokháromszöget vágna le, információ nélkül. Visszatérés: [(csúcs, előző csúcs, következő csúcs)] a vágás irányához. |
| 481 | `t_vagovonal(cella_geom, csucs, elozo, kovetkezo)` | Vágóvonal egy T-csomópontból: a határra merőlegesen a cella belseje felé, az első kilépésig a cellából (a szemközti élig). A határ érintője a két szomszédos határszakasz átlagolt iránya; a belső irányt a csúcs melletti próbapont dönti el. Visszatérés: a vágóvonal mindkét végén VAGOVONAL_TULNYULAS-sal meghosszabbítva, vagy None. |
| 519 | `cella_felosztas(cella_geom, vagovonalak)` | Egy cella belső felosztása vágóvonalakkal (`shapely.ops.split`). A vágóvonalak egyszerre hatnak. Visszatérés: a darabok (Polygon-lista); ha a vágás nem változtat, [cella_geom]. |
| 535 | `t_feldarabolas(cellak, eltabla, cella_idk, debug, lepes)` | T-feldarabolás (3.2, 6.): a megadott cellák mindegyikét elvágjuk minden T-csomópontjánál. Visszatérés: (új cellatábla, T-csomópontok listája, vágóvonalak listája) – az utóbbi kettő a debug rétegekhez. |

### `szburkolas/v3_sliver.py` (125 sor)

> v3_sliver – 3. lépés: sliver-tisztítás.

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 17 | `beirt_atmero(cella_geom)` | A cella legnagyobb beírt körének átmérője (shapely.maximum_inscribed_circle; az eredmény a középpontból a legközelebbi határpontba húzott szakasz, hossza a sugár). |
| 25 | `lepes_3_1_sliver_felismeres(cellak, cimek, debug)` | 3.1 MINDEN cellára: SLIVER, HA 2·r < TELEKSZÉLESSÉG ÉS a cellában nincs címpont. A `sliver` oszlopot állítja. Visszatérés: cellak. |
| 44 | `lepes_3_2_t_feldarabolas(cellak, eltabla, debug)` | 3.2 MINDEN sliverRE: elvágjuk minden T-csomópontjánál (a határra merőlegesen, az első kilépésig). Így minden darab egy tömb hosszú, és csak a saját tömbjébe tud beolvadni. |
| 54 | `lepes_3_3_beolvasztas(cellak, eltabla, cimek, debug)` | 3.3 ISMÉTELD, AMÍG van sliver-darab: minden sliver-darab a legkisebb rangú közös élen át (egyenlő rangnál a leghosszabbon) beolvad a nem-sliver szomszédjába; körönként R. rutin. HA egy körben semmi nem olvadt be → a maradék cím nélküli cellaként marad (6. lépés kezeli). Visszatérés: (cellak, eltabla, cimek). |
| 92 | `lepes_3_4_egycimes_haromszog_jelzes(cellak, cimek)` | 3.4 Ismert korlát: egycímes, telekszélesség alatti cella (a V-alakú csonk háromszöge, amelynek végében egy cím van). Nem sliver (van címe), de a kimenetben jelezni kell. |
| 108 | `_jelzes_hozzaad(meglevo, uj)` | Jelzés hozzáfűzése ';'-vel, ismétlés nélkül. |
| 116 | `lepes_3_sliver_tisztitas(cellak, eltabla, cimek, debug)` | 3. lépés – sliver-tisztítás: felismerés, T-feldarabolás, beolvasztás, R. rutin, jelzés. |

### `szburkolas/v3_savok.py` (389 sor)

> v3_savok – 4. lépés (tömbök felbontása utcafront-sávokra) és 5. lépés (sarokcellák).

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 22 | `_nem_utca_csoport(el_id)` | Nem-utca él (lakott határ, vasút, folyó, segédvonal) saját csoportneve a sávosításban. |
| 27 | `_utca_vonal_index(utca_vonalak, utca_id, sav_geom)` | Egy sáv U-vonala: az utca azon komponense, amellyel a sáv határának a leghosszabb a közös része (ha egyik sem érinti, a legközelebbi). Visszatérés: komponens-index vagy None. |
| 44 | `tomb_savositas(tomb_geom, tomb_elek, debug_gyujto)` | Egy tömb felbontása utcafront-sávokra Voronoival a tömb határpontjaiból (4.3). |
| 192 | `lepes_4_savositas(cellak, eltabla, cimek, utca_vonalak, debug)` | 4. lépés – MINDEN tömbRE (cella, amelynek határán ≥ 2 különböző utca_id van) sávosítás. Ha a tömb határán csak egy utca van, a tömb maga egy sáv; ha egy sincs, nem sávosítunk. Visszatérés: (cellak, eltabla, cimek) az R. rutin után (a vázvonalak itt kapják a vázvonal típust és az 1-es rangot). |
| 256 | `_lanc_s_vegponttal(elek_geom, S)` | A tömb egy utcájának éleiből összefűzött lánc(ok) közül az, amelynek S végpontja. Visszatérés: (lánc, S az eleje?) vagy None. |
| 269 | `_meroleges_egyenes(lanc, S_elol, d, atlo)` | P ← S-től d méterre a lánc mentén; m ← a láncra P-ben merőleges, mindkét irányban `atlo` hosszú egyenes-szakasz (a tömbre később vágjuk). Visszatérés: (P, m). |
| 280 | `tomb_sarkai(tomb_geom, tomb_elek)` | Egy tömb sarok-csomópontjai: a határ csúcsai, ahol két KÜLÖNBÖZŐ utca_id-jű utca találkozik (ugyanannak az utcának a kanyarja nem sarok). Visszatérés: [(S, U1, U2)]. |
| 297 | `lepes_5_sarokcellak(cellak, eltabla, cimek, tombok, eltabla_tombok, debug)` | 5. lépés – MINDEN tömbRE, MINDEN sarok-csomópontRA (S; U1, U2): d ← min(SAROKMÉRET, az utca e tömb menti hosszának fele); P1, P2 a két utcán d-re S-től; m1, m2 a merőlegesek; X ← m1 ∩ m2 (HA X nincs a tömbben → nincs sarokcella); sarok_négyszög ← S, P1, X, P2; az U1 és U2 sávjait elvágjuk m1 és m2 mentén; sarokcella ← azok a darabok, amelyek reprezentatív pontja a sarok_négyszögben van. A vágás a merőlegesek P–X szakaszával történik (a négyszög két belső oldala): így a sarok pontosan a négyszög, a sávok távolabbi részét a merőleges nem darabolja. `tombok`/`eltabla_tombok`: a 3. lépés utáni (sávosítás előtti) tömbök és éltáblájuk. Visszatérés: (cellak, eltabla, cimek) az R. rutin után. |
| 384 | `_hosszabbit_eleje(vonal, hossz)` | A vonal ELEJÉT hosszabbítja meg `hossz` méterrel (a vége – X – helyben marad). |

### `szburkolas/v3_felosztas.py` (712 sor)

> v3_felosztas – 7. lépés (besorolás) és 8. lépés (vegyes cellák felosztása).

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 23 | `jelzes_hozzaad(meglevo, uj)` | Jelzés hozzáfűzése ';'-vel, ismétlés nélkül. |
| 35 | `azonos_pont_jelzes(cimek, cella_idk)` | Terv #7: egy koordinátán több cím eltérő kategóriával (az A-fázis több-a-többhez párosítása). Ilyen pont nem választható szét semmilyen vágással; a ponton a többségi kategória marad, a kisebbségi címek 'azonos-pont' jelzést kapnak és a besorolásban nem számítanak. Csak a megadott (vegyes) cellák címeit vizsgálja. Visszatérés: (cimek, db). |
| 56 | `lepes_7_besorolas(cellak, cimek, debug)` | 7. MINDEN cellára: 0 cím → ÜRES; minden cím egy kategóriájú → a cella azé a kategóriáé (szabály: címes); több kategória → VEGYES → 8. lépés. Visszatérés: (cellak, cimek, vegyes cella_id-k listája, n_azonos_pont). |
| 87 | `futamok(kategoriak_sorban)` | Egy rendezett kategória-sor futamai: [(kategória, első index, utolsó index)]. |
| 98 | `tiszta(futam_lista)` | TISZTA, HA minden kategória egyetlen összefüggő futamot alkot (A A B B: igen; A B A: nem). |
| 103 | `erinto_irany(U, t)` | Az U vonal érintő-irányvektora a t méterjelnél: a t ± ERINTO_FELHOSSZ pontok iránya (görbe utcánál is egyértelmű). |
| 114 | `meroleges_egyenes(U, t, hossz)` | Az U-ra a t méterjelnél merőleges, mindkét irányban `hossz` hosszú egyenes-szakasz. |
| 121 | `vago_a_cellaban(egyenes, cella_geom, P_pont)` | Egy egyenes cellán belüli darabja a P ponthoz legközelebb (a sávból való kilépésig), mindkét végén túlnyúlással. None, ha az egyenes nem metszi a cellát. |
| 131 | `cella_atlo(cella_geom)` | A cella befoglaló téglalapjának átlója + 1 m: a vágóvonal-sugarak és a 8.4 bináris keresés felső határa. *(kézi leírás)* |
| 136 | `darabok_kategoriai(darabok, cimek_cella)` | Minden darabhoz a benne lévő (számító) címek kategória-halmaza. |
| 150 | `ellenorzes(darabok, cimek_cella)` | ELLENŐRZÉS: minden darabban legfeljebb egy kategória? |
| 155 | `t_csomopontok_utcan(U, index)` | Az U vonalon ülő T-csomópontok (ahol másik vonal végződik vagy keresztez: ≥3 él) t értékei. |
| 169 | **class** `Gyujto` | A 8. lépés debug-geometriáinak és statisztikáinak gyűjtője. |
| 172 | `Gyujto.__init__(self)` | A 8. lépés debug-listái (vetítések, sorok, T-/köz-/közös/párhuzamos vágások, Voronoi-sejtek) üres listákkal. *(kézi leírás)* |
| 189 | `_koz_vagas_t(koz, t_jeloltek)` | Egy köz (nyílt intervallum) vágási helye: a felezőponthoz legközelebbi T-jelölt, ha a közbe esik; különben a köz közepe. Visszatérés: (t_vágás, 'T' \| 'közép'). |
| 200 | `_egy_koz_vagovonala(sav_geom, U, koz, t_jeloltek, cimek_cella, elotte_idx, utana_idx, gy)` | Egy futam-határ vágóvonala: először a T-jelölt / köz közepe, ha a vágás nem választja el tisztán a két oldalt (görbe utca), a köz negyedelő pontjai. Visszatérés: (vágóvonal, típus) vagy None. |
| 232 | `vagas_8_1(sav_geom, U, cimek_cella, t_jeloltek, gy)` | 8.1: t ← U.project(cím); sor ← t szerint rendezve. HA TISZTA: minden szomszédos futam-pár közé merőleges vágóvonal. Visszatérés: (darabok, vágóvonalak, típusok) vagy None (nem tiszta, vagy a vágás nem tudta elválasztani a futamokat → 8.3 / 8.4). |
| 269 | `_futam_koz(U, cimek_cella)` | Egy sáv címeinek t-sora; HA tiszta és pontosan két futam → (köz, {K1, K2}, sorrend) különben None. |
| 280 | `_meroleges_a_vazvonalon(U, t, vazvonal, atlo)` | Az U t-jelénél húzott merőleges hol metszi a vázvonalat: a vázvonal menti helye (project) vagy None. |
| 291 | `vagas_8_2(sav1, U1, cimek1, sav2, U2, cimek2, vazvonal, t_jeloltek1, t_jeloltek2, gy)` | 8.2: HA a szemközti sáv is vegyes, és ugyanaz a két kategória között van futam-határa, és a két köz vázvonalra vetített intervalluma (I1, I2) átfed: közös vonal a vázvonal V pontjából a két utcára bocsátott merőlegesekkel (párhuzamos utcáknál egy egyenes, különben V-ben törik). Visszatérés: ((darabok1, vágás1), (darabok2, vágás2), v, törésszög) vagy None (→ lépcső, 8.1). |
| 337 | `vagas_8_3(sav_geom, U, cimek_cella, gy)` | 8.3: d ← U.distance(cím); sor ← d szerint rendezve; HA TISZTA: a futamok d-közének közepén az utcával párhuzamos vágóvonal (U.offset_curve, a sávra vágva). Visszatérés: (darabok, vágóvonalak, d-k) vagy None. |
| 373 | `_hatart_erinto_vegek_hosszabbitasa(vonal, hatar, hossz)` | A vágóvonal azon végeit hosszabbítja meg `hossz`-szal, amelyek a cellahatáron ülnek. |
| 388 | `voronoi_vagas(cella_geom, cimek_cella, gy)` | VORONOI-VÁGÁS (cella, címek): 1. minden címpont egy mag; Voronoi, a cellára vágva; minden darab a magja kategóriáját kapja 2. azonos kategóriájú darabok egyesítve → nyers al-cellák 3. a különböző kategóriájú al-cellák közös határvonalai → nyers vágóháló 4. KIEGYENESÍTÉS: a legnagyobb t tűrés bináris kereséssel (18 lépés, 0 és a cella átlója között), amelynél a Douglas–Peucker-rel egyszerűsített vágóvonalakkal vágott cella minden darabja egykategóriás (a cellahatárt érintő végek 1 m-rel meghosszabbítva) 5. a legnagyobb érvényes t-vel vágás; cím nélküli darab a legközelebbi cím kategóriáját kapja; azonos kategóriájú darabok egyesítve. Visszatérés: [(darab, szkid)] vagy None, ha a Voronoi nem tud tiszta felosztást adni. |
| 477 | `_elo_es_utofutam(futam_lista)` | A sor elejének / végének leghosszabb tiszta futama: olyan futam, amelynek kategóriája máshol nem fordul elő. Visszatérés: (előfutam vagy None, utófutam vagy None). |
| 488 | `vagas_8_4_sav(sav_geom, U, cimek_cella, t_jeloltek, gy)` | 8.4 SÁVNÁL: az elő- és utófutamot merőleges vágóvonallal levágjuk (8.1 szerint), a középső, keveredő darabra Voronoi-vágás. Visszatérés: [(darab, szkid vagy None)], vágóvonalak. |
| 535 | `_szemkozti_sav(eltabla, cella_id, cellak_sor)` | A sáv szemközti sávja: ugyanaz a tömb, közös vázvonal (rang 1 él). Visszatérés: (szemközti cella_id, a közös vázvonal LineString) vagy (None, None). |
| 559 | `_tobbsegi(cimek_cella)` | Többségi kategória (döntetlennél a kisebb szkid). |
| 565 | `lepes_8_vegyes_felosztas(cellak, eltabla, cimek, vegyes_idk, utca_vonalak, debug)` | 8. lépés – MINDEN vegyes cellára a megfelelő felosztás; 8.5: minden darab a benne lévő címek kategóriáját kapja, cím nélküli darab → ÜRES; azonos kategóriájú darabok egyesülnek; R. rutin. Visszatérés: (cellak, eltabla, cimek, vágástípus-statisztika Counter). |

### `szburkolas/v3_besorolas.py` (342 sor)

> v3_besorolas – 9. lépés (üres cellák besorolása) és 10. lépés (lezárás).

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 28 | `lepes_9_1_folytonossag(cellak, utca_vonalak, debug, lepes)` | MINDEN üres sávRA (utca U): jelöltek ← a U-hoz tartozó, címes besorolású sávok UGYANAZON AZ OLDALON (a reprezentatív pontjaikat összekötő szakasz nem keresztezi U-t); a két irányban a legközelebbi jelölt: egyező kategória vagy csak egy → az; eltérő → a közelebbi, és a sáv 'eltérő-folytonosság' JELZÉST kap. Egy menetben, csak a címes sávokból örökölve. |
| 88 | `lepes_9_2_rang(cellak, eltabla, debug, lepes)` | ISMÉTELD, AMÍG változik: MINDEN üres cellára a besorolt szomszédok közül a legkisebb rangú közös élen át (egyenlő rangnál a leghosszabbon) örököl; ugyanabban a körben a többi üres már látja. Vázvonalon (rang 1) könnyen, vasúton (rang 6) csak ha más nincs, lakott határon soha. |
| 125 | `lepes_9_3_legkozelebbi(cellak, debug, lepes)` | A még üres cellák (nincs közös élük semmivel, pl. utca nélküli folt) a legközelebbi besorolt cella kategóriáját kapják; HA nincs ilyen → üres marad, jelzéssel. |
| 150 | `lepes_9_ures_besorolas(cellak, eltabla, cimek, utca_vonalak, debug, lepes_elotag, folytonossag)` | 9. lépés – a három szabály sorban, majd R. rutin. `lepes_elotag` a debug rétegek nevéhez (a 10.1 utáni újrafutásnál '10_1_9'). `folytonossag=False`: a 9.1 kimarad – a 10.1 utáni újrafutásnál, mert az utca menti folytonosság ugyanazt a (nem szomszédos sávtól örökölt) szigetet hozná létre újra; a rang-szabály és a legközelebbi cella csak szomszédtól örököl, így a sziget a környezetébe olvad. |
| 169 | `lepes_10_1_szigetek(cellak, cimek, debug)` | MINDEN kategóriáRA: cellái egyesítve, összefüggő darabokra bontva; a legfeljebb 1 címet tartalmazó darab gyanús (BIZTONSÁG: ha minden darab gyanús, a legnagyobb marad). A gyanús darabok cellái → ÜRES, a bennük lévő cím 'hibás-sziget' jelzést kap (a besorolásban és a validációban nem számít). Visszatérés: (cellak, cimek, gyanús cella_id-k). |
| 214 | `lepes_10_2_egyesites(cellak, crs)` | MINDEN kategóriáRA: az összes cellája uniója → a körzet poligonja (MultiPolygon megengedett). A besorolatlan cellák (szkid None) külön sorba kerülnek 'besorolatlan' jelzéssel, hogy a lefedés teljes maradjon. A körzet jelzése a cellái jelzéseinek uniója. |
| 236 | `lepes_10_3_validacio(korzetek, referencia_terulet, cimek, debug)` | VALIDÁCIÓ – mindnek teljesülnie kell: • nincs átfedés (> 0,001 m²) • a lefedett terület megegyezik a 2. lépés celláinak uniójával (eltérés ≤ max(5 m², 0,01 %)) • nincs lógó vonal • egyetlen (számító) cím sincs a saját kategóriájától eltérő körzetben • annyi körzet jött létre, ahány kategória (szkid) a bemeneti címekben van: minden kategóriának pontosan egy körzet-poligonja (MultiPolygon) van, és nincs kategória nélküli körzet Visszatérés: (rendben, hibaüzenetek listája). |
| 309 | `szerkezeti_mutatok(cellak_7, cimek_7, vagas_tipusok, korzetek, n_rang3_torolt, n_narancs, qa_cimek_kivul, qa_foutak_kivul, lakott_gyanus, n_azonos_pont, n_hibas_sziget, validacio_ok)` | A 12. fejezet mutatói településenként egy dict-ben (a napló CSV egy sora). |

### `szburkolas/burkolas_v3.py` (221 sor)

> burkolas_v3 – a B. burkoló egység V3 folyamata egy településre.

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 37 | **class** `BurkolasEredmeny` | Egy település burkolásának eredménye (helyi UTM-vetületben). |
| 48 | **class** `_Ido` | Lépésidők kiírása (csak debug futásnál). |
| 51 | `_Ido.__init__(self, aktiv)` | Az időmérő indítása (t0). *(kézi leírás)* |
| 55 | `_Ido.__call__(self, cimke)` | Debug futásnál kiírja az eltelt másodperceket és a lépés címkéjét. *(kézi leírás)* |
| 60 | `_szin_oszlop(korzetek, cimek, debug_iro)` | 11. kimenet: a körzetek és a címek `szin` oszlopa a kategória (szkid) palettájából – a körzet és a hozzá tartozó címek azonos színűek, QGIS-ben adatvezérelt kitöltéssel egyből olvasható. |
| 70 | `burkol_telepules(varos, cimek_wgs, debug, debug_dir)` | A B egység teljes folyamata egy településre (Budapesten kerületre). |

### `szburkolas/burkolas_multiple.py` (212 sor)

> burkolas_multiple – a V3 burkolás kötegelt, többmagos futtatása minden településre.

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 40 | `_setup_logging(log_file)` | Időbélyeges logger: a FÁJL mindent kap (INFO+), a KONZOL csak a rendellenességeket (WARNING+). |
| 58 | `_ensure_cols(gdf_in, cols)` | A GDF-et a megadott fix sémára igazítja (hozzáfűzés-konzisztencia). |
| 67 | `append_layer(gdf_in, path, layer, cols)` | A sorokat a gyűjtő GPKG adott rétegéhez fűzi (új fájlnál létrehozza). |
| 78 | `_kesz_telepulesek(out_gpkg)` | A gyűjtő GPKG-ben MÁR meglévő településnevek (a folytatás alapja). |
| 91 | `_naplo_sor(naplo_csv, varos, status, naplo)` | Egy település mutatói a napló CSV-be (fejléc az első sornál). |
| 107 | `_worker(task)` | Egy település teljes burkolása (worker-oldal): számolás + reprojekció EOV-ba. Fájlba NEM ír. task = (varos, cimek_sub); visszatérés: (varos, status, korzetek, cimek, naplo, log_text). |
| 132 | `burkolas_multiple(cimek_gpkg, out_gpkg, varosok, n_workers)` | Végigmegy a településeken TÖBB MAGON, a gyűjtő GPKG-t és a napló CSV-t a fő processz írja. |

### `szburkolas/teszt_v3_szintetikus.py` (159 sor)

> teszt_v3_szintetikus – a V3 burkolás lépésenkénti ellenőrzése egy mesterséges mini-településen.

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 36 | `szintetikus_bemenet()` | A mesterséges mini-település felépítése (lyukas lakott folt + külön folt, közig. határ x=300-nál, 2×2 tömbös utcaháló zsákutcával, két kategória címei). *(kézi leírás)* |
| 69 | `kozel(a, b, tures)` | Lebegőpontos összehasonlítás tűréssel (alapértelmezés 0,5) az assert-ekhez. *(kézi leírás)* |
| 73 | `teszt()` | A szintetikus teszt: a lépéseket sorban futtatja, mindegyik után assert (lásd 19.1). *(kézi leírás)* |

### `adatstruktua/osm_letoltes.py` (570 sor)

> Letölti minden városra (data/nyers_data/varosok.txt) az OSM adatokat WGS84-ben: - varos_kozigazgatasi_hatar.parquet (hivatalos közig. határ, geocode_to_gdf) - varos_lakottterulet_hatar.parquet (lakott terület, landuse=residential) - utcak_nodes.parquet - utcak_edges.parquet V3 burkoláshoz (osm_letoltes_v3, külön parquet-ek, a fentiek érintetlenek): - utcak_all_edges.parquet (network_type='all': minden járható út, gyalogút is) - vasut_folyo.parquet (railway=rail, waterway=river/stream/canal vonalak)

| Sor | Függvény / osztály | Mit csinál (docstring) |
|---:|---|---|
| 71 | `_nan(v)` | Igaz, ha az érték None vagy NaN. *(kézi leírás)* |
| 75 | `to_json_strings(df)` | A vegyes (skalár/lista) objektum-oszlopokat JSON-stringgé alakítja, hogy a pyarrow parquetbe írhassa; a NaN változatlan marad. A beolvasó (`v3_io`) visszafejti. *(kézi leírás – kódkomment alapján)* |
| 93 | `_stringesit_kevert_oszlopok(df)` | Merge-drift javítás: ha egy object oszlopban string (régi, JSON-osított) ÉS nyers skalár (friss letöltés: bool/int/float) is van, a nem-string értékeket is JSON-stringgé alakítjuk — container híján a to_json_strings nem triggerel, a pyarrow írás viszont elhasalna a kevert oszlopon. A polygon_io._restore_mixed_object_cols mindkét alakot visszafejti (json.loads("false") → False, "123" → 123). |
| 120 | `_parquet_merge(path, uj, csere_telepulesek, append)` | Az új sorokat a meglévő parquet-ba fésüli: a csere_telepulesek régi sorait eldobja (idempotens újrafuttatás), a többit érintetlenül hagyja. append=False → sima felülírás. |
| 135 | `_mentes(hatarok, lakott, nodes, edges, csere_telepulesek, append)` | A négy adatréteg kiírása/merge-elése. Üres réteget nem ír (append-nél nincs mit cserélni, felülírásnál pedig nem nullázzuk ki a fájlt egy hibás kör miatt). |
| 154 | `_hibak_mentes(hibak, csere_telepulesek, append)` | Hibalog írása; append-nél a régi sorok megmaradnak, kivéve az újratöltött települések sorait (azok cserélődnek az aktuális eredményre). |
| 164 | `hianyzo_varosok()` | varosok.txt mínusz a határ-parquetben már meglévő telepules-értékek (sorrendtartó). |
| 173 | `osm_letoltes(varosok, append)` | varosok=None → teljes varosok.txt és felülírás (régi viselkedés, 0_pipeline-kompatibilis); megadott lista → csak azok, alapból append-merge. A Budapest-neveket ("Budapest" és "... kerület") a geokódos ág kihagyja — ha szerepeltek, a végén a Budapest-ág fut le. |
| 241 | `budapest_keruletek_hatarok()` | A 23 kerület közigazgatási határa admin_level=9 relációkból, EGY egész-Budapest lekérdezéssel. Kimenet: GeoDataFrame [kerulet (1..23), telepules (kanonikus név, varosok.txt-alak), geometry], kerületszám szerint rendezve. Assert-kapu: pontosan 23 kerület, mind értelmes területtel — ez véd a bulk-letöltés előtt. |
| 274 | `budapest_keruletek_letoltes(append)` | Budapest külön ága — a kerületnevek nem geokódolhatók, ezért egész-városi letöltés egyszer, majd kerülethatár szerinti vágás: - határ: 'Budapest' egész-városi sor + 23 kerület-sor, - lakott: egész-városi residential foltok 'Budapest' címkével (az MS-szűrésnek egyben kell) ÉS kerülethatárra vágott darabok kerület-címkével (ezt olvassa a burkolás), - utcák: egész-városi drive-gráf EGY letöltéssel, kerületenként truncate-elve. Egész-városi gráf-sorokat nem tárolunk (nincs fogyasztója, csak duplázna). A gráfot gráf-szinten vágjuk (truncate_graph_polygon), nem GDF-szinten: az olvas_varos gráf-újraépítése megköveteli, hogy minden él végponti node-ja megvan a kerület soraiban. |
| 347 | `_all_edges_gdf(G, telepules)` | Egy 'all' gráf éleit a V3-nak kellő oszlopokra szűkíti és telepules-címkével látja el. Hiányzó oszlop (pl. 'service' egy kis faluban) None-nal pótolva, hogy a parquet sémája településenként azonos legyen. |
| 360 | `_vasut_folyo_sorok(feat, telepules, vago_poligon)` | A features_from_place eredményéből a vasút/folyó VONALAKAT szedi ki soronként: [{'tipus': 'vasút'\|'folyó', 'geometry', 'telepules'}]. Poligon-geometriát (pl. folyó- terület) eldob. vago_poligon megadásával (Budapest-ág) a vonalakat a kerülethatárra vágja és a MultiLineString-eket darabolja. |
| 384 | `_mentes_v3(edges_all, vasut_folyo, csere_telepulesek, append)` | A két V3-réteg kiírása/merge-elése (ugyanazzal a _parquet_merge logikával). |
| 395 | `_ujraprobalva(hivas, probak, varakozas)` | Az Overpass nyilvános szervere túlterhelésnél 502-t ad: ilyenkor exponenciális várakozással újrapróbáljuk (30, 60, 120 s). Más hibát (pl. Nominatim nem talál) azonnal továbbadunk. |
| 411 | `osm_letoltes_v3_adagokban(varosok, adag)` | Országos futáshoz: a településeket `adag` méretű csoportokban tölti és MENTI (append-merge), így egy megszakadás legfeljebb egy adagnyi munkát visz el. A Budapest-neveket kihagyja, ha a 23 kerület már a cache-ben van. |
| 426 | `hianyzo_varosok_v3()` | varosok.txt mínusz az utcak_all_edges.parquet-ben már meglévők, mínusz azok, amelyeknél a hibafájl 'utcahálózat_all' hibát rögzített (hálózat nélküli aprófalvak – ezeket nem próbáljuk újra minden futásnál). Sorrendtartó. |
| 443 | `osm_letoltes_v3(varosok, append)` | V3 bemenetek letöltése: 'all' utcahálózat élei + vasút/folyó vonalak, településenként. varosok=None → teljes varosok.txt és felülírás; megadott lista → csak azok, append-merge. A Budapest-neveket a geokódos ág kihagyja, a végén a Budapest-ág fut le rájuk. Ha egy településen nincs vasút/folyó, az NEM hiba (üres réteg normális). |
| 492 | `budapest_vasut_folyo(ker, hiba)` | Vasút + folyó egész Budapestre egy lekérdezéssel, kerülethatárra vágva. Külön függvény, hogy Overpass-hiba (502) után a gráf újratöltése nélkül megismételhető legyen. |
| 510 | `budapest_vasut_folyo_potlas()` | Csak a budapesti vasút/folyó réteg (újra)letöltése és merge-elése a parquet-be. |
| 528 | `budapest_keruletek_letoltes_v3(append)` | Budapest V3-ága: egész-városi 'all' gráf EGY letöltéssel, kerületenként truncate-elve; vasút/folyó egész városra lekérdezve, kerülethatárra vágva. Kerület-címkés sorok kerülnek a parquet-ekbe (egész-városi 'Budapest' sor nincs: a burkolás kerületenként dolgozik). |

### Belső (beágyazott) segédfüggvények

Ezek egy másik függvény belsejében vannak definiálva (az AST-lista csak a felső szintet mutatja):

| Hely | Függvény | Mit csinál |
|---|---|---|
| `v3_io._json_visszafejtes` (62. sor) | `egy_ertek(v)` | Egy parquet-cella JSON-visszafejtése (`json.loads`); nem-string vagy hibás JSON változatlanul marad. |
| `v3_savok.tomb_savositas` (121. sor) | `savok_polygonize(vaz)` | `polygonize(tömb határa ∪ vázvonalak)` RÁCS-on; a tömbön belüli lapok a nyers sávhoz (csoporthoz) rendelve aszerint, melyikben van a lap reprezentatív pontja. |
| `v3_savok.lepes_4_savositas` (232. sor) | `csoport_szin(cs)` | Debug: egy határpont-csoport (utca) színe az utca-palettából, ismeretlen csoportnál a „határpont” szín. |
| `v3_felosztas.lepes_8_vegyes_felosztas` (576. sor) | `t_jeloltek_u(utca_id, idx)` | Egy U-vonal (utca, komponens) T-jelölt csomópontjai (`t_csomopontok_utcan`), cache-elve, hogy minden U-ra csak egyszer számolódjon. |
| `v3_felosztas.lepes_8_vegyes_felosztas` (582. sor) | `U_vonal(sor)` | Egy sáv-cella U-vonala (`utca_vonalak[utca_id][utca_vonal_idx]`); nem-sáv cellánál vagy hiányzó utcánál None. |


## B. melléklet – minden modulszintű konstans értékkel

Gépi úton generálva: a modulok felső szintű, NAGYBETŰS nevű értékadásai, a forrásbeli kifejezéssel.


### `szburkolas/v3_param.py`

| Sor | Név | Érték |
|---:|---|---|
| 17 | `TELEKSZELESSEG` | `6.0` |
| 21 | `SAROKMERET` | `30.0` |
| 25 | `VAZFELBONTAS` | `2.0` |
| 28 | `RACS` | `0.01` |
| 36 | `NARANCS_SAJAT_TAVOLSAG` | `0.25` |
| 40 | `VAGOVONAL_TULNYULAS` | `1.0` |
| 43 | `VORONOI_KERESES_LEPES` | `18` |
| 46 | `ERINTO_FELHOSSZ` | `0.5` |
| 49 | `ATFEDES_TURES_M2` | `0.001` |
| 50 | `LEFEDES_TURES_ABS_M2` | `5.0` |
| 51 | `LEFEDES_TURES_REL` | `0.0001` |
| 54 | `QA_CIMEK_KIVUL_KUSZOB` | `0.10` |
| 55 | `QA_FOUTAK_KIVUL_KUSZOB` | `0.30` |
| 61 | `TIPUS_LAKOTT_HATAR` | `'lakott határ'` |
| 62 | `TIPUS_UTCA` | `'utca'` |
| 63 | `TIPUS_VASUT` | `'vasút'` |
| 64 | `TIPUS_FOLYO` | `'folyó'` |
| 65 | `TIPUS_SEGEDVONAL` | `'segédvonal'` |
| 66 | `TIPUS_VAZVONAL` | `'vázvonal'` |
| 67 | `TIPUS_VAGOVONAL` | `'vágóvonal'` |
| 69 | `RANG_VAZVONAL` | `1` |
| 70 | `RANG_SEGEDVONAL` | `2` |
| 71 | `RANG_GYALOGUT` | `3` |
| 72 | `RANG_LAKOUTCA` | `4` |
| 73 | `RANG_FOUT` | `5` |
| 74 | `RANG_VASUT_FOLYO` | `6` |
| 75 | `RANG_LAKOTT_HATAR` | `7` |
| 78 | `HIGHWAY_RANG` | `{ # rang 3 'footway': RANG_GYALOGUT, 'path': RANG_GYALOGUT, 'pedestrian': RANG_GYALOGUT, 'steps': RANG_GYALOGUT, 'service': RANG_GYALOGUT, 'cycleway': RANG_G…` |
| 95 | `HIGHWAY_RANG_ROAD` | `RANG_LAKOUTCA` |
| 96 | `RANG_ISMERETLEN_HIGHWAY` | `RANG_GYALOGUT` |
| 102 | `CELLA_TOMB` | `'tömb'` |
| 103 | `CELLA_FOLT` | `'folt'` |
| 104 | `CELLA_SAV` | `'sáv'` |
| 105 | `CELLA_SAROK` | `'sarok'` |
| 107 | `KULVILAG` | `-1` |
| 109 | `SZABALY_CIMES` | `'címes'` |
| 110 | `SZABALY_FOLYTONOSSAG` | `'folytonosság'` |
| 111 | `SZABALY_RANG` | `'rang'` |
| 112 | `SZABALY_LEGKOZELEBBI` | `'legközelebbi'` |
| 115 | `ALLAPOT_EGYKATEGORIAS` | `'egykategóriás'` |
| 116 | `ALLAPOT_VEGYES` | `'vegyes'` |
| 117 | `ALLAPOT_URES` | `'üres'` |
| 120 | `VAGAS_T` | `'merőleges-T'` |
| 121 | `VAGAS_KOZEP` | `'merőleges-közép'` |
| 122 | `VAGAS_KOZOS` | `'közös-vonal'` |
| 123 | `VAGAS_PARHUZAMOS` | `'párhuzamos'` |
| 124 | `VAGAS_VORONOI` | `'Voronoi'` |
| 127 | `JELZES_FOLYTONOSSAG` | `'eltérő-folytonosság'` |
| 128 | `JELZES_HAROMSZOG` | `'egycímes-háromszög'` |
| 129 | `JELZES_VALIDACIO` | `'validáció-hiba'` |
| 130 | `JELZES_AZONOS_PONT` | `'azonos-pont'` |
| 131 | `JELZES_HIBAS_SZIGET` | `'hibás-sziget'` |
| 132 | `JELZES_BESOROLATLAN` | `'besorolatlan'` |
| 133 | `JELZES_LAKOTT_GYANUS` | `'lakott terület gyanús'` |
| 139 | `SZIN` | `{ 'lakott határ': '#000000', 'közigazgatási határ': '#555555', 'rang 3': '#AAAAAA', 'rang 4': '#1F77B4', 'rang 5': '#08306B', 'vasút': '#8C564B', 'folyó': '#…` |

### `szburkolas/v3_io.py`

| Sor | Név | Érték |
|---:|---|---|
| 32 | `ROOT` | `Path(__file__).resolve().parent.parent` |
| 33 | `DATA_DIR` | `ROOT / 'data' / 'nyers_data'` |
| 34 | `LAKOTT_PARQUET` | `DATA_DIR / 'varos_lakottterulet_hatar.parquet'` |
| 35 | `KOZIG_PARQUET` | `DATA_DIR / 'varos_kozigazgatasi_hatar.parquet'` |
| 36 | `UTCAK_ALL_PARQUET` | `DATA_DIR / 'utcak_all_edges.parquet'` |
| 37 | `VASUT_FOLYO_PARQUET` | `DATA_DIR / 'vasut_folyo.parquet'` |
| 38 | `VAROSOK_TXT` | `DATA_DIR / 'varosok.txt'` |
| 39 | `ADATOK_DIR` | `ROOT / 'szburkolas' / 'adatok'` |
| 338 | `POLIGON_OSZLOPOK` | `['szkid', 'telepules', 'jelzes', 'szin', 'geometry']` |
| 339 | `CIM_OSZLOPOK` | `['szkid', 'utca', 'hazszam', 'telepules', 'irsz', 'szin', 'geometry']` |
| 340 | `TARGET_CRS` | `'EPSG:23700'` |

### `szburkolas/v3_topologia.py`

| Sor | Név | Érték |
|---:|---|---|
| 24 | `ELTABLA_OSZLOPOK` | `['el_id', 'tipus', 'rang', 'utca_id', 'cella_bal', 'cella_jobb', 'hossz', 'geometry']` |
| 26 | `CELLA_OSZLOPOK` | `['cella_id', 'cellatipus', 'tomb_id', 'utca_id', 'utca_vonal_idx', 'szkid', 'szabaly', 'jelzes', 'sliver', 'geometry']` |
| 297 | `KIHAGYOTT_CIM_JELZESEK` | `(P.JELZES_HIBAS_SZIGET, P.JELZES_AZONOS_PONT)` |
| 441 | `KOLLINEARIS_SZOG_FOK` | `5.0` |

### `szburkolas/burkolas_multiple.py`

| Sor | Név | Érték |
|---:|---|---|
| 37 | `MAX_TASKS_PER_CHILD` | `25` |

### `szburkolas/teszt_v3_szintetikus.py`

| Sor | Név | Érték |
|---:|---|---|
| 33 | `CRS` | `'EPSG:32634'` |

### `adatstruktua/osm_letoltes.py`

| Sor | Név | Érték |
|---:|---|---|
| 47 | `DATA` | `Path(__file__).resolve().parent.parent / 'data' / 'nyers_data'` |
| 49 | `HATAR_PARQUET` | `DATA / 'varos_kozigazgatasi_hatar.parquet'` |
| 50 | `LAKOTT_PARQUET` | `DATA / 'varos_lakottterulet_hatar.parquet'` |
| 51 | `NODES_PARQUET` | `DATA / 'utcak_nodes.parquet'` |
| 52 | `EDGES_PARQUET` | `DATA / 'utcak_edges.parquet'` |
| 53 | `EDGES_ALL_PARQUET` | `DATA / 'utcak_all_edges.parquet'` |
| 54 | `VASUT_FOLYO_PARQUET` | `DATA / 'vasut_folyo.parquet'` |
| 55 | `HIBAK_TXT` | `DATA / 'osm_letoltes_hibak.txt'` |
| 58 | `HIBA_RETEG_ALL` | `'utcahálózat_all'` |
| 59 | `HIBA_RETEG_VASUT` | `'vasut_folyo'` |
| 62 | `ALL_EDGE_OSZLOPOK` | `['u', 'v', 'key', 'osmid', 'highway', 'name', 'service', 'geometry']` |
| 63 | `VASUT_FOLYO_TAGS` | `{'railway': 'rail', 'waterway': ['river', 'stream', 'canal']}` |
| 68 | `BUDAPEST_QUERY` | `{'city': 'Budapest', 'country': 'Hungary'}` |
