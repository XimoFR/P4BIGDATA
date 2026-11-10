"""Carga única del histórico horario de calidad del aire de Valencia (2016-2022).

Descarga los dos recursos del portal CKAN con datastore_search y los guarda en
data/raw/calidad_aire/historico/origen=<rango>/historico.parquet.

Todos los valores se guardan como texto, tal cual vienen (formatos de fecha/hora
distintos y días/meses intercambiados se arreglan en el Bloque 4). Solo se
normalizan los nombres de columna (minúsculas, sin tildes ni espacios).

Uso: python historico_aire.py [--forzar]
"""
import argparse
import logging
import re
import time
import unicodedata
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
CKAN = "https://opendata.vlci.valencia.es/api/3/action/datastore_search"
PAGINA = 30000

RECURSOS = {
    "2016_2021": "4be7248b-9597-4017-89af-82a9b6e2382f",  # ~449.000 filas
    "2021_2022": "19b5a1a7-5888-4d3e-b69b-3c1436d64e6e",  # ~201.000 filas
}

log = logging.getLogger("historico_aire")


def normalizar(nombre: str) -> str:
    """'Dia de la semana' -> 'dia_de_la_semana'; 'PM2.5' -> 'pm2_5'."""
    if nombre == "_id":  # id interno de CKAN; evita chocar con la columna 'Id' del recurso
        return "ckan_id"
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sin_tildes.lower()).strip("_")


def get_con_reintentos(params, reintentos=3):
    for intento in range(reintentos):
        try:
            r = requests.get(CKAN, params=params, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()["result"]
        except (requests.RequestException, ValueError):  # red, HTTP, respuesta truncada o JSON roto
            if intento == reintentos - 1:
                raise
            time.sleep(10 * (intento + 1))


def descargar(origen: str, resource_id: str, destino: Path) -> int:
    """Escribe el recurso en `destino` página a página; devuelve las filas escritas."""
    tmp = destino.with_suffix(".tmp")
    escritor = None
    filas = 0
    offset = 0
    try:
        while True:
            res = get_con_reintentos({"resource_id": resource_id, "limit": PAGINA, "offset": offset})
            registros = res["records"]
            if not registros:
                break
            if escritor is None:
                campos = [f["id"] for f in res["fields"]]
                nombres = [normalizar(c) for c in campos]
                if len(set(nombres)) != len(nombres):
                    raise RuntimeError(f"{origen}: nombres de columna duplicados tras normalizar: {nombres}")
                schema = pa.schema([(n, pa.string()) for n in nombres])
                escritor = pq.ParquetWriter(tmp, schema, compression="snappy")
            columnas = [
                pa.array([None if r[c] is None else str(r[c]) for r in registros], pa.string())
                for c in campos
            ]
            escritor.write_table(pa.Table.from_arrays(columnas, schema=schema))
            filas += len(registros)
            offset += len(registros)
            log.info("%s: %d / %d filas", origen, filas, res["total"])
            if filas >= res["total"]:
                break
    except BaseException:
        if escritor is not None:
            escritor.close()
        tmp.unlink(missing_ok=True)
        raise
    if escritor is None:
        raise RuntimeError(f"{origen}: el recurso no devolvió filas")
    escritor.close()
    tmp.replace(destino)
    return filas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forzar", action="store_true", help="repetir la descarga aunque ya exista")
    args = ap.parse_args()

    for origen, resource_id in RECURSOS.items():
        carpeta = DATA_DIR / "calidad_aire" / "historico" / f"origen={origen}"
        destino = carpeta / "historico.parquet"
        if destino.exists() and not args.forzar:
            log.info("%s: ya existe %s, se omite (usa --forzar)", origen, destino)
            continue
        carpeta.mkdir(parents=True, exist_ok=True)
        filas = descargar(origen, resource_id, destino)
        log.info("%s: %d filas -> %s", origen, filas, destino)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
