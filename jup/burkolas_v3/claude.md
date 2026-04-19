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

