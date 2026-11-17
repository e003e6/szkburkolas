# B. Burkoló egység V3 – korrekciós feladat

Ez a dokumentum a `szburkolas/` V3 kódjának javítási feladata. A hibákat a Budapest XX. kerület
debug GPKG-jén (`Budapest_XX__kerület_debug.gpkg`) mért számok azonosították; a mérés a
mellékelt `v3_diagnosztika.py` szkripttel megismételhető.

Olvasd el előbb: `B-burkolas-v3-mukodes.md` (a leírás) és `V3_MEGVALOSITASI_NAPLO.md` (a napló).

---

## 0. Kötöttségek

Az eljárás lépései, sorrendjük és a négy paraméter nem változnak. A javítások mind egy-egy meglévő
lépés pontosításai.

- Vonal nem mozog, kész határ nem simítódik, kozmetikai utómunka nincs.
- Új paraméter nincs. Ahol távolság kell, az a meglévő `TELEKSZELESSEG`, `SAROKMERET` vagy `RACS`
  értékéből adódik.
- A lépések sorrendje (0–10) és az R. rutin változatlan.
- Az A egységhez és a kimeneti sémához nem nyúlunk.
- Determinizmus: ugyanarra a bemenetre bájtra ugyanaz a kimenet.
- A 10.3 validáció minden településen rendben marad.
- A J7 pont csak külön jóváhagyás után készül el (lásd ott).

---

## 1. A mért állapot (Budapest XX. kerület, javítás előtt)

| Mutató | Érték |
|---|---:|
| vonalháló: rang 3 névtelen / rang 3 neves / rang 4–5 | 124,5 km / 5,8 km / 151,4 km |
| különböző `utca_id` (ebből névtelen `osm_*`) | 1817 (1519) |
| tömbök a 2.1 után / sliverek a 3.1-ben | 2354 / 937 |
| tömbök, ha a névtelen rang 3 vonal nincs a hálóban (számított) | 614 (ebből 6 m-nél keskenyebb: 11) |
| sarok-csomópontok (ebből névtelen utcával) | 3974 (2306) |
| cellák: 3. után → 4. → 5. → 6. | 1450 → 6191 → 9490 → 27 738 |
| 1 m²-nél kisebb cellák a 6. után | 5523 |
| üres cellák a 7. lépésben | 24 883 / 27 738 (medián 8 m², a terület 19,9 %-a) |
| 6. lépés T-csomópontjai | 13 949 |
| … ebből csak szerkesztett vonal (váz-/vágóvonal) okozza | 12 205 (87,5 %) |
| … ebből névtelen rang 3 becsatlakozás | 1255 (9,0 %) |
| … ebből valódi keresztutca | 425 (3,0 %) |
| 9.1 öröklési távolság | medián 121 m, 90 %: 350 m, max 2417 m |
| 9.1 `eltérő-folytonosság` jelzés | 956 / 4305 |
| 9.2 öröklés utcán át (rang ≥ 4) | 615, ebből 583-nak volt rang ≤ 2 szomszédja is |
| 9.3 legközelebbi cella | 604 (medián 0,01 m²; 468-nak geometriailag van szomszédja) |
| 10.1 gyanús sziget | 252 (ebből 0 címes: 211) |
| körzethatár összetétele | 52,4 % szerkesztett vonal, 17,5 % rang 3, 30,0 % rang 4–5 |
| körzethatár töréspont / km (viszonyítás: rang ≥ 4 utcaháló) | 56 (17) |
| körzethatár 5 m-nél rövidebb egyenes szakaszai | 1786 / 4230 (42 %) |
| egycímes, egyetlen utcán átnyúló darab, tömbön belül más kategória mellett | 33 |

Az ok-okozati lánc: a névtelen rang 3 vonalak (járda, gyalogátkelő, parkösvény, parkolóút)
zárt hurkokat alkotnak, ezért az 1.4 nem törli őket → négyszer annyi tömb → mindegyik névtelen
vonal saját `utca_id`, tehát saját sáv és saját sarokcella → a 6. lépés minden szerkesztett vonal
végénél is vág → 24 883 üres, átlagosan néhány m²-es cella → a 9.1 és a 9.2 ezeket egyenként
sorolja be, a körzethatár pedig a cellák élein fut.

---

## 2. Javítások

### J1 – Névtelen rang 3 vonal nem kerül a vonalhálóba

**Hiba.** A rang 3 (footway, path, pedestrian, steps, service, cycleway, track, ismeretlen highway)
vonalak hosszának 95 %-a névtelen. Ezek mindkét végükön kapcsolódnak (a járda a gyalogátkelőn át körbeér),
ezért az 1.4 lógóvég-törlése nem hat rájuk.

**Szabály.** Rang 3 vonal csak akkor vonalháló-tag, ha van közterületneve (`name` nem üres).
A név azt jelzi, hogy a vonal önálló közterület (sétány, köz, lépcső, sétálóutca, szervizút),
amelyre cím szólhat. A rang 4–7 vonalakra a szabály nem vonatkozik.

**Hol.** `v3_io.betolt_telepules`, 5. pont: a `rang` és az `utca_id` kiszámítása után, a 0.3 vágás
előtt.

```
MINDEN utcavonalRA:
    HA rang == 3 ÉS (name hiányzik VAGY üres string) → kiszűröm
```

- A kiszűrt vonalak a `Bemenet`-ben megmaradnak a debughoz: új réteg `0_3_vonalak_kiszurt`
  (highway, rang).
- A neves rang 3 vonalakra minden marad (1.4 lógóvég-törlés, sliver, sávosítás).
- A 0.4 QA (b) nem változik (csak rang 4–5 hosszt mér).
- Napló: `n_rang3_nevtelen_kiszurt` (db) és `rang3_nevtelen_km`.
- Újraletöltés nem kell: a `name` oszlop a meglévő `utcak_all_edges.parquet`-ben megvan.

**Elfogadás (XX. kerület).** Tömbök a 2.1 után ≈ 600–650 (most 2354); sliver a 3.1-ben < 50
(most 937); különböző `utca_id` ≈ 300–350 (most 1817).

### J2 – T-csomópontot csak a vonalháló vonala ad

**Hiba.** A 3.2 és a 6. lépés T-csomópontja a leírás szerint „≥ 3 él a csúcsban”. Így a vázvonalak
és a sarok-vágóvonalak végpontjai is T-csomópontok, és a 6. lépés 13 949 vágásából 12 205-öt
ezek okoznak. A leírás szándéka (3.2, 6. ábrái) a keresztutca.

**Szabály.** Az idegen él csak akkor ad T-csomópontot, ha a rangja ≥ 2, vagyis bemeneti vonal
(utca, vasút, folyó, lakott határ) vagy narancs segédvonal. Vázvonal és vágóvonal (rang 1) soha.

**Hol.** `v3_topologia.vegpont_index` (a tuple-be kerüljön be az él rangja) és
`v3_topologia.t_csomopontok` (az idegen élek szűrése). A kollinearitás-kivétel és a `t_vagovonal`
változatlan. A 3.2 és a 6. lépés ugyanazt a függvényt hívja, mindkettőre hat.

**Elfogadás.** A `6_T_csomopontok` rétegben a diagnosztika A és B osztálya 0; a 6. lépés után a
cellaszám legfeljebb néhány százzal nő az 5. lépéshez képest.

### J3 – 9.2 rang-szabály: rang-első kiértékelési sorrend

**Hiba.** A 9.2 `cella_id` sorrendben megy, és minden üres cella az éppen besorolt szomszédai közül
választ. Ha egy cella rang 1 szomszédai még üresek, de az utca túloldala már besorolt, a cella az
utcán át örököl, pedig egy körrel később a saját tömbjéből kapna kategóriát. Példa: 5_R `cella_id`
13927 (Knézich utca – Dessewffy utca sarok) rang 4 élen át örökölt, miközben két rang 1 szomszédja
volt. A 615 utcán át öröklő cellából 583 ilyen.

**Szabály.** Egyszerre egy cella kap kategóriát: mindig az, amelyiknek a legalacsonyabb rangú a
kapcsolata egy már besorolt kategóriával; egyenlő rangnál az, amelyiknek a leghosszabb.

```
ISMÉTELD:
    MINDEN üres cellára, amelynek van besorolt szomszédja rang < 7 élen át:
        r(c) ← a besorolt szomszédokkal közös élek legkisebb rangja
        K(c), h(c) ← az r(c) rangú éleken át érintkező kategóriák közül az, amellyel
                     az r(c) rangú közös élek összhossza a legnagyobb (döntetlen: kisebb szkid)
    HA nincs ilyen cella → KÉSZ
    c* ← legkisebb r(c); egyenlőnél legnagyobb h(c); egyenlőnél kisebb cella_id
    c* kategóriája ← K(c*), szabály "rang"
```

- A hossz kategóriánként összegződik, nem szomszéd-cellánként: egy több darabra vágott szomszéd
  ne veszítsen egy egyben maradt ellen.
- Megvalósítás: prioritási sor (`heapq`) `(r, −h, cella_id)` kulccsal, lusta érvénytelenítéssel;
  egy besorolás után csak a besorolt cella üres szomszédait kell újraszámolni. Az élek az
  éltáblából jönnek soronként (nem a `szomszedok_osszesitve` szomszédonkénti összesítéséből).
- **Hol.** `v3_besorolas.lepes_9_2_rang`. A 10.1 utáni újrafutás ugyanezt hívja. A 3.3 beolvasztás
  nem változik.
- Debug: a `9_2_rang` nyíl a nyertes kategória leghosszabb élű szomszédjára mutat; új attribútum
  `sorszam` (a besorolás sorszáma).

**Elfogadás.** Egy cella csak akkor örököl rang ≥ 4 élen át, ha abban a pillanatban egyetlen üres
cellának sincs alacsonyabb rangú kapcsolata. A jelenlegi cellákon szimulálva az utcán át öröklő
cellák száma 615-ről 144-re esik.

### J4 – 9.1 utca menti folytonosság: csak szomszédos sávtól

**Hiba.** A 9.1 az utca mentén a legközelebbi címes sávot keresi távolságkorlát nélkül. A medián
öröklési távolság 121 m, a legnagyobb 2417 m. Ahol a két sáv utca menti vetülete között 250 m-nél
nagyobb a hézag, ott a cellák 66 %-a olyan kategóriát kap, amelynek a tömbben nincs címe; érintkező
vetületnél ez 2 %.

**Szabály.** Jelölt csak szomszédos sáv lehet: a két sáv U-ra vetített intervalluma között
legfeljebb `2 · SAROKMERET` a hézag. Indoklás: két egymást követő sáv között az utca mentén
legfeljebb két sarokcella állhat (az innenső tömb sarka és a szomszéd tömbé).

```
t-intervallum(sáv) ← [min, max] az U.project értékekből a sáv külső gyűrűjének csúcsain
hézag(a, b) ← max(0, max(a.min, b.min) − min(a.max, b.max))
jelölt, HA címes, ugyanaz az utca és U-komponens, ugyanaz az oldal, ÉS hézag ≤ 2 · SAROKMERET
```

A többi változatlan (két irány, egyezés vagy egy jelölt → az; eltérés → a közelebbi és jelzés;
egy menet, csak címes forrás). Ha egyik irányban sincs jelölt, a sáv a 9.2-re marad.

**Hol.** `v3_besorolas.lepes_9_1_folytonossag`. Debug: a `9_1_folytonossag` rétegbe új attribútum
`hezag`.

**Elfogadás.** A `9_1_folytonossag` rétegben nincs 60 m-nél nagyobb `hezag`.

### J5 – 10.1: egycímes átnyúlás az utca túloldalára

**Hiba.** A 10.1 csak az egyesítés utáni különálló darabot tekinti szigetnek. Ha egy rossz
koordinátájú cím az utca szemközti oldalára esik, a cellája az utcavonalon át összeér a saját
körzetével, ezért nem sziget, és a körzet egyetlen cím miatt átnyúlik az utcán.

**Szabály.** A 10.1 a meglévő szigetvizsgálat után egy második vizsgálatot is végez.

```
darabok ← kategóriánként a cellák, rang ≤ 3 éleken át összefüggő csoportokban
          (vagyis utcán, vasúton, folyón át nem összefüggő)
MINDEN darabRA, amelyben PONTOSAN EGY számító cím van:
    (a) a darabnak van rang ≤ 3 élen át más kategóriájú szomszédja
    (b) a saját kategóriája többi cellájával csak rang ≥ 4 éleken át érintkezik,
        és ezek az élek mind ugyanahhoz az utcához (utca_id) tartoznak
    (c) nincs megerősítés: a darab címes sávjának utcája mentén, ugyanazon az oldalon
        nincs szomszédos (J4 szerinti) sáv ugyanebből a kategóriából
    HA (a) ÉS (b) ÉS (c) → EGYCÍMES ÁTNYÚLÁS:
        a darab cellái ÜRES-ek, a cím "egycímes-átnyúlás" jelzést kap
```

- A (c) feltétel védi azt az esetet, amikor egy utca mindkét oldala ugyanahhoz a körzethez
  tartozik, és egy rövid tömboldalon csak egy geokódolt cím van. Sarokcellából álló darabnál
  (nincs utcája) a (c) nem vizsgálható, ott (a) és (b) dönt.
- Rang ≥ 4 él `utca_id` nélkül (vasút, folyó): a kulcs az él típusa.
- A BIZTONSÁG szabály érvényes marad: egy kategória nem tűnhet el.
- Az új jelzés: `JELZES_ATNYULAS = 'egycímes-átnyúlás'` (`v3_param.py`), bekerül a
  `KIHAGYOTT_CIM_JELZESEK` közé (`v3_topologia.py`), és a kimeneti `jelzes` értékkészletébe.
- Utána a 9. lépés újrafut 9.1 nélkül, ahogy most a szigeteknél.
- **Hol.** `v3_besorolas.lepes_10_1_szigetek`. Debug: új réteg `10_1_atnyulasok`. Napló:
  `n_egycimes_atnyulas`.

**Elfogadás.** A diagnosztika 7. pontjának utolsó sora (most 33) azokra az esetekre csökken,
amelyeket a (c) megerősít.

### J6 – Belső szilánkcellák beolvasztása

**Hiba.** A sliver-tisztítás csak a 3. lépésben fut. A 4–6. és a 8. lépés saját vágásai is
hoznak létre telekszélesség alatti, cím nélküli darabokat (a sarok X pontja körüli háromszögek,
rövid utcaszakasz keskeny sávja, 5 m alatti `d`-vel vágott sarok). A 6. lépés után 5523 cella
1 m²-nél kisebb. A 9.3-ra 604 cella marad (medián 0,01 m²), holott 468-nak geometriailag van
közös éle egy másik cellával. Valószínű ok, amit a kódban ellenőrizni kell: az `eltabla_epites`
bal/jobb mintavétele ± RÁCS távolságra a 2 cm-nél keskenyebb cellán kívülre esik, így a cella
egyetlen élhez sem kerül be.

**Szabály.** A 3.1 definíciója az eljárás által vágott cellákra is érvényes, de a beolvasztás
csak szerkesztett vonalon át történhet, tehát a tömbből nem lép ki.

```
BELSŐ SZILÁNK, HA  2·r < TELEKSZÉLESSÉG  ÉS  nincs számító címe  ÉS  van rang 1 éle
ISMÉTELD, AMÍG van belső szilánk, amely be tud olvadni:
    nyertes ← az a szomszéd, amellyel a rang 1 közös élek összhossza a legnagyobb
              (döntetlen: kisebb cella_id); a nyertes attribútumai maradnak
    R. rutin
```

- Rang ≥ 2 élen át soha nem olvad be; címes cella soha nem olvad be.
- **Hol.** Új függvény a `v3_sliver.py`-ban (a `beirt_atmero` és a 3.3 mintájára), hívás a
  `burkolas_v3.py`-ban a 6. lépés R. rutinja után és a 8. lépés R. rutinja után.
- Ellenőrzés az R. rutinban: az éltáblában egyetlen él nélkül maradt cellák száma a naplóba
  kerül (`n_szomszed_nelkuli`); utca nélküli foltcellán kívül 0-nak kell lennie.
- Debug: `6_S_beolvasztas`, `8_S_beolvasztas` (nyíl, mint 3.3). Napló: `n_belso_szilank`.

**Elfogadás.** A 9.3 csak valóban szomszéd nélküli cellát sorol be (XX. kerület: az 5 foltcella
közül az elszigeteltek). Cím nélküli, 6 m-nél keskenyebb cella csak akkor marad, ha nincs rang 1 éle.

### J7 – A sáv csúcsának levágása a sarokcellák vonalában (CSAK KÜLÖN JÓVÁHAGYÁS UTÁN)

Ez nem hibajavítás, hanem az 5. lépés kiegészítése egy vágóvonallal. Előbb a J1–J6 kész
eredményét kell megnézni.

**Jelenség.** A sáv a vázvonalig ér, a sarokcella csak 30 m-ig. A 60 m-nél mélyebb tömbben a sáv
ezért a két sarokcella közötti téglalapból és egy mögötte kicsúcsosodó háromszögből áll. A leírás
ezt ismert korlátként említi (5.), de a XX. kerület címes tömbjeinek 84 %-a mélyebb 60 m-nél
(a beírt kör átmérőjének mediánja 70 m), tehát ez az alapeset.

```
 ══════════════ utca ══════════════        ══════════════ utca ══════════════
 │sarok│     sáv eleje      │sarok│        │sarok│     sáv eleje      │sarok│
 ├─────┤                    ├─────┤        ├─────┼────────────────────┼─────┤ ← záróvonal X1–X2
 │      ╲    sáv csúcsa    ╱      │        │      ╲   hátsó rész     ╱      │
 │        ╲              ╱        │        │        ╲   (üres)     ╱        │
 │          ╲          ╱          │        │          ╲          ╱          │
      most: egy cella                          J7: két cella
```

**Szabály.**

```
MINDEN tömbRE, MINDEN utca-láncRA, amelynek mindkét végén készült sarokcella (X1, X2):
    záróvonal ← X1 → X2 szakasz
    a lánc sávját elvágom a záróvonallal (cella_felosztas)
    hátsó rész ← az a darab, amelyik nem érinti az utca láncát
    hátsó rész: cellatípus "tömb", utca_id üres, tomb_id marad
```

A hátsó rész címei szerint kap kategóriát; cím nélkül a 9.2 (J3) sorolja be, a 9.1 nem (nem sáv).
A J3 sorrendje miatt a hosszú oldal hátsó része előbb a saját elejéhez kerül (az a leghosszabb
éle), a rövid oldal levágott csúcsa pedig ahhoz a kategóriához, amellyel összesen a leghosszabb
a határa: ha a két hosszú oldal egy körzet, a csúcs hozzájuk kerül, és a rövid oldal 30 m mély
téglalap marad.

**Hol.** `v3_savok.lepes_5_sarokcellak`, a sarokcellák kivágása után, az R. rutin előtt.
Debug: `5_zarovonalak`.

---

## 3. Munkamenet

1. Mérés a jelenlegi kóddal: `python v3_diagnosztika.py szburkolas/adatok/Budapest XX. kerület_debug.gpkg`
   → a kimenet mentése.
2. A javítások egyenként, ebben a sorrendben: J1, J2, J6, J3, J4, J5. Mindegyik után:
   `teszt_v3_szintetikus.py` zöld, majd a XX. kerület és Halásztelek debug futása, a diagnosztika
   kimenete elmentve.
3. A végén a napló 19.3 táblázatának 24 települése kötegelt futásban: 0 hiba, 0 validációs hiba,
   két futás azonos kimenet.
4. Összefoglaló táblázat: a diagnosztika minden sora javítás előtt és után.

Ha egy javítás után egy mutató romlik, vagy a validáció bukik, állj meg és jelezd; ne tegyél be
kerülő megoldást vagy új küszöböt.

---

## 4. Tesztek (`teszt_v3_szintetikus.py` bővítése)

| Javítás | Új elem a mesterséges településen | Assert |
|---|---|---|
| J1 | névtelen járdahurok egy tömb körül; neves sétány két utca között | a járda nincs a hálóban, a sétány benne van; a tömbök száma nem változik a járdától |
| J2 | üres sáv vázvonal-csomóponttal a határán és egy szemközti keresztutcával | a sáv pontosan egy helyen vágódik (a keresztutcánál) |
| J3 | üres sarokcella, amelynek tömbön belüli szomszédai üresek, az utca túloldala besorolt | a sarokcella a saját tömbjének kategóriáját kapja |
| J4 | üres sáv, a legközelebbi címes sáv ugyanazon az utcán 200 m-re | a 9.1 nem sorolja be |
| J5 | egy idegen kategóriájú cím az utca túloldalán; és egy megerősített egycímes sáv | az első átnyúlás, a második marad |
| J6 | 5 m alatti `d`-vel vágott sarok | a sarokdarab beolvad, nincs szomszéd nélküli cella |

---

## 5. Dokumentáció

A leírásban (`B-burkolas-v3-mukodes.md`) a következő helyek frissítendők, a pszeudokóddal együtt:

- 0.4 rangtábla, 3. sor: „névvel rendelkező gyalogút, sétány, köz, lépcső, szervizút”; 1.1: a
  névtelen rang 3 vonal kiszűrése; 13. Ismert korlátok első pontja.
- 0.3 fogalmak, T-csomópont: „a vonalháló egy vonala (nem vázvonal, nem vágóvonal)”; 3.2 és 6.
- 9.1: a szomszédos sáv feltétele; 9.2: a rang-első sorrend pszeudokódja.
- 10.1: az egycímes átnyúlás; 11.: az új jelzés és az új debug rétegek.
- 3. vagy 6. után: a belső szilánkok beolvasztása.
- 12.: az új napló-mutatók.

A naplóban (`V3_MEGVALOSITASI_NAPLO.md`) új fejezet a J1–J6 megvalósításáról, a 20. táblázat
bővítése, és a javítás előtti/utáni mérőszámok.

A debug rétegekbe kerüljön be még: a cella-rétegekbe `utca_vonal_idx` és `jelzes`; új réteg
`10_1_cimek_jelzett` (a `hibás-sziget`, `azonos-pont`, `egycímes-átnyúlás` jelzésű címek).
A `8_1_vagas_T` és `8_1_vagas_kozep` rétegek `t` oszlopa kapja meg a valódi t értéket (napló 21/3).
