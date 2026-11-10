# Proyecto 4: BIGDATA

## Descripción y objetivo

Este proyecto tiene como objetivo preparar una estructura de trabajo para organizar la ingesta, el almacenamiento y el procesamiento de datos dentro del módulo de Big Data. El resultado será un flujo de trabajo documentado que permita preparar los datos para su análisis y visualización.

Caso de uso: Big Data urbano de Valencia (tráfico, calidad del aire y meteorología) con datos abiertos.

## Arquitectura

```mermaid
graph LR
    A[Fuentes de datos] --> B[Ingesta]
    B --> C[Almacenamiento]
    C --> D[Procesamiento]
    D --> E[Análisis y visualización]
```

## Estructura del repositorio

```text
P4BIGDATA/
├── .gitignore
├── README.md
├── requirements.txt
├── docker-compose.yml        # NiFi (ampliación)
├── recolector/               # independiente: se copia tal cual al servidor
│   ├── recolector.py
│   ├── crontab.txt           # programación en Linux
│   ├── programar_windows.ps1 # equivalente para probar en Windows
│   └── .env.example          # plantilla (el .env real no se versiona)
├── ingesta/
│   └── historico_aire.py     # carga única del histórico 2016-2022
├── nifi/                     # exportación del flujo NiFi (ampliación)
├── data/raw/                 # no versionado
├── logs/                     # no versionado
├── etl/
└── dashboard/
```

## Tecnologías

- Python 3.10 o superior (el recolector solo exige ≥ 3.9) con `requests`, `pyarrow` y `python-dotenv`.
- Git y GitHub.
- cron (Linux) / Programador de tareas (Windows) para la ingesta programada.
- Apache NiFi 1.28.1 (ampliación opcional).

Instalación:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp recolector/.env.example recolector/.env           # y poner la AEMET_API_KEY
```

---

## Bitácora de prácticas

### Bloque 1: Ingesta batch

#### Fuentes de datos

| Fuente | Origen | Capa / endpoint | Clave | Frecuencia en origen | Histórico |
|---|---|---|---|---|---|
| Tráfico: espiras | Geoportal Valencia (ArcGIS MapServer) | `Trafico/MapServer/208` | No | Cada hora | No |
| Tráfico: tramos | Geoportal Valencia | `Trafico/MapServer/188` | No | Tiempo real | No |
| Tráfico: estado | Geoportal Valencia | `Trafico/MapServer/192` | No | Cada 3 min | No |
| Calidad del aire (estaciones) | Geoportal Valencia | `MedioAmbiente/MapServer/156` | No | Horaria | No (solo foto actual) |
| Meteorología municipal | Geoportal Valencia | `MedioAmbiente/MapServer/157` | No | Horaria | No |
| Meteorología (predicción) | AEMET OpenData | `prediccion/especifica/municipio/diaria/46250` | **Sí** (`AEMET_API_KEY`) | Diaria | No |
| Calidad del aire horaria 2016-2022 | CKAN `opendata.vlci.valencia.es` | `datastore_search` (2 recursos) | No | Carga única | Sí, hasta 2022 |

Consulta de ejemplo (base `https://geoportal.valencia.es/server/rest/services/OPENDATA`):

```text
<base>/Trafico/MapServer/208/query?where=1=1&outFields=*&f=json
<base>/Trafico/MapServer/208?f=pjson            # campos y tipos de la capa
&resultRecordCount=3                              # solo 3 registros
```

**Ninguna fuente pública ofrece histórico de tráfico** (ni de calidad del aire horaria desde 2023): el Geoportal solo da la foto del momento. El histórico se construye acumulando las capturas del recolector, y cada captura perdida es un hueco definitivo.

**Campos usados**

- *Espiras (208, 1211 registros)*: `idpm` (punto de medida), `ih` (vehículos/hora; `-1` = sin dato, `null` = sin lectura), `fecha_actualizacion` (epoch ms), `angulo`, más `longitud`/`latitud` añadidas desde la geometría.
- *Tramos (188)*: `idtramo`, `des_tramo`, `lectura`, `estado`, `imv`, `tipo_vehiculo`. *Estado (192)*: `idtramo`, `denominacion`, `estado`. Son capas de líneas: no llevan coordenadas.
- *Aire (156, 11 estaciones)*: `nombre`, `no2`, `pm10`, `pm25`, `so2`, `o3`, `co`, `fecha_carg` (epoch ms), `calidad_am`, `tipozona`, `tipoemisio`, `parametros`.
- *Meteo municipal (157, 5 estaciones)*: `nombre`, `fecha_carg`, `viento_dir`, `viento_vel`, `temperatur`, `humedad_re`, `presion_ba`, `precipitac`.
- *AEMET*: una fila por día pronosticado (`fecha`, `temperatura_maxima`, `temperatura_minima`, `probabilidad_precipitacion` del periodo `00-24`).
- *Histórico CKAN*: 28 columnas (2016-2021, 449.026 filas) y 31 (2021-2022, 201.480 filas): `fecha`, `hora`, `estacion`, contaminantes (`pm1`, `pm2_5`, `pm10`, `no`, `no2`, `nox`, `o3`, `so2`, `co`…) y meteo por estación.

#### Diagrama de la ingesta

```mermaid
flowchart TB
    subgraph SRV["Servidor siempre encendido (cron)"]
        R[recolector.py]
        G["Geoportal: espiras, tramos,<br/>estado, aire, meteo"]
        A["AEMET<br/>(predicción municipio 46250)"]
        D[("datos/&lt;fuente&gt;/fecha=.../*.parquet")]
        L[("_registro/ejecuciones.csv")]
        R --> G
        R --> A
        R --> D
        R --> L
    end
    H["historico_aire.py<br/>carga única"]
    P[("data/raw en el proyecto")]
    D -- rsync --> P
    L -- rsync --> P
    CKAN[(CKAN)] --> H
    H --> P
```

El recolector (en el servidor) guarda cada captura en Parquet y anota el resultado en `_registro/ejecuciones.csv`; esas carpetas se sincronizan con `rsync` a `data/raw/` del proyecto. El histórico 2016-2022 se descarga una sola vez desde CKAN directamente a `data/raw/`.

Ejemplo de sincronización, desde el proyecto:

```bash
rsync -av usuario@servidor:/opt/p4urbano/data/raw/ data/raw/
```

#### Estructura de lo almacenado

```text
data/raw/
├── trafico/{espiras,tramos,estado}/fecha=AAAA-MM-DD/<fuente>_AAAAMMDDTHHMMSSZ.parquet
├── calidad_aire/estaciones/fecha=.../aire_....parquet
├── calidad_aire/historico/origen={2016_2021,2021_2022}/historico.parquet
├── meteo/{municipal,aemet}/fecha=.../...parquet
└── _registro/ejecuciones.csv        # captura_utc, fuente, ok, filas, error
```

Cada fichero lleva la columna `captura_utc` (momento de la captura). Un fallo en una fuente se anota en `ejecuciones.csv` con `ok=0` y el mensaje, y **no impide recoger las demás**: ese registro es la base para medir huecos en el Bloque 4.

#### Programación (cron)

Ver [recolector/crontab.txt](recolector/crontab.txt). Se instala con `crontab recolector/crontab.txt` en un servidor siempre encendido.

| Fuente | Cron | Justificación |
|---|---|---|
| estado | `*/5 * * * *` | Origen cada 3 min; cada 5 min da una buena resolución con ~288 ficheros/día de 18 KB (≈5 MB/día). |
| tramos | `*/15 * * * *` | Tiempo real en origen, pero la lectura por tramo no necesita más de 4 fotos/hora. |
| espiras | `5 * * * *` | El sensor se actualiza cada hora; capturar más seguido repetiría datos. Minuto 5 para dejar margen a la publicación. |
| aire, meteo | `10 * * * *` | Datos horarios, publicados poco después de la hora en punto. |
| aemet | `30 6 * * *` | Predicción diaria; una llamada al día evita errores 429 y la API solo cambia unas pocas veces al día. |

Volumen estimado: ≈10 MB/día en Parquet con esta programación.

Para probarlo en Windows se incluye [recolector/programar_windows.ps1](recolector/programar_windows.ps1), que crea las mismas tareas en el Programador de tareas (`-Quitar` las elimina). Registro de ejecuciones en `logs/cron_<fuente>.log` y en `data/raw/_registro/ejecuciones.csv`.

Evidencia: las cinco tareas `P4-*` en el Programador de tareas de Windows, con su última ejecución y resultado correcto, y las líneas resultantes en `ejecuciones.csv`.

![Programador de tareas](docs/evidencias/programador_tareas.png)

#### Carga única del histórico de aire (2016-2022)

```bash
python ingesta/historico_aire.py            # no repite si ya existe
python ingesta/historico_aire.py --forzar   # vuelve a descargar
```

Se descargan dos recursos de CKAN con `datastore_search`:

| Origen | Resource id | Filas | Columnas |
|---|---|---|---|
| `origen=2016_2021` | `4be7248b-9597-4017-89af-82a9b6e2382f` | 449.026 | 28 |
| `origen=2021_2022` | `19b5a1a7-5888-4d3e-b69b-3c1436d64e6e` | 201.480 | 31 |

Todos los valores se guardan como **texto, tal cual vienen**; solo se normalizan los nombres de columna (`PM2.5` → `pm2_5`). Se escribe a un fichero temporal que sustituye al final, así una descarga cortada no deja datos a medias.

#### Incidencias encontradas

- **Ejemplo de aire del enunciado**: era una copia del de tráfico. Los campos reales de la capa 156 son `nombre`, `no2`, `pm10`, `fecha_carg`, etc.
- **Formato de las peticiones**: se usa `f=json` con paginación (`exceededTransferLimit`) y `outSR=4326`; la longitud y la latitud se añaden desde `geometry` en las capas de puntos.
- **Espiras sin dato**: en la captura del 2026-11-04, 213 de 1211 registros tenían `ih` y `fecha_actualizacion` nulos y 39 tenían `ih = -1`. Hay 1211 registros pero 1210 `idpm` distintos (un punto duplicado).
- **Aire**: Boulevar Sur y Viveros devuelven `pm10` nulo (no lo miden). Las agregaciones deben ignorar nulos.
- **Capas de líneas**: `tramos` y `estado` no tienen `longitud`/`latitud`.
- **AEMET**: probabilidad de precipitación a `None` a partir del quinto día de la predicción (AEMET no la da). No se han observado errores 429 con una llamada diaria. Se usa el endpoint de predicción por municipio 46250; el enunciado menciona también la estación 8416, que no se ha usado.
- **Tres recursos de histórico en CKAN**: el recurso "2016-2020" tiene 352.666 filas y no coincide con el enunciado; se usan "desde 2016" (449.026) y "2021-2022" (201.480).
- **Columna `Id` y `_id`**: el recurso 2021-2022 trae `Id` y el `_id` interno de CKAN, que chocaban al normalizar los nombres; `_id` se renombra a `ckan_id`.
- **Descarga cortada**: el servidor CKAN truncó una respuesta (`IncompleteRead`); el reintento cubre ahora cualquier error de red o JSON incompleto.
- **Calidad del histórico (para el Bloque 4)**: fechas con día y mes intercambiados (p. ej. `fecha=2021-03-07` con `dia_del_mes=3`), `fecha` con formatos `2020-01-01T00:00:00` y `2022-10-05` según el recurso, y horas con formatos `0:00:00` y `4:00` mezclados.
- **Programador de tareas**: (1) por defecto Windows no lanza tareas con el portátil a batería y la tarea acababa con resultado 0 sin ejecutar nada; se corrigió con `-AllowStartIfOnBatteries` y `-StartWhenAvailable`. (2) Varias tareas lanzadas a la vez fallaban (resultado 1) al escribir en el mismo `cron.log`; ahora hay un log por fuente. (3) `schtasks /TR` admite 261 caracteres como máximo; se usa el lanzador corto `ejecutar.cmd`.
- **Windows**: `pyarrow` no cargaba por una directiva de Control de aplicaciones de Windows que bloquea DLL sin firmar; se resolvió desactivándola. En Windows hace falta además `tzdata` para leer zonas horarias.

### Ampliación: captura de espiras con Apache NiFi

Despliegue: `docker compose up -d nifi` y acceso a `https://localhost:8443/nifi` (usuario `admin`; la contraseña está en `docker-compose.yml`). La carpeta `data/raw/nifi` del host se monta en `/data/raw/nifi` del contenedor.

Flujo (cada hora): `InvokeHTTP` → `EvaluateJsonPath` → `SplitJson` → `UpdateAttribute` → `PutFile`. `PutFile` no tiene propiedad de nombre de fichero (usa el atributo `filename` del flowfile), por eso se añade `UpdateAttribute` para poner el nombre con marca de tiempo.

| Procesador | Configuración |
|---|---|
| InvokeHTTP | GET `https://geoportal.valencia.es/server/rest/services/OPENDATA/Trafico/MapServer/208/query?where=1=1&outFields=*&f=geojson`, Run Schedule 1 hour |
| EvaluateJsonPath | Destination `flowfile-content`, propiedad `features` = `$.features` |
| SplitJson | JsonPath Expression `$` (un flowfile por punto de medida) |
| UpdateAttribute | Propiedad `filename` = `espiras_${now():format('yyyyMMdd-HHmmss')}_${fragment.index}.json` |
| PutFile | Directory `/data/raw/nifi` |

**Incidencia con el certificado del Geoportal**: la primera ejecución de `InvokeHTTP` falló con `SSLHandshakeException: PKIX path building failed`. El Geoportal usa certificados de la ACCV, cuya raíz no está en el almacén de confianza del Java del contenedor (`openssl` sí la reconoce). Solución: crear un truststore PKCS12 con la cadena ACCV (`keytool -importcert` dentro del contenedor, en `/opt/nifi/nifi-current/conf/p4-truststore.p12`) y asignar a `InvokeHTTP` un `StandardSSLContextService` que lo use, en lugar de desactivar la verificación TLS.

**Resultado**: con *Run Once* en `InvokeHTTP` el flujo produjo 1 flowfile (503 KB) que `SplitJson` dividió en **1.211** (uno por punto de medida) y `PutFile` escribió como 1.211 ficheros `espiras_AAAAMMDD-HHMMSS_<n>.json` en `data/raw/nifi/` (≈425 bytes cada uno), con todas las colas a 0. Flujo exportado con *Download flow definition* en [nifi/espiras_flow.json](nifi/espiras_flow.json) (sin contraseñas: el truststore es un valor sensible y NiFi no lo exporta).

![Flujo NiFi en ejecución](docs/evidencias/nifi_flujo.png)

![Data Provenance de un flowfile en PutFile](docs/evidencias/nifi_provenance.png)

#### Comparativa: recolector Python frente a flujo NiFi

Basada en lo observado al construir y ejecutar ambos. Nota: el primer procesador se creó desde la interfaz y el resto del flujo se configuró mediante la API REST de NiFi, así que el tiempo de desarrollo no es el de un montaje enteramente manual.

| Aspecto | Recolector Python | Flujo NiFi |
|---|---|---|
| Tiempo de desarrollo | Unas pocas funciones (≈150 líneas en `recolector.py`); el reintento, el Parquet y el registro CSV hay que escribirlos, pero `requests` resolvió el certificado del Geoportal sin configurar nada | Imagen de 3,3 GB, 1-2 min de arranque, 5 procesadores y 4 conexiones. Hubo que resolver el certificado ACCV (truststore + servicio SSL) y añadir `UpdateAttribute`, porque `PutFile` no tiene propiedad de nombre de fichero. No se guarda en Parquet ni se registra el resultado en un CSV |
| Facilidad para depurar un fallo | Traza en `logs/cron_<fuente>.log` y mensaje en `ejecuciones.csv`; se reproduce ejecutando el script a mano | El error aparece al instante como *bulletin* sobre el procesador (el `PKIX path building failed` se vio en la propia pantalla), pero la causa real exigió `docker logs`, `openssl` y `keytool` dentro del contenedor |
| Visibilidad del estado en ejecución | Ninguna salvo los logs; hay que consultar `ejecuciones.csv` o el Programador de tareas | Contadores In/Out, colas entre procesadores (1 → 1 → 1.211 → 1.211 → 1.211, todas a 0) y Data Provenance de cada flowfile, todo en tiempo real |
| Facilidad de versionado en Git | Texto plano, diffs legibles | Un JSON de ≈21 KB (`espiras_flow.json`) con identificadores, posiciones en el lienzo y descriptores de propiedades: versionable, pero con diffs ruidosos y poco legibles |
| Recursos necesarios en el servidor | Python + 3 librerías, sin servicios; basta con cron y se ejecuta unos segundos | Contenedor Java permanente: imagen de 3,3 GB y 1,63 GiB de RAM en reposo (≈6 % de CPU), y genera 1.211 ficheros pequeños en lugar de un Parquet de ≈90 KB |
