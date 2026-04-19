# Szavazóköri poligonok létrehozása

**Poligonok létrehozása lakott területet fedve az utcahálózat alapján a választási címjegyzék kordinátái alapján**

A projtekt célja, hogy létrehozzon szavazóköri poligonokot amik
1. teljesen és koherensen (nem egymásba lógva, a lakott területet teljesen fedve) lefedik az OSM-től lekérdezett lakotterületi határokat
2. a nyilvános választási címjegyzék alapján dologzunk, lerakjuk a címjegyzékben szereplő címeket a térképre és ez alapján próbáljuk meghatározni a szavazókör határait. 

*A poligonoknak tökéltesen illeszkedniük kell, tökéletesen fedniük kell a lakott területi részt és soha nem lehetnek fedésben egymással a szavazóköri poligonok!*

Logikai alapok: 
1. a lakott terület feloszjuk az utcahálózat alapján (plusz segédvonalk amik összekötik az utcahálózatot a lakotterületi határral) kisebb részegységekre
2. a kisebb részegységekbe vetítjük a szavzókörök szerint besorolt címeket kordinátájukkal
3. ha egy kisebb egységben egyetlen szavazóköri címek vannak akkor ezt az egységet oda soroljuk
4. ha egy kisebb egységben több szavazókörhüz tartaozó címek vannak akkor tovább daraboljuk az egységet
5. a sehová nem tartozó egységeket hozzáreneljük az egyik szomszédos szavazóköri egységhez
6. az azonos szavazókörökhüz taratozó egységeeket összevonjuk egyetlen nagy poligonná


## Bementei adatok
Lokális bemenetek: 
1. címjegyzék tábla: címjegyzékben szereplő házszámmal ellátott címek, a hozzájuk tartozó kordináta és a besololásuk hogy melyik szavazókörbe tartozik a cím (szavazókör ID)


Címjegyzék tábla:
| id | szavazokorid | utca | hazszam | telepules | geometry |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1739892 | 1600517 | Petőfi Sándor utca | 1 | Ibafa | POINT (17.91557 46.15492) |
| 1739893 | 1600517 | Petőfi Sándor utca | 10 | Ibafa | POINT (17.91403 46.15501) |
| 1739894 | 1600517 | Petőfi Sándor utca | 11 | Ibafa | POINT (17.91308 46.15513) |
| 1739895 | 1600517 | Petőfi Sándor utca | 12 | Ibafa | POINT (17.91349 46.15506) |
| 1739897 | 1600517 | Petőfi Sándor utca | 14 | Ibafa | POINT (17.91289 46.15508) |

Lekérdezett bemenetek: 
1. OSM-től (osmnx csomag) lekérdezett lakotterületi határ a városra (ha az adott várhoz jelzi a program)
2. OSM-től (osmnx csomag) lekérzdett utcahálózat a városra (ha az adott várhoz jelzi a program)


## Program logikai működése V2

### 1. Adatletöltés (`letoltes`)
- OSM-ről letöltjük metrikus vetületbe (EOV) a:
  - Úthálózatot (gráf + nodes/edges GeoDataFrame)
  - `landuse=residential` poligonokat (lakott területek)
  - Hivatalos városhatárt (`geocode_to_gdf`)

### 2. Terület-előkészítés
- `vag_residential_city`: minden lakóterületi poligont levágja a városhatárra (metszés)
- `res_area_es_boundary`: megtartja csak azokat a lakóterületeket, ahol az úthálózat ténylegesen fut (metszet hossza > 0) → eredmény: `res_area` (unió poligon), `boundary` (határ vonal)

### 3. Összekötő vonalak generálása
- `orange_gen` – NARANCS vonalak (zsákutcák lezárása):
  - Megkeressük az összes zsákutca-végpontot (irányítatlan gráfban csomópontfok = 1)
  - Minden végpontból MAX_EXT métert sugárzunk az utca folytatásában kifelé
  - A sugár első metszéspontján (más utcával) megállunk → connector keletkezett

- `blue_gen` – KÉK vonalak (határ-közelség):
  - Minden csomóponttól, ami DIST_LIM-en belül van a lakóterület határától
  - Merőleges vonalat húzunk a legközelebbi határpontig → connector keletkezett

### 4. Hálózat összerakása (`kapcsolas`)
a. Összegyűjtjük az összes réteget: utcák + narancs + kék vonalak → levágjuk `res_area`-ra
b. SNAP: a vonalvégpontokat ráhúzzuk a lakóterületi határra (SNAP_TOL tolerancián belül)
c. STRIP & JOIN (dupla-fal kezelés):
   - A határral párhuzamosan futó utcák egy "dupla falat" képeznének (vékony sliver sávban)
   - A STRIP_TOL sávban lévő vonalvégpontokból explicit összekötőket húzunk a határra
   - Majd a teljes STRIP_TOL sávot kivágjuk a vonalhálóból → dupla fal eltűnik
d. Végső háló = clipped utcák + összekötők + boundary vonal
e. `unary_union` + `linemerge` → topológiailag egységes, zárt vonalháló

### 5. Poligonizálás + kis poligonok beolvasztása (`egyesites`)
a. `polygonize()`: a zárt vonalhurkok kitöltése → cellapoligonok keletkeznek
b. Kis poligonok (< MIN_AREA m²) iteratív beolvasztása:
   - Mindig a legkisebb poligont keressük
   - Azt a szomszédját keressük, amellyel a leghosszabb a közös határ
   - Beolvasztjuk → ismételjük amíg minden poligon ≥ MIN_AREA

### 6. Szavazókörökhöz rendelés
a. Betöltjük a választási címjegyzéket (geocódolt pontok szavazókör ID-val)
b. Szín hozzárendelés szavazókörönként: golden ratio hue elosztással → vizuálisan jól elkülönülő paletta
c. `pontok_polygonban` – minden cellához pont-szavazókör megfeleltetés:
   - 0 pont a cellában → `szavazokorid = None`
   - Pontosan 1 szavazókör → hozzárendeljük
   - Több szavazókör → `polygon_tobb_szavazokor` (rekurzív szétbontás)
d. `polygon_tobb_szavazokor` – vegyes cellák szétbontása (stack-alapú DFS):
   - A cellát felezzük a centroid mentén, a hosszabb tengelyen
   - Minden felére újraszűrjük a pontokat
   - Ha egy fél egységes (1 szavazókör) → kész; ha vegyes → vissza a stackbe
   - `max_depth` korlát a végtelen ciklus ellen
e. `ures_polyk_besorolasa` – üres cellák feltöltése:
   - Érintkező szomszédok szavazóköreit megszámoljuk → a legtöbbször előforduló = nyertes
f. `polygonok_egyesitese` – szavazókörönkénti összevonás:
   - unary_union szavazókörönként
   - Ha MultiPolygon maradt: morfológiai zárás (buffer(+tol) → buffer(-tol)), növekvő tol
   - Ismétlés amíg egyetlen Polygon nem lesz, vagy max_tol el nem ér

### 7. Export
- QGIS-ba exportálás `.gpkg` formátumban (szavazókör határok + geocódolt címpontok)


**A célunk hogy a V2 működésmódot fejlesszük és egy jobb v3-at hozzunk létre de úgy hogy kövessük a leírt elveket és célokat**

## Program logikai működése V3

### 1. Vonalhálózat kialakítása (`burkolas_v3.py :: poly_gen_pipeline`)

#### 1.1. Terület-letöltés
- `letoltes`: OSM-ről letölti metrikus vetületbe az úthálózatot (gráf+edges), a `landuse=residential` poligonokat és a hivatalos városhatárt.
- `vag_residential_city`: minden residential poligont a városhatárra vág.
- `res_area_es_boundary`: megtartja csak azokat a residential foltokat, ahol az úthálózat ténylegesen fut → `res_area` (unió), `boundary` (vonal).

#### 1.2. Segédvonalak (három réteg)
- `orange_gen` — **narancs**: minden zsákutca-végpontból (fokszám=1) MAX_EXT (200 m) hosszú sugarat lő az utca folytatásában, és az első MÁS utcával való metszésig megy.
- `blue_gen` — **kék**: a határtól DIST_LIM (100 m) távolságon belüli csomópontokból induló utcákat a határra hosszabbítja (DIST_LIM × RAY_MULT hosszan sugároz, első határ-metszéspontnál áll meg).
- `red_gen` — **vörös**: a lakott területi határ MINDEN csúcsában a szomszédos él folytatását a polygon BELSEJE felé sugárral vizsgálja (szög ≤ SHARP_MAX_ANGLE_DEG, probe contains); megáll az első utca/orange/blue/határ metszésnél. A kinyúló városrészek alapját vágja el, így nem képződnek elnyúló tüske-poligonok.

#### 1.3. Vonalháló tisztítása (`kapcsolas`)
- **Alapszabály**: a határvonal és az utcahálózat együtt **FROZEN** — egyetlen pontjuk sem mozdul el a tisztítás során. Csak a helper vonalak (red/blue/orange) snapelődnek hozzájuk és egymáshoz a `red > blue > orange` prioritás szerint. Az utcahálózat második a rangsorban a határ mögött, de éppúgy sérthetetlen; a határ-párhuzamos szűrés után a megmaradt utcák már tisztán egyediek (duplikáltság csak a helper vonalakban engedélyezett).
- `szur_hatarral_parhuzamos`: a határ PARALLEL_TOL (15 m) bufferén belül teljesen fekvő **utcákat** törli (dupla-fal-sliver megelőzés). Az orange/blue/red szándékos határ-híd vonalak, ezeket nem szűri. A helper vonalakat cutterként használja: ha egy parallel utcát egy red/blue/orange keresztez, az utcát a keresztezési pontnál szétvágja, és a helper-horgonyos sub-szakaszt MEGTARTJA még ha a bufferen belül van is — így a helper sosem lóg a levegőben törölt utca-részlet miatt.
- `endpoint_cluster_merge`: union-find-alapú végpont-klaszterezés ENDPOINT_TOL (10 m) távolságon belül. Tier 0 = FROZEN (határ + utcák) → egyetlen pontjuk sem mozdul. Mozgatható prioritás: `red > blue > orange` (magasabb prioritású helper pontja az anchor egymás között). Tranzitív klaszterben levő azonos-vonalbeli második végpont mozgatását átugorja (0-ra zsugorodás elleni védelem). A határ MINDEN csúcsát phantom tier-0 horgonyként hozzáadja — így ha egy blue vég a határ-szakasz belsejében landol és egy red ugyanannak a csúcsnak a közeléből indul, mindkettő a közös határ-csúcsra rögzül (nem kerül a közelbe-de-nem-pontosan helyzetbe).
- `hierarchikus_snap`: csak a mozgatható helper-tiereket snapeli `shapely.snap` MERGE_TOL (3 m) toleranciával; minden tier a FROZEN + már-snapolt helperek unióján anchorodik. A FROZEN bemenet változatlanul megy tovább. Return: `(frozen_lines, movable_snapped)`.
- `reanchor_touching_endpoints` *(új)*: a `shapely.snap` csak csúcs→csúcs snapol. Ha egy helper végpont egy másik helper szegmens-belsejében ült és annak vertexe elmozdult, a szegmens megdől → a pont lefloatol → dangle. Ez a függvény ENDPOINT_TOL (10 m) távolságon belül visszavetíti a lefloatolt **helper** végpontokat a legközelebbi másik vonalra (STRtree + `nearest_points`). A FROZEN geometriát soha nem módosítja.
- `unify_close_endpoints` *(új, safety net)*: a fenti lépések után is előfordulhat, hogy két mozgatható helper végpont ENDPOINT_TOL-on belül maradt külön koordinátán (tipikusan blue-blue, blue-orange, blue-red közeli-de-nem-azonos végek). Union-find klaszterezéssel egyetlen közös pontba olvasztja őket: ha van frozen-érintő tag (1e-6 táv), az lesz az anchor, különben a klaszter centroidja. A két-vég-egy-klaszter védelme a 0-ra zsugorodás ellen itt is érvényes.
- `v_shape_fix` *(új)*: a közös végpontból induló vonalpárok közül a rövidebbet eltávolítja, ha a hosszabb V_TOL (3 m) távolságon belül fut a rövidebb MÁSIK végpontjától ÉS az a másik vég dangling (fokszám=1) — a snap-drift után maradt V-zászlókat szünteti meg, de terhelő (mindkét végén csatlakozó) szakaszokat érintetlenül hagy.
- `set_precision 0.01`: 1 cm-es rácsra rögzíti a koordinátákat.
- **Dangle-loop**: iteratív dead-end-eltávolítás `polygonize_full` dangle-kimenete alapján, amíg nincs több (max 20 iteráció). Ezzel a `kapcsolas` tiszta, zárt hálót ad vissza — az `egyesites` csak poligont épít.

#### 1.5. Poligonizálás (`egyesites`)
- `polygonize`: a zárt vonalhurkok kitöltése.
- Kis poligonok iteratív beolvasztása (< MIN_AREA m², default 500): mindig a legkisebb cellát az azzal legnagyobb közös határú szomszédba olvasztja, amíg minden cella ≥ MIN_AREA.

### 2–6. Szavazókörökhöz rendelés (`generalas_pipeline`)
A V2 lépések változatlanok: `meret_alapu_felosztas` → `pontok_polygonban` → (`polygon_tobb_szavazokor`) → `ures_polyk_besorolasa` → `rebuild_coverage` → `polygonok_egyesitese`. Részletek a V2 szekcióban.

### 7. Export
Ugyanaz mint a V2-nél: `.gpkg` QGIS-be.

