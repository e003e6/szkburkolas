import os
import gzip
import json
import glob
import shutil
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
from tqdm import tqdm


DATASET_LINKS_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
OUTPUT_DIR = "../data/nyers_data/microsoft_download"
MANIFEST_NAME = "_manifest.json"


def _build_session():
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _validate_jsonl_gz(gz_path):
    with gzip.open(gz_path, "rb") as f:
        first_line = f.readline()
    if not first_line.strip():
        raise IOError(f"Üres geojsonl fájl: {gz_path}")
    json.loads(first_line)


def _load_manifest(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(path, manifest):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def download_chunk(session, url, gz_path, csv_path):
    """Streamelve letölt egy .csv.gz chunk-ot, byte-ellenőrzéssel a Content-Length headerrel,
    validálja a gzip+JSONL tartalmat, kicsomagolja .csv-be, majd törli a .gz-t."""
    tmp_path = gz_path + ".part"
    with session.get(url, stream=True, timeout=(10, 120)) as r:
        r.raise_for_status()
        expected = r.headers.get("Content-Length")
        expected = int(expected) if expected is not None else None

        written = 0
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)

    if expected is not None and expected != written:
        os.remove(tmp_path)
        raise IOError(
            f"Csonka letöltés {url}: Content-Length={expected}, kapott={written}"
        )

    os.replace(tmp_path, gz_path)
    try:
        _validate_jsonl_gz(gz_path)
        with gzip.open(gz_path, "rb") as f_in, open(csv_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    finally:
        if os.path.exists(gz_path):
            os.remove(gz_path)


def ms_building_download():
    # Microsoft Maps letöltés útmutató: https://github.com/microsoft/GlobalMLBuildingFootprints
    # A .csv.gz fájlok valójában geojsonl tartalmúak (egy GeoJSON Feature soronként).
    # Frissülés-detektálás: a dataset-links.csv UploadDate és Url oszlopa alapján
    # egy _manifest.json követi nyilván, hogy melyik chunkot mikor és honnan töltöttük le.

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    manifest_path = os.path.join(OUTPUT_DIR, MANIFEST_NAME)

    dataset_links = pd.read_csv(DATASET_LINKS_URL, dtype=str)
    hun_links = dataset_links[dataset_links.Location == "Hungary"].reset_index(drop=True)

    assert len(hun_links) > 0, (
        "Nincs 'Hungary' sor a dataset-links.csv-ben — ellenőrizd, hogy a "
        "Location oszlop nem nevezte át Microsoft."
    )

    manifest = _load_manifest(manifest_path)
    session = _build_session()

    elavult_quadkeys = set(manifest.keys()) - set(hun_links.QuadKey)
    if elavult_quadkeys:
        print(
            f"FIGYELEM: {len(elavult_quadkeys)} chunk már nincs a Microsoft listájában: "
            f"{sorted(elavult_quadkeys)} — kézzel törölhetők a {OUTPUT_DIR}-ből."
        )

    sikertelenek = []
    skipped = 0
    letoltve = 0
    for _, row in tqdm(hun_links.iterrows(), total=len(hun_links), desc="MS chunks"):
        quadkey = row.QuadKey
        gz_path = os.path.join(OUTPUT_DIR, f"{quadkey}.csv.gz")
        csv_path = os.path.join(OUTPUT_DIR, f"{quadkey}.csv")

        rec = manifest.get(quadkey)
        up_to_date = (
            rec is not None
            and rec.get("url") == row.Url
            and rec.get("upload_date") == row.UploadDate
            and os.path.exists(csv_path)
            and os.path.getsize(csv_path) > 0
        )
        if up_to_date:
            skipped += 1
            continue

        try:
            download_chunk(session, row.Url, gz_path, csv_path)
            manifest[quadkey] = {"url": row.Url, "upload_date": row.UploadDate}
            _save_manifest(manifest_path, manifest)
            letoltve += 1
        except Exception as e:
            sikertelenek.append((quadkey, str(e)))

    print(f"MS letöltés kész: {letoltve} új/frissített chunk, {skipped} változatlan skippelve.")

    if sikertelenek:
        msg = "\n".join(f"  {qk}: {err}" for qk, err in sikertelenek)
        raise RuntimeError(
            f"{len(sikertelenek)} chunk letöltése meghiúsult:\n{msg}"
        )

    letoltott_csv = glob.glob(os.path.join(OUTPUT_DIR, "*.csv"))
    assert len(letoltott_csv) >= len(hun_links), (
        f"Chunk-szám eltérés: vártunk legalább {len(hun_links)} fájlt, "
        f"de {len(letoltott_csv)} db .csv van a {OUTPUT_DIR}-ben"
    )


if __name__ == "__main__":
    ms_building_download()
