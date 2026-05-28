# A fájl letölti az aktuális Overpass adatokat a data/nyers_data/overpass_download mappába.
# Régiónként két lekérdezés készül: addr:housenumber-rel rendelkező node-ok (pontok)
# és minden building tag-gel rendelkező way/relation (épület poligonok).

import json
import os

import osm2geojson
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry


OUTPUT_DIR = "../data/nyers_data/overpass_download"
PONTOK_DIR = os.path.join(OUTPUT_DIR, "pontok")
EPULETEK_DIR = os.path.join(OUTPUT_DIR, "epuletek")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 1500  # 25 perc — nagy régiók épület-lekérdezése lassú


REGIONS = {
    # slug -> (OSM relation name, admin_level)
    "budapest":          ("Budapest",           "6"),
    "pestmegye":         ("Pest vármegye",      "6"),  # 2022-es átnevezés óta vármegye
    "kozepdunantul":     ("Közép-Dunántúl",     "5"),
    "nyugatdunantul":    ("Nyugat-Dunántúl",    "5"),
    "deldunantul":       ("Dél-Dunántúl",       "5"),
    "eszakmagyarorszag": ("Észak-Magyarország", "5"),
    "eszakalfold":       ("Észak-Alföld",       "5"),
    "delalfold":         ("Dél-Alföld",         "5"),
}


QUERY_PONTOK = """\
[out:json][timeout:{timeout}];
{area_query}
( node["addr:housenumber"](area.searchArea); );
out body geom;
"""

QUERY_EPULETEK = """\
[out:json][timeout:{timeout}];
{area_query}
(
  way["building"](area.searchArea);
  relation["building"](area.searchArea);
);
out body geom;
"""


def _build_session():
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    # Overpass 406-tal utasít vissza UA nélküli kéréseket.
    s.headers.update({"User-Agent": "szkburkolas-pipeline/1.0"})
    return s


def _area_query(name, admin_level):
    return (
        f'rel["name"="{name}"]["boundary"="administrative"]'
        f'["admin_level"="{admin_level}"]; map_to_area->.searchArea;'
    )


def _post_overpass(session, query):
    r = session.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=(30, OVERPASS_TIMEOUT_S + 120),
    )
    r.raise_for_status()
    return r.json()


def _flatten_properties(features):
    # osm2geojson nested ({type, id, tags, nodes, ...}) -> flat ({id, ...tags})
    # `id` (nem `@id`), mert ezt várja a downstream osm_epulet_pont_egyesites.py.
    for f in features:
        props = f.get("properties", {})
        osm_type = props.get("type")
        osm_id = props.get("id")
        tags = props.get("tags") or {}
        new_props = {}
        if osm_type and osm_id is not None:
            new_props["id"] = f"{osm_type}/{osm_id}"
        new_props.update(tags)
        f["properties"] = new_props


def _osm_json_to_geojson_str(osm_json):
    gj = osm2geojson.json2geojson(osm_json)
    _flatten_properties(gj.get("features", []))
    ordered = {
        "type": gj.get("type", "FeatureCollection"),
        "generator": "overpass-api-script",
        "copyright": (
            "The data included in this document is from www.openstreetmap.org. "
            "The data is made available under ODbL."
        ),
        "timestamp": osm_json.get("osm3s", {}).get("timestamp_osm_base", ""),
        "features": gj.get("features", []),
    }
    return json.dumps(ordered, ensure_ascii=False, indent=2)


def _clean_legacy_files():
    # A korábbi kézi letöltések elgépelt fájljai — friss letöltés helyes nevet ír,
    # a régieket itt egyszer kitakarítjuk, hogy a downstream ne olvassa be duplán.
    for legacy in [
        os.path.join(PONTOK_DIR, "overpass_pont_nyudatdunantul.geojson"),
        os.path.join(EPULETEK_DIR, "overpass_epulet_nyudatdunantul.geojson"),
        os.path.join(EPULETEK_DIR, "overpas_epulet_delalfold.geojson"),
    ]:
        if os.path.exists(legacy):
            os.remove(legacy)


def overpass_download():
    os.makedirs(PONTOK_DIR, exist_ok=True)
    os.makedirs(EPULETEK_DIR, exist_ok=True)
    _clean_legacy_files()

    session = _build_session()

    jobs = []
    for slug, (name, admin_level) in REGIONS.items():
        area_q = _area_query(name, admin_level)
        jobs.append((
            slug, "pont", PONTOK_DIR,
            QUERY_PONTOK.format(timeout=OVERPASS_TIMEOUT_S, area_query=area_q),
        ))
        jobs.append((
            slug, "epulet", EPULETEK_DIR,
            QUERY_EPULETEK.format(timeout=OVERPASS_TIMEOUT_S, area_query=area_q),
        ))

    sikertelenek = []
    for slug, kind, outdir, query in tqdm(jobs, desc="Overpass régiók"):
        fname = f"overpass_{kind}_{slug}.geojson"
        out_path = os.path.join(outdir, fname)
        tmp_path = out_path + ".part"
        try:
            osm_json = _post_overpass(session, query)
            if not osm_json.get("elements"):
                raise IOError(
                    f"Üres Overpass válasz {fname}-hoz "
                    f"(remark={osm_json.get('remark')!r})"
                )
            geojson_str = _osm_json_to_geojson_str(osm_json)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(geojson_str)
            os.replace(tmp_path, out_path)
        except Exception as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            sikertelenek.append((fname, str(e)))

    if sikertelenek:
        msg = "\n".join(f"  {n}: {e}" for n, e in sikertelenek)
        raise RuntimeError(
            f"{len(sikertelenek)} Overpass lekérdezés meghiúsult:\n{msg}"
        )


if __name__ == "__main__":
    overpass_download()
