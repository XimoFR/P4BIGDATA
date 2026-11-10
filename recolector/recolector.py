"""Recolector de fotos en tiempo real (Geoportal Valencia + AEMET).

Uso: python recolector.py espiras tramos estado aire meteo aemet
Cada fuente se recoge de forma independiente: un fallo queda registrado en
_registro/ejecuciones.csv y no impide recoger las demás.
"""
import csv
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests
from dotenv import load_dotenv

AQUI = Path(__file__).resolve().parent
load_dotenv(AQUI / ".env")

# Por defecto <proyecto>/data/raw; se puede cambiar con RECOLECTOR_DATA_DIR
# (útil al copiar el recolector solo a un servidor).
DATA_DIR = Path(os.getenv("RECOLECTOR_DATA_DIR", AQUI.parent / "data" / "raw"))

GEOPORTAL = "https://geoportal.valencia.es/server/rest/services/OPENDATA"
AEMET = "https://opendata.aemet.es/opendata/api/prediccion/especifica/municipio/diaria/46250"

FUENTES = {
    "espiras": {"capa": "Trafico/MapServer/208", "ruta": "trafico/espiras"},
    "tramos": {"capa": "Trafico/MapServer/188", "ruta": "trafico/tramos"},
    "estado": {"capa": "Trafico/MapServer/192", "ruta": "trafico/estado"},
    "aire": {"capa": "MedioAmbiente/MapServer/156", "ruta": "calidad_aire/estaciones"},
    "meteo": {"capa": "MedioAmbiente/MapServer/157", "ruta": "meteo/municipal"},
}

log = logging.getLogger("recolector")


def get_con_reintentos(url, params=None, headers=None, reintentos=3):
    """GET con reintentos ante errores de red, 429 y 5xx."""
    for intento in range(reintentos):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            if intento == reintentos - 1:
                raise
            time.sleep(10 * (intento + 1))


def descargar_capa(capa: str) -> list[dict]:
    """Devuelve todos los registros de una capa ArcGIS (con paginación).

    Pide las coordenadas en WGS84 (outSR=4326) y las añade como longitud/latitud
    cuando la capa tiene geometría de puntos.
    """
    url = f"{GEOPORTAL}/{capa}/query"
    params = {"where": "1=1", "outFields": "*", "outSR": 4326, "f": "json", "resultOffset": 0}
    registros = []
    while True:
        datos = get_con_reintentos(url, params).json()
        if "error" in datos:
            raise RuntimeError(f"ArcGIS: {datos['error']}")
        for f in datos["features"]:
            fila = dict(f["attributes"])
            geom = f.get("geometry") or {}
            if "x" in geom and "y" in geom:
                fila["longitud"], fila["latitud"] = geom["x"], geom["y"]
            registros.append(fila)
        if not datos.get("exceededTransferLimit"):
            return registros
        params["resultOffset"] = len(registros)


def descargar_aemet() -> list[dict]:
    """Predicción diaria de Valencia (46250) en dos pasos; una fila por día."""
    clave = os.getenv("AEMET_API_KEY")
    if not clave:
        raise RuntimeError("Falta AEMET_API_KEY en recolector/.env")
    peticion = get_con_reintentos(AEMET, headers={"api_key": clave}).json()
    if peticion.get("estado") != 200:
        raise RuntimeError(f"AEMET paso 1: {peticion.get('estado')} {peticion.get('descripcion')}")
    prediccion = get_con_reintentos(peticion["datos"]).json()
    filas = []
    for dia in prediccion[0]["prediccion"]["dia"]:
        prob = next(
            (p["value"] for p in dia.get("probPrecipitacion", []) if p.get("periodo") == "00-24"),
            None,
        )
        filas.append({
            "fecha": dia["fecha"][:10],
            "temperatura_maxima": dia["temperatura"]["maxima"],
            "temperatura_minima": dia["temperatura"]["minima"],
            "probabilidad_precipitacion": prob,
        })
    return filas


def guardar_parquet(registros: list[dict], ruta: str, nombre: str, captura: datetime) -> Path:
    """Escribe <DATA_DIR>/<ruta>/fecha=AAAA-MM-DD/<nombre>_AAAAMMDDTHHMMSSZ.parquet."""
    tabla = pa.Table.from_pylist(registros)
    tabla = tabla.append_column("captura_utc", pa.array([captura] * tabla.num_rows))
    carpeta = DATA_DIR / ruta / f"fecha={captura:%Y-%m-%d}"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{nombre}_{captura:%Y%m%dT%H%M%SZ}.parquet"
    pq.write_table(tabla, destino, compression="snappy")
    return destino


def registrar(captura: datetime, fuente: str, ok: bool, filas: int, error: str = "") -> None:
    """Añade una línea a _registro/ejecuciones.csv."""
    fichero = DATA_DIR / "_registro" / "ejecuciones.csv"
    fichero.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not fichero.exists()
    with fichero.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(["captura_utc", "fuente", "ok", "filas", "error"])
        w.writerow([captura.isoformat(), fuente, int(ok), filas, error[:300]])


def recoger(nombre: str) -> None:
    captura = datetime.now(timezone.utc)
    try:
        if nombre == "aemet":
            registros, ruta = descargar_aemet(), "meteo/aemet"
        else:
            registros, ruta = descargar_capa(FUENTES[nombre]["capa"]), FUENTES[nombre]["ruta"]
        if not registros:
            raise RuntimeError("La fuente devolvió 0 registros")
        destino = guardar_parquet(registros, ruta, nombre, captura)
        registrar(captura, nombre, True, len(registros))
        log.info("%s: %d filas -> %s", nombre, len(registros), destino)
    except Exception as e:
        registrar(captura, nombre, False, 0, str(e))
        log.exception("Fallo en %s", nombre)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for fuente in sys.argv[1:]:  # p. ej.: python recolector.py espiras tramos aire meteo aemet
        recoger(fuente)
