#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Partidos del fin de semana del C.D. Ciudad de los Ángeles, listos para WhatsApp.

Lee elbalondemadrid.es (datos públicos de la RFFM), localiza automáticamente
todos los equipos del club, busca sus partidos del fin de semana y genera el
mensaje con el formato de WhatsApp (negrita con *asteriscos*).
Además genera el mensaje de RESULTADOS: guarda en estado.json los partidos que
va viendo y, cuando la web publica el marcador (en el calendario o en el acta),
lo añade.

Uso:
    pip install requests beautifulsoup4
    python partidos_whatsapp.py                    # próximo fin de semana
    python partidos_whatsapp.py --sabado 2026-10-10  # un fin de semana concreto
    python partidos_whatsapp.py --abrir            # además abre WhatsApp con el texto

Resultado: se imprime por pantalla y se guarda en partidos_whatsapp.txt
(con --json también se guarda en JSON para la página web de GitHub Pages)
"""
import argparse
import copy
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Comment, NavigableString

BASE = "https://www.elbalondemadrid.es"
CLUB_ID = 4427  # C.D. Ciudad de los Ángeles
CLUB_URL = f"{BASE}/club/{CLUB_ID}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
}
PAUSA = 0.8  # segundos entre peticiones (cortesía con la web)

# Calendarios ya comprobados de la temporada 2026/27: ruta -> (nombre, ids de equipo).
# Así no hace falta abrir la ficha de cada equipo. Los equipos que no estén aquí
# (p. ej. Segunda Alevín F-7) se buscan solos desde la ficha del club.
CONOCIDOS = {
    "competicion/26737701/grupo/26737704": ("Preferente Aficionado Grupo 3", {"2856"}),
    "competicion/26738300/grupo/26738319": ("Segunda Aficionado Grupo 20", {"6818931"}),
    "competicion/26737718/grupo/26737723": ("Preferente Juvenil Grupo 5", {"6815292"}),
    "competicion/26737724/grupo/26737735": ("Primera Juvenil Grupo 11", {"15328636"}),
    "competicion/26738323/grupo/27131269": ("Segunda Juvenil Grupo 13", {"15292622"}),
    "competicion/26737742/grupo/26737747": ("Preferente Cadete Grupo 5", {"8961562"}),
    "competicion/26737768/grupo/26737795": ("Segunda Cadete Grupo 27", {"13538232"}),
    "competicion/26737819/grupo/26737824": ("Preferente Infantil Grupo 5", {"13538235"}),
    "competicion/26738324/grupo/26738357": ("Segunda Infantil Grupo 34", {"8961563"}),
    "competicion/26738132/grupo/26738138": ("Preferente Alevín F-7 Grupo 6", {"15328645"}),
}

TZ = ZoneInfo("Europe/Madrid")
AVISO = "Información sacada de la web El Balón de Madrid"
# WhatsApp no permite poner el escudo real dentro del texto; se usa un emoji.
# Cámbialo por el que prefieras, p. ej. "⚪⚫" o "🦓" (o "" para quitarlo).
ESCUDO = ""
LINEA = "━" * 16
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# ---------------------------------------------------------------------------
# Ajustes de nombres. Edita estos diccionarios para dejar los nombres como
# quieras que salgan en el mensaje. Clave = nombre en MAYÚSCULAS, sin comillas
# ni la letra del equipo (A/B/C) y sin prefijos tipo C.D., C.F., S.A.D., A.D.
# ---------------------------------------------------------------------------
NOMBRES_EQUIPO = {
    "CIUDAD LOS ANGELES": "Ciudad de los Ángeles",
    "RACING VILLAVERDE": "Racing de Villaverde",
    "UNION 2000": "Unión 2000",
    "UNION CARRASCAL": "Unión Carrascal",
    "SANTIAGO APOSTOL VILLAVERDE": "Santiago Apóstol Villaverde",
    "TORREJON": "Torrejón",
}
NOMBRES_CAMPO = {
    # "IDB DAVID DIEZ DE LA CRUZ": "IDB David Diez de la Cruz",
}
QUITAR_PREFIJOS = {
    "C.D.", "C.F.", "S.A.D.", "A.D.", "C.D.E.", "CDE", "CDB", "C.D.B.", "ESC.MUN.FUT.",
    "U.D.", "A.D.C.", "A.J.D.C.",
}
SIGLAS = {"IDB", "CC", "II", "III", "IV", "PVO"}
MINUSCULAS = {"de", "del", "la", "las", "los", "el", "y"}
ACENTOS_TITULO = {"Alevin": "Alevín", "Benjamin": "Benjamín", "GRUPO": "Grupo"}


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------
def _palabra(m):
    p = m.group(0)
    if p.upper() in SIGLAS:
        return p.upper()
    if p.lower() in MINUSCULAS:
        return p.lower()
    return p.capitalize()


def capitalizar(texto):
    t = re.sub(r"[^\W\d_]+", _palabra, texto.lower())
    return t[:1].upper() + t[1:]


def limpiar_equipo(nombre):
    n = nombre.replace("’", "'").strip()
    m = re.search(r"'([A-Za-z])'\s*$", n)
    letra = m.group(1).upper() if m else ""
    n = re.sub(r"\s*'[A-Za-z]'\s*$", "", n)
    tokens = [t for t in n.split() if t.upper() not in QUITAR_PREFIJOS]
    base = " ".join(tokens).upper()
    base = NOMBRES_EQUIPO.get(base) or capitalizar(base)
    base = base.replace(" - ", "-")  # los patrocinadores llevan " - " y se confundirían con el guion entre equipos
    # espacio irrompible: que la letra final (A/B/C) no salte de línea suelta
    return f"{base} {letra}" if letra else base


def limpiar_campo(campo):
    c = re.sub(r"\s*\([^)]*\)", "", campo or "").strip()
    if not c:
        return ""
    if c.upper() in NOMBRES_CAMPO:
        return NOMBRES_CAMPO[c.upper()]
    return capitalizar(_separar_via(c))


def limpiar_titulo(t):
    for k, v in ACENTOS_TITULO.items():
        t = t.replace(k, v)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# Nombre del campo: separación entre el nombre corto y la vía/ubicación
#
# El nombre que da la web a veces trae el nombre corto del campo y la vía
# pegados sin ningún separador claro (p. ej. "STA ANAAVDA DE LOS ROSALES"),
# lo que al capitalizar queda ilegible ("Santa Anaavenida..."). Como no hace
# falta la dirección postal exacta, basta con detectar dónde empieza la vía
# (por palabras típicas: Avda, Calle, Plaza...) e insertar ". " delante,
# aunque en el original no hubiera ni un espacio.
# ---------------------------------------------------------------------------
_RE_VIA = re.compile(
    r"(AV(?:D)?A?\.?|AVENIDA|CALLE|C/|CTRA\.?|CARRETERA|PASEO|PSEO|PZA\.?|PLAZA|"
    r"POL(?:IGONO|ÍGONO)?\.?|GTA\.?|GLORIETA|CAMINO|CMNO\.?|RONDA|TRAVES[IÍ]A|"
    r"PASAJE|URB(?:ANIZACION|ANIZACIÓN)?\.?)(?=\s)",
    re.IGNORECASE)


def _separar_via(c):
    """Inserta ". " entre el nombre del campo y la vía cuando ambos vienen
    pegados o solo separados por un guión."""
    m = _RE_VIA.search(c)
    if not m or m.start() == 0:
        return c
    antes = c[:m.start()].rstrip(" -,")
    despues = c[m.start():]
    return f"{antes}. {despues}" if antes else c


# ---------------------------------------------------------------------------
# Descarga y análisis
# ---------------------------------------------------------------------------
def get(url, intentos=4):
    """Descarga una página; reintenta si la web devuelve un error temporal (5xx)."""
    ultimo = None
    for i in range(intentos):
        time.sleep(PAUSA if i == 0 else 4 * i)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code < 500:
                r.raise_for_status()
                return BeautifulSoup(r.content, "html.parser")
            ultimo = requests.HTTPError(f"error {r.status_code} en {url}")
        except (requests.ConnectionError, requests.Timeout) as e:
            ultimo = e
    raise ultimo


def grupos_a_consultar(errores):
    """Devuelve ({url_jornadas: (nombre o None, {ids de equipos del club})},
    {id de equipo: (nombre, categoría)} tal como figuran en la ficha del club)."""
    grupos, conocidos, equipos_club = {}, set(), {}
    for ruta, (nombre, ids) in CONOCIDOS.items():
        grupos[f"{BASE}/{ruta}/jornadas"] = (nombre, set(ids))
        conocidos |= ids

    # Equipos del club que no están en la lista anterior: se buscan en la web.
    try:
        club = get(CLUB_URL)
    except Exception as e:
        errores.append("ficha del club (no se han buscado equipos nuevos)")
        print(f"Aviso: {e}", file=sys.stderr)
        return grupos, equipos_club
    nuevos = set()
    for a in club.find_all("a", href=True):
        m = re.search(r"/equipo/(\d+)/?$", a["href"])
        if m:
            textos = list(a.stripped_strings)
            if textos:
                equipos_club[m.group(1)] = (textos[0], textos[1] if len(textos) > 1 else "")
            if m.group(1) not in conocidos:
                nuevos.add(m.group(1))
    for eid in sorted(nuevos):
        try:
            pag = get(f"{BASE}/equipo/{eid}")
        except Exception as e:
            errores.append(f"equipo {eid}")
            print(f"Aviso: {e}", file=sys.stderr)
            continue
        for a in pag.find_all("a", href=True):
            if re.search(r"/competicion/\d+/grupo/\d+/clasificacion", a["href"]):
                url = urllib.parse.urljoin(BASE, a["href"]).replace("/clasificacion", "/jornadas")
                nombre, ids = grupos.get(url, (None, set()))
                ids.add(eid)
                grupos[url] = (nombre, ids)
                break
    return grupos, equipos_club


FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
HORA = re.compile(r"^(\d{2}:\d{2}|--:--)$")
RESULTADO = re.compile(r"^(\d{1,2})\s*[·–\-]\s*(\d{1,2})$")
JORNADA = re.compile(r"^Jornada\s*(\d{1,2})\s*(?:Actual)?\s*\|?\s*(\d{2}-\d{2}-\d{4})$")


def ahora():
    return datetime.now(TZ)


def normalizar(soup):
    """La web (React) puede partir '4·1' en trozos y comentarios: los unimos."""
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for tag in soup.find_all(["span", "b", "strong", "i", "em", "small", "time"]):
        tag.unwrap()
    if hasattr(soup, "smooth"):
        soup.smooth()
    return soup


def parsear_jornada(soup):
    """Recorre la página en orden y reconstruye los partidos:
    fecha -> local -> hora (o marcador) -> visitante -> campo -> 'Ver acta' (fin)."""
    normalizar(soup)
    partidos, fecha, actual = [], None, None
    jornada, esperando_numero = None, False

    def nuevo():
        return {"fecha": fecha, "jornada": jornada, "hora": None, "resultado": None,
                "equipos": [], "campo": "", "acta": None}

    for nodo in soup.descendants:
        if isinstance(nodo, NavigableString):
            if nodo.parent is not None and nodo.parent.name in ("script", "style"):
                continue
            t = str(nodo).strip()
            mj = JORNADA.match(t)
            if mj:  # cabecera de la jornada mostrada, p. ej. "Jornada3 27-09-2026"
                jornada = int(mj.group(1))
            elif t == "Jornada":
                esperando_numero = True
            elif esperando_numero and t.isdigit():
                jornada, esperando_numero = int(t), False
            elif FECHA.match(t):
                fecha = datetime.strptime(t, "%d/%m/%Y").date()
            elif HORA.match(t):
                actual = actual or nuevo()
                actual["hora"] = None if t == "--:--" else t
            else:
                m = RESULTADO.match(t)
                if m and actual is not None and len(actual["equipos"]) == 1:
                    actual["resultado"] = (int(m.group(1)), int(m.group(2)))
        elif getattr(nodo, "name", None) == "a" and nodo.get("href"):
            h = nodo["href"]
            texto = nodo.get_text(" ", strip=True)
            m = re.search(r"/equipo/(-?\d+)", h)
            if m:
                if actual is not None and len(actual["equipos"]) >= 2:
                    partidos.append(actual)  # el partido anterior no tenía enlace al acta
                    actual = None
                actual = actual or nuevo()
                actual["equipos"].append((m.group(1), texto))
            elif "/campo/" in h and "google" not in h:
                if actual is not None:
                    actual["campo"] = texto
            elif "/acta/" in h:
                if actual is not None and len(actual["equipos"]) >= 2:
                    actual["acta"] = urllib.parse.urljoin(BASE, h)
                    partidos.append(actual)
                actual = None
    if actual is not None and len(actual["equipos"]) >= 2:
        partidos.append(actual)
    return partidos


# ---------------------------------------------------------------------------
# Goleadores y tarjetas de un acta ya jugada.
#
# Esto ya NO se basa en adivinar patrones de texto: se ha inspeccionado el HTML real que
# guarda el navegador de una acta ya jugada (partido Club Arganda Futsal A 4-5 Futsala
# Villaverde A, Juvenil J2, comprobado a mano por César el 28/09/2026) y la estructura es
# siempre esta:
#
#   - El nombre de cada equipo (enlace <a href="/equipo/ID">) NO se repite junto a cada gol
#     o tarjeta: en toda la página solo aparece 2 veces, en la cabecera del marcador (el
#     primer enlace es el equipo LOCAL, el segundo el VISITANTE).
#   - Las secciones "Goles" y "Tarjetas" son, cada una, un <h2> seguido de un <div> de DOS
#     columnas (una rejilla de 2 columnas): la primera es la del equipo local y la segunda
#     la del visitante (mismo orden que la cabecera). Cada columna es un <ul> con un <li>
#     por gol/tarjeta, o un simple "—" cuando ese equipo no tiene ninguno en esa sección.
#   - Cada <li> de gol trae el minuto y, en un enlace, el nombre del jugador.
#   - Cada <li> de tarjeta trae el minuto, uno o dos iconos (con aria-label indicando si es
#     amarilla, roja o "segunda amarilla y expulsión") y el nombre del jugador.
#
# El método anterior (ir guardando el último nombre de equipo visto en el texto, según se
# iba leyendo la página de arriba a abajo) es el que causaba que TODOS los goles y tarjetas
# del partido salieran atribuidos a un único equipo: como el nombre de equipo solo aparece
# una vez cada uno y muy lejos (al principio de la página) de las listas de goles/tarjetas,
# ese "último equipo visto" se quedaba fijo en el segundo (visitante) durante toda la
# lectura. Ahora se usa directamente la columna a la que pertenece cada <li>, así que no
# hace falta adivinar nada.
#
# IMPORTANTE: `normalizar()` (más abajo) deshace los <span> de toda la página para poder
# leerla como texto plano, y eso destruye justo la estructura que se necesita aquí (los
# <span> del minuto y de los iconos de tarjeta). Por eso esta función NO debe recibir un
# soup que ya haya pasado por `normalizar()` (por ejemplo, el que ya haya usado
# `datos_de_acta`): quien la llame debe pasarle una copia del soup tal cual se descargó.
# ---------------------------------------------------------------------------
_RE_PENALTI = re.compile(r"penalt", re.IGNORECASE)
# gol en propia puerta: el acta lo apunta bajo el jugador que lo marca (columna de SU equipo),
# pero el gol cuenta para el equipo CONTRARIO -si no se tiene esto en cuenta, el marcador que
# sale de sumar los goles de cada columna no cuadra con el resultado real del partido.
_RE_PROPIA = re.compile(r"en\s+propia", re.IGNORECASE)
# una tarjeta cuenta como "roja" tanto si es roja directa como si es una segunda amarilla con
# expulsión (el icono de "segunda amarilla y expulsión" incluye un cuadro rojo y la palabra
# "expulsión" en su título, aunque el texto visible del jugador solo diga "amarilla").
_RE_TARJETA_ROJA = re.compile(r"roja|expulsi[oó]n", re.IGNORECASE)


def _texto_o_vacio(nodo):
    return nodo.get_text(" ", strip=True) if nodo is not None else ""


def _columnas_de_seccion(soup, titulo):
    """Busca la sección <h2>titulo</h2> (Goles o Tarjetas) y devuelve (columna_local,
    columna_visitante), cada una la lista de <li> de esa columna. Si la página no tiene la
    forma esperada (dos columnas justo después del título), devuelve ([], []) para no
    arriesgarse a leer mal los datos."""
    h2 = soup.find("h2", string=lambda s: bool(s) and s.strip().lower() == titulo)
    if h2 is None:
        return [], []
    contenedor = h2.find_next_sibling()
    if contenedor is None:
        return [], []
    columnas = contenedor.find_all(recursive=False)
    if len(columnas) != 2:
        return [], []

    def items(col):
        return col.find_all("li", recursive=False) if col.name == "ul" else []

    return items(columnas[0]), items(columnas[1])


def goles_y_tarjetas_de_acta(soup, marcador=None):
    """(goles, tarjetas) de un acta ya jugada. Cada gol: {minuto, jugador, equipo,
    penalti, propia}; cada tarjeta: {minuto, jugador, equipo, tipo}. `equipo` es el
    nombre de equipo tal como aparece en el acta (sin limpiar todavía) AL QUE SE LE
    CUENTA el gol -en uno en propia puerta, es el contrario del equipo de `jugador`,
    para que sumar los goles por equipo cuadre con el resultado real.

    `soup` debe ser una copia del acta SIN pasar por `normalizar()` (ver aviso arriba)."""
    nombres_equipo, vistos = [], set()
    for a in soup.find_all("a", href=re.compile(r"/equipo/\d+")):
        nombre = a.get_text(" ", strip=True)
        if nombre and nombre not in vistos:
            vistos.add(nombre)
            nombres_equipo.append(nombre)
    if len(nombres_equipo) < 2:
        return [], []
    equipo_local, equipo_visitante = nombres_equipo[0], nombres_equipo[1]

    goles, tarjetas = [], []

    col_local, col_visitante = _columnas_de_seccion(soup, "goles")
    for equipo_col, equipo_rival, columna in (
            (equipo_local, equipo_visitante, col_local),
            (equipo_visitante, equipo_local, col_visitante)):
        for li in columna:
            m = re.search(r"\d+", _texto_o_vacio(li.find("span")))
            a = li.find("a")
            jugador = _texto_o_vacio(a)
            if not m or not jugador:
                continue
            texto_li = _texto_o_vacio(li)
            propia = bool(_RE_PROPIA.search(texto_li))
            goles.append({"minuto": int(m.group()), "jugador": jugador,
                          "equipo": equipo_rival if propia else equipo_col,
                          "penalti": bool(_RE_PENALTI.search(texto_li)), "propia": propia})

    col_local, col_visitante = _columnas_de_seccion(soup, "tarjetas")
    for equipo, columna in ((equipo_local, col_local), (equipo_visitante, col_visitante)):
        for li in columna:
            m = re.search(r"\d+", _texto_o_vacio(li.find("span")))
            a = li.find("a")
            jugador = _texto_o_vacio(a)
            if not m or not jugador:
                continue
            # el icono de "segunda amarilla y expulsión" pinta un cuadro amarillo y otro rojo,
            # pero el rojo va sin aria-label (aria-hidden="true"); por eso se mira también la
            # clase CSS (bg-tarjeta-roja) y el título ("expulsión"), no solo el aria-label.
            pistas = []
            for s in li.find_all("span"):
                if s.get("title"):
                    pistas.append(s.get("title"))
                if s.get("aria-label"):
                    pistas.append(s.get("aria-label"))
                pistas.append(" ".join(s.get("class", [])))
            tipo = "roja" if _RE_TARJETA_ROJA.search(" ".join(pistas)) else "amarilla"
            tarjetas.append({"minuto": int(m.group()), "jugador": jugador, "equipo": equipo, "tipo": tipo})

    if marcador is not None:
        esperados = sum(marcador)
        if esperados and len(goles) != esperados:
            print(f"Aviso: acta con marcador {marcador[0]}-{marcador[1]} pero se han "
                  f"reconocido {len(goles)} goles en la web — revisar el patrón de lectura.",
                  file=sys.stderr)
    return goles, tarjetas


def escudos_de_acta(soup):
    """(escudo_local, escudo_visitante): URL de la imagen del escudo de cada equipo, tal como
    viene en la cabecera del marcador del acta: el mismo <a href="/equipo/ID"> que ya se usa
    para leer el nombre de cada equipo trae también, dentro, su escudo en un <img> -no hace
    falta ninguna petición extra. Se usa el mismo criterio que en goles_y_tarjetas_de_acta
    (los dos primeros equipos distintos que aparecen en la página) para que el orden (local,
    visitante) sea siempre el mismo que el de los goles/tarjetas. Si el acta no trae imagen
    para alguno de los dos, se deja en None (la web ya cae entonces al círculo genérico)."""
    urls, vistos = [], set()
    for a in soup.find_all("a", href=re.compile(r"/equipo/\d+")):
        nombre = a.get_text(" ", strip=True)
        if not nombre or nombre in vistos:
            continue
        vistos.add(nombre)
        img = a.find("img")
        src = (img.get("src") or img.get("data-src")) if img else None
        urls.append(urllib.parse.urljoin(BASE, src) if src else None)
        if len(urls) == 2:
            break
    while len(urls) < 2:
        urls.append(None)
    return urls[0], urls[1]


def _lado(nombre_acta, e):
    """'local' o 'visitante' según a qué equipo del partido corresponde `nombre_acta` (tal
    como viene escrito en el acta), comparando cuántas palabras tiene en común con el nombre
    de cada equipo guardado en el estado (los nombres pueden venir escritos de forma algo
    distinta entre el acta y el calendario)."""
    def puntos(nombre_partido):
        a, b = set(_sin_acentos(nombre_acta).split()), set(_sin_acentos(nombre_partido).split())
        return len(a & b)
    return "local" if puntos(e["local"][1]) >= puntos(e["visitante"][1]) else "visitante"


def _con_lado(eventos, e):
    return [{**ev, "lado": _lado(ev["equipo"], e)} for ev in eventos]


def datos_de_acta(soup):
    """(marcador, hora) de un acta. El marcador se da en cuanto la web deja de marcar el
    partido como "POR JUGAR", aunque el acta todavía no tenga las alineaciones completas: el
    árbitro puede tardar más en firmar el acta entera que en quedar reflejado el resultado."""
    normalizar(soup)
    textos = [t.strip() for t in soup.find_all(string=True)
              if t.parent is not None and t.parent.name not in ("script", "style")]
    marcador = None
    if not any(t and "POR JUGAR" in t.upper() for t in textos):
        for t in textos:
            m = RESULTADO.match(t)
            if m:
                marcador = (int(m.group(1)), int(m.group(2)))
                break
    hora = None
    m = re.search(r"\d{2}-\d{2}-\d{4}\s*(\d{2}:\d{2})", " ".join(textos))
    if m:
        hora = m.group(1)
    return marcador, hora


def resultado_de_acta(soup):
    return datos_de_acta(soup)[0]


def temporada_finalizada(soup):
    """La web avisa cuando se está viendo una temporada antigua ('ya finalizada')."""
    return any("ya finalizada" in str(t) for t in soup.find_all(string=True))


def _entero(texto):
    try:
        return int(texto.replace("+", "").replace("\u2212", "-").strip())
    except ValueError:
        return 0


NUMERO = re.compile(r"^[+\-\u2212]?\d+$")


def _escudo_de(enlace):
    """URL del escudo dentro de un <a href="/equipo/ID"> (fila de la clasificación o del
    marcador de un acta), o None si esa fila no trae imagen."""
    img = enlace.find("img")
    src = (img.get("src") or img.get("data-src")) if img else None
    return urllib.parse.urljoin(BASE, src) if src else None


def _fila_generica(enlace):
    """Lee una fila aunque no sea una <tr>: sube desde el enlace del equipo hasta el
    contenedor más grande que solo tenga ese equipo y lee sus textos en orden."""
    patron = re.compile(r"/equipo/\d+")
    fila, nodo = None, enlace
    for _ in range(7):
        nodo = nodo.parent
        if nodo is None or len(nodo.find_all("a", href=patron)) != 1:
            break
        fila = nodo
    if fila is None:
        return None
    textos = [t.strip() for t in fila.stripped_strings if t.strip()]
    nombre = enlace.get_text(" ", strip=True)
    try:
        i = next(k for k, t in enumerate(textos) if t and t in nombre)
    except StopIteration:
        return None
    if not i or not textos[0].isdigit():
        return None
    nums = [t for t in textos[i + 1:] if NUMERO.match(t)]
    if len(nums) < 8:
        return None
    ident = re.search(r"/equipo/(\d+)", enlace["href"]).group(1)
    v = [_entero(x) for x in nums[:8]]
    return {"pos": int(textos[0]), "id": ident, "nombre": limpiar_equipo(nombre),
            "nuestro": _es_nuestro((ident, nombre)), "escudo": _escudo_de(enlace),
            "pj": v[0], "g": v[1], "e": v[2], "p": v[3], "gf": v[4], "gc": v[5], "dg": v[6], "pts": v[7]}


def tabla_de(soup):
    """Tabla de clasificación: (jornada, [filas]). Cada fila lleva posición, equipo,
    PJ, G, E, P, GF, GC, DG y puntos."""
    original = BeautifulSoup(str(soup), "html.parser")  # sin unir textos, para el plan B
    normalizar(soup)
    jornada = cabecera_jornada(soup)
    filas = []
    for tr in soup.find_all("tr"):
        enlace = tr.find("a", href=re.compile(r"/equipo/\d+"))
        if not enlace:
            continue
        celdas = tr.find_all(["td", "th"])
        textos = [c.get_text(" ", strip=True) for c in celdas]
        idx = next((i for i, c in enumerate(celdas)
                    if c.find("a", href=re.compile(r"/equipo/\d+"))), None)
        if idx is None or not textos or not textos[0].isdigit() or idx + 7 >= len(textos):
            continue
        ident = re.search(r"/equipo/(\d+)", enlace["href"]).group(1)
        bruto = enlace.get_text(" ", strip=True)
        filas.append({
            "pos": int(textos[0]), "id": ident,
            "nombre": limpiar_equipo(bruto), "nuestro": _es_nuestro((ident, bruto)),
            "escudo": _escudo_de(enlace),
            "pj": _entero(textos[idx + 1]), "g": _entero(textos[idx + 2]),
            "e": _entero(textos[idx + 3]), "p": _entero(textos[idx + 4]),
            "gf": _entero(textos[idx + 5]), "gc": _entero(textos[idx + 6]),
            "dg": _entero(textos[idx + 7]), "pts": _entero(textos[-1]),
        })
    if not filas:  # plan B: la tabla no es una <table> normal
        vistos = set()
        for enlace in original.find_all("a", href=re.compile(r"/equipo/\d+")):
            f = _fila_generica(enlace)
            if f and f["id"] not in vistos:
                vistos.add(f["id"])
                filas.append(f)
    return jornada, filas


def cabecera(soup):
    """(número, fecha) de la jornada que muestra la página; la fecha puede faltar."""
    normalizar(soup)
    esperando = False
    for t in soup.find_all(string=True):
        if t.parent is not None and t.parent.name in ("script", "style"):
            continue
        txt = str(t).strip()
        mj = JORNADA.match(txt)
        if mj:
            return int(mj.group(1)), datetime.strptime(mj.group(2), "%d-%m-%Y").date()
        if txt == "Jornada":
            esperando = True
        elif esperando and txt.isdigit():
            return int(txt), None
    return None, None


def cabecera_jornada(soup):
    return cabecera(soup)[0]


def _con_fecha(p, fecha_jornada):
    """Los partidos sin día asignado se colocan provisionalmente en la fecha de su jornada."""
    if p["fecha"] is None and fecha_jornada is not None:
        p = dict(p, fecha=fecha_jornada)
    return p


def inicio_temporada():
    """1 de julio de la temporada en curso: todo lo anterior es de la temporada pasada."""
    hoy = ahora().date()
    return date(hoy.year if hoy.month >= 7 else hoy.year - 1, 7, 1)


def titulo_grupo(soup):
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return limpiar_titulo(h1.get_text(" ", strip=True))
    t = soup.title.get_text() if soup.title else ""
    m = re.search(r"resultados (.+?) \d{4}/\d{2}", t)
    return limpiar_titulo(m.group(1)) if m else "Partido"


# ---------------------------------------------------------------------------
# Estado: partidos vistos (para poder dar el resultado aunque la web ya haya
# pasado a la jornada siguiente)
# ---------------------------------------------------------------------------
def cargar_estado(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        datos = {}
    meta = datos.get("meta", {})
    meta.setdefault("hechas", {})
    return datos.get("partidos", {}), datos.get("clasificaciones", {}), meta


def guardar_estado(ruta, partidos, clasificaciones, meta):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"partidos": partidos, "clasificaciones": clasificaciones, "meta": meta},
                  f, ensure_ascii=False, indent=1, sort_keys=True)


def clave(p):
    m = re.search(r"/acta/(\d+)", p["acta"] or "")
    return m.group(1) if m else f"{p['fecha']}|{p['equipos'][0][0]}|{p['equipos'][1][0]}"


def actualizar_estado(estado, p, titulo, url, posiciones=None):
    k = clave(p)
    anterior = estado.get(k, {})
    resultado = list(p["resultado"]) if p["resultado"] else anterior.get("resultado")

    # Posiciones en la tabla ANTES de la jornada: se actualizan hasta que empieza
    # el fin de semana del partido y después se congelan.
    pos = anterior.get("pos")
    inicio = p["fecha"] - timedelta(days=1) if p["fecha"].weekday() == 6 else p["fecha"]
    congelado = bool(resultado) or (pos and ahora().date() >= inicio)
    if posiciones and not congelado:
        nuevo = [posiciones.get(p["equipos"][0][0]), posiciones.get(p["equipos"][1][0])]
        if any(nuevo):
            pos = nuevo

    estado[k] = {
        "fecha": p["fecha"].isoformat(),
        "jornada": p.get("jornada") or anterior.get("jornada"),
        "hora": p["hora"] or anterior.get("hora"),
        "grupo": titulo,
        "url_grupo": url,
        "campo": p["campo"] or anterior.get("campo", ""),
        "local": list(p["equipos"][0]),
        "visitante": list(p["equipos"][1]),
        "resultado": resultado,
        "pos": pos,
        "acta": p["acta"] or anterior.get("acta"),
    }


def _ya_toca_mirar_acta(e, t):
    f = date.fromisoformat(e["fecha"])
    if f < t.date():
        return True
    if f > t.date():
        return False
    if e.get("hora"):
        h, m = map(int, e["hora"].split(":"))
        return t >= datetime(f.year, f.month, f.day, h, m, tzinfo=TZ) + timedelta(hours=2)
    return t.hour >= 21


def completar_con_actas(estado, errores):
    """Lee las actas para completar el marcador (partidos ya jugados), la hora (partidos de
    los que solo se conoce el marcador) y, cuando ya hay marcador, los goleadores y tarjetas."""
    t = ahora()
    pendientes = []
    for e in estado.values():
        if not e.get("acta"):
            continue
        dias = (t.date() - date.fromisoformat(e["fecha"])).days
        sin_marcador = not e.get("resultado") and dias <= 10 and _ya_toca_mirar_acta(e, t)
        sin_hora = bool(e.get("resultado")) and not e.get("hora") and dias >= 0
        sin_goles = bool(e.get("resultado")) and not e.get("goles") and dias <= 10 and _ya_toca_mirar_acta(e, t)
        # el escudo va en la cabecera del marcador de cualquier acta, se haya jugado ya el
        # partido o no ("POR JUGAR"), así que no hace falta esperar a que toque mirarla como
        # con el marcador/goles: así ya sale en la pestaña "Partidos", antes de jugarse. Y, a
        # diferencia del marcador o los goles, el escudo no caduca con el tiempo (el equipo es
        # el mismo se mire cuando se mire la acta), así que tampoco tiene sentido limitarlo a
        # partidos de los últimos 10 días: si se hiciera, cualquier partido que ya llevara más
        # de 10 días en estado.json antes de que este código se desplegara (como ha pasado con
        # el CDCA, que arrastraba partidos desde el 12/09) se quedaría sin escudo para siempre.
        sin_escudo = not e.get("escudo_local") and not e.get("escudo_visitante")
        if sin_marcador or sin_hora or sin_goles or sin_escudo:
            pendientes.append(e)
    for e in pendientes[:25]:
        try:
            soup = get(e["acta"])
            # goles_y_tarjetas_de_acta necesita el soup tal cual, sin pasar por normalizar()
            # (que deshace los <span> del minuto y de los iconos de tarjeta), así que se
            # guarda una copia intacta ANTES de que datos_de_acta lo normalice.
            soup_crudo = copy.deepcopy(soup)
            marcador, hora = datos_de_acta(soup)
        except Exception as ex:
            print(f"Aviso: no se pudo leer un acta: {ex}", file=sys.stderr)
            continue
        if marcador and not e.get("resultado"):
            e["resultado"] = list(marcador)
        if hora and not e.get("hora"):
            e["hora"] = hora
        if not e.get("escudo_local") and not e.get("escudo_visitante"):
            try:
                esc_local, esc_visitante = escudos_de_acta(soup_crudo)
                if esc_local or esc_visitante:
                    e["escudo_local"], e["escudo_visitante"] = esc_local, esc_visitante
            except Exception as ex:
                print(f"Aviso: no se pudieron leer los escudos de un acta: {ex}", file=sys.stderr)
        if e.get("resultado") and not e.get("goles"):
            try:
                goles, tarjetas = goles_y_tarjetas_de_acta(soup_crudo, tuple(e["resultado"]))
                # solo se guarda si de verdad se ha encontrado algo: el acta puede tener ya el
                # marcador pero todavía no la sección de goles/tarjetas (el árbitro puede
                # rellenar el acta por partes), así que si sale vacío se reintenta en la
                # siguiente ejecución en vez de darlo por hecho para siempre
                if goles or tarjetas:
                    e["goles"] = _con_lado(goles, e)
                    e["tarjetas"] = _con_lado(tarjetas, e)
            except Exception as ex:
                print(f"Aviso: no se pudieron leer goleadores/tarjetas de un acta: {ex}", file=sys.stderr)


def completar_pendientes_con_navegador(estado, errores):
    """Último recurso para partidos que ya deberían tener resultado según su hora, pero el
    acta todavía no lo da: la web puede marcar el partido como acabado y dar el marcador en
    la propia jornada bastante antes de que el árbitro firme el acta completa (con las
    alineaciones). Se abre un navegador automático, se hace clic en la jornada exacta de cada
    partido pendiente -tal y como haría una persona- y se lee el resultado de ahí. Solo se usa
    cuando de verdad hace falta (partidos realmente pendientes), para no abrir el navegador sin
    necesidad; si Playwright no está instalado, se avisa y se sigue sin él."""
    t = ahora()
    pendientes_por_grupo = {}
    for e in estado.values():
        if e.get("resultado") or not e.get("jornada") or not e.get("url_grupo"):
            continue
        if not _ya_toca_mirar_acta(e, t):
            continue
        pendientes_por_grupo.setdefault(e["url_grupo"], set()).add(e["jornada"])
    if not pendientes_por_grupo:
        return

    try:
        nav = NavegadorPlaywright()
    except Exception as ex:
        print(f"Aviso: no se ha podido abrir el navegador para completar resultados "
              f"pendientes: {ex}", file=sys.stderr)
        return
    try:
        for url, jornadas in pendientes_por_grupo.items():
            titulo = next((e["grupo"] for e in estado.values() if e.get("url_grupo") == url), url)
            try:
                nav.abrir(url)
                inicial = None
                for _ in range(60):
                    inicial = BeautifulSoup(nav.html(), "html.parser")
                    if cabecera(inicial)[0]:
                        break
                    nav.pausa(0.5)
                if not cabecera(inicial)[0]:
                    print(f"Aviso: navegador (pendientes): {titulo}: la página no llegó a "
                          f"mostrar ninguna jornada.", file=sys.stderr)
                    continue
            except Exception as ex:
                errores.append(titulo)
                print(f"Aviso: navegador (pendientes): no se pudo abrir {titulo}: {ex}", file=sys.stderr)
                continue
            firma_previa = _firma(inicial)
            encontrados = 0
            for j in sorted(jornadas):
                try:
                    if not nav.ir_a(j):
                        continue
                    pagina_j = _esperar_jornada(nav, j, firma_previa)
                except Exception as ex:
                    print(f"Aviso: navegador (pendientes): {titulo}, jornada {j}: {ex}", file=sys.stderr)
                    continue
                if pagina_j is None:
                    continue
                firma_previa = _firma(pagina_j)
                _, fecha_j = cabecera(pagina_j)
                for p in parsear_jornada(pagina_j):
                    p = _con_fecha(p, fecha_j)
                    if not p.get("resultado"):
                        continue
                    # Se busca la entrada pendiente que YA existía (por grupo + jornada +
                    # equipos) y se actualiza esa misma, en vez de dejar que actualizar_estado()
                    # calcule su propia clave: si esta vez la página trae un dato que la primera
                    # vez no tenía (p. ej. el enlace al acta), la clave saldría distinta y se
                    # crearía un duplicado en lugar de completar el partido pendiente.
                    ids_partido = {p["equipos"][0][0], p["equipos"][1][0]}
                    objetivo = next((e for e in estado.values()
                                     if e.get("url_grupo") == url and e.get("jornada") == j
                                     and {e["local"][0], e["visitante"][0]} == ids_partido), None)
                    if objetivo is None:
                        continue
                    objetivo["resultado"] = list(p["resultado"])
                    if p.get("hora") and not objetivo.get("hora"):
                        objetivo["hora"] = p["hora"]
                    if p.get("campo") and not objetivo.get("campo"):
                        objetivo["campo"] = p["campo"]
                    if p.get("acta") and not objetivo.get("acta"):
                        objetivo["acta"] = p["acta"]
                    encontrados += 1
            if encontrados:
                print(f"Navegador (pendientes): {titulo}: {encontrados} resultado(s) recogidos "
                      f"que el acta todavía no daba.", file=sys.stderr)
    finally:
        nav.cerrar()


# ---------------------------------------------------------------------------
# Jornadas anteriores (histórico)
#
# La web no cambia de dirección al elegir otra jornada, así que no se puede pedir
# directamente. Lo que sí tiene cada partido es su acta, y las actas de un grupo suelen
# tener números correlativos. Se leen hacia atrás las actas anteriores a la jornada actual
# y solo se guardan las que confirman ser de una jornada anterior y de equipos del grupo.
# ---------------------------------------------------------------------------
MAX_RELLENO = 100  # actas (pasadas y futuras) que se leen por ejecución
MAX_SEGUIDOS = 12  # actas seguidas que pueden fallar antes de dar por terminado un recorrido
VERSION_HISTORICO = 3  # al subirla, se reintentan los recorridos que acabaron sin encontrar nada


def _sin_acentos(texto):
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode().upper()


def leer_acta(soup, url_acta):
    """Datos de un acta o None si no se reconoce su estructura."""
    normalizar(soup)
    textos = [t.strip() for t in soup.find_all(string=True)
              if t.parent is not None and t.parent.name not in ("script", "style") and t.strip()]
    plano = " ".join(textos)
    cab = re.search(r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 \-\.]*?)\s*·\s*Grupo\s+(\d+)\s*·\s*Jornada\s+(\d+)",
                    plano, re.I)
    fecha_hora = re.search(r"(\d{2}-\d{2}-\d{4})\s*(\d{2}:\d{2})?", plano)
    equipos = []
    for a in soup.find_all("a", href=re.compile(r"/equipo/\d+")):
        ident = re.search(r"/equipo/(\d+)", a["href"]).group(1)
        if ident not in [e[0] for e in equipos]:
            equipos.append((ident, a.get_text(" ", strip=True)))
    if not cab or not fecha_hora or len(equipos) < 2:
        return None
    marcador, _ = datos_de_acta(soup)
    campo = soup.find("a", href=re.compile(r"/campo/\d+"))
    return {
        "categoria": cab.group(1).strip(), "grupo_n": int(cab.group(2)), "jornada": int(cab.group(3)),
        "fecha": datetime.strptime(fecha_hora.group(1), "%d-%m-%Y").date(),
        "hora": None if fecha_hora.group(2) in (None, "00:00") else fecha_hora.group(2),
        "equipos": equipos[:2], "resultado": marcador,
        "campo": campo.get_text(" ", strip=True) if campo else "", "acta": url_acta,
    }


def _bloque_correlativo(numeros):
    """(primero, cuántos) del tramo de números consecutivos más largo."""
    mejor, actual = (0, 0), None
    for n in sorted(set(numeros)):
        if actual and n == actual[0] + actual[1]:
            actual = (actual[0], actual[1] + 1)
        else:
            actual = (n, 1)
        if actual[1] > mejor[1]:
            mejor = actual
    return mejor


def _leer(numero, comp, grupo):
    """(info, motivo, no_existe) de un acta."""
    url_acta = f"{BASE}/acta/{numero}?temporada=22&competicion={comp}&grupo={grupo}"
    try:
        info = leer_acta(get(url_acta), url_acta)
    except Exception as e:
        if "404" not in str(e):
            return None, f"no se pudo descargar ({e})", False
        # algunas actas solo se encuentran sin indicar competición y grupo: se reintenta así
        url_acta = f"{BASE}/acta/{numero}?temporada=22"
        try:
            info = leer_acta(get(url_acta), url_acta)
        except Exception as e2:
            return None, f"no se pudo descargar ({e2})", "404" in str(e2)
    return info, ("" if info else "estructura no reconocida"), False


def rellenar_jornadas(url, ids, titulo, actual, actas_actuales, equipos_grupo, estado, meta, cupo):
    """Recorre hacia atrás las actas anteriores a las de la jornada actual y guarda los
    partidos de nuestros equipos. Cada acta se acepta solo si es de una jornada anterior y
    sus dos equipos pertenecen al grupo. Hay actas que no existen (404): se saltan. Recuerda
    por dónde iba para continuar en la siguiente ejecución. Devuelve cuántas actas ha leído."""
    terminado = meta.setdefault("terminado", {})
    cursor = meta.setdefault("cursor", {})
    base, n = _bloque_correlativo(actas_actuales)
    m = re.search(r"/competicion/(\d+)/grupo/(\d+)", url)
    if terminado.get(url) or actual <= 1 or n < 2 or not m or cupo <= 0:
        return 0
    comp, grupo = m.groups()
    numero = cursor.get(url, base - 1)
    piso = base - (actual + 1) * n  # no se busca más allá de una jornada de margen
    print(f"Histórico de {titulo}: jornada actual {actual}, actas {base}-{base + n - 1}; "
          f"se sigue desde el acta {numero}.", file=sys.stderr)
    encontrados, gastadas, seguidos_mal, inexistentes, avisos = {}, 0, 0, 0, 0
    while numero > max(piso, 0) and gastadas < cupo:
        gastadas += 1
        info, motivo, no_existe = _leer(numero, comp, grupo)
        if info:
            if info["jornada"] >= actual:
                motivo = f"es de la jornada {info['jornada']}"
            elif not all(e[0] in equipos_grupo for e in info["equipos"]):
                motivo = "sus equipos no son de este grupo"
        if motivo:
            seguidos_mal += 1
            if no_existe:
                inexistentes += 1
            elif avisos < 5:
                avisos += 1
                print(f"Aviso: histórico de {titulo}: acta {numero}: {motivo}.", file=sys.stderr)
            if seguidos_mal >= MAX_SEGUIDOS:
                terminado[url] = True  # ya no hay actas que encajen: no se sigue
                break
        else:
            seguidos_mal = 0
            encontrados.setdefault(info["jornada"], []).append(info)
            if info["jornada"] == 1 and len(encontrados[1]) >= n:
                numero -= 1
                terminado[url] = True  # ya se ha llegado al principio de la temporada
                break
        numero -= 1
    cursor[url] = numero
    if numero <= max(piso, 0):
        terminado[url] = True
    for infos in encontrados.values():
        for info in infos:
            if any(e[0] in ids for e in info["equipos"]):
                actualizar_estado(estado, {
                    "fecha": info["fecha"], "jornada": info["jornada"], "hora": info["hora"],
                    "resultado": info["resultado"], "equipos": info["equipos"],
                    "campo": info["campo"], "acta": info["acta"]}, titulo, url, None)
    if gastadas:
        print(f"Histórico de {titulo}: leídas {gastadas} actas ({inexistentes} no existen), "
              f"jornadas encontradas: {sorted(encontrados)}.", file=sys.stderr)
    return gastadas


VENTANA = 4  # cuántas jornadas por delante de la actual se miran


def explorar_proximas(url, ids, titulo, actual, actas_actuales, equipos_grupo, estado, meta, cupo):
    """Lee las actas de las jornadas siguientes a la actual para conocer cuándo juega cada
    equipo, aunque el día y la hora aún no estén confirmados. Se repite cada 6 días para
    recoger cambios. Devuelve cuántas actas ha leído."""
    seguimiento = meta.setdefault("proximas", {})
    est = seguimiento.get(url)
    hoy = ahora().date()
    base, n = _bloque_correlativo(actas_actuales)
    m = re.search(r"/competicion/(\d+)/grupo/(\d+)", url)
    if n < 2 or not m or cupo <= 0:
        return 0
    if est and est["actual"] == actual and est.get("cursor") is None \
            and (hoy - date.fromisoformat(est["fecha"])).days < 6:
        return 0  # ya explorado hace poco
    if est and est["actual"] == actual and est.get("cursor"):
        numero = est["cursor"]  # continuar donde se dejó
    else:
        numero = base + n       # empezar justo después de la jornada actual
    comp, grupo = m.groups()
    tope = base + n * (VENTANA + 1) + 3
    gastadas, seguidos_mal, jornadas, terminado, inexistentes, avisos = 0, 0, set(), False, 0, 0
    while numero < tope and gastadas < cupo:
        gastadas += 1
        info, motivo, no_existe = _leer(numero, comp, grupo)
        if info:
            if info["jornada"] <= actual:
                motivo = f"es de la jornada {info['jornada']}"
            elif not all(e[0] in equipos_grupo for e in info["equipos"]):
                motivo = "sus equipos no son de este grupo"
            elif info["jornada"] > actual + VENTANA:
                terminado = True
                break
        if motivo:
            seguidos_mal += 1
            if no_existe:
                inexistentes += 1
            elif avisos < 5:
                avisos += 1
                print(f"Aviso: próximas jornadas de {titulo}: acta {numero}: {motivo}.", file=sys.stderr)
            if seguidos_mal >= MAX_SEGUIDOS:
                terminado = True
                break
        else:
            seguidos_mal = 0
            jornadas.add(info["jornada"])
            if any(e[0] in ids for e in info["equipos"]):
                actualizar_estado(estado, {
                    "fecha": info["fecha"], "jornada": info["jornada"], "hora": info["hora"],
                    "resultado": None, "equipos": info["equipos"],
                    "campo": info["campo"], "acta": info["acta"]}, titulo, url, None)
        numero += 1
    if numero >= tope:
        terminado = True
    seguimiento[url] = {"actual": actual, "fecha": hoy.isoformat(),
                        "cursor": None if terminado else numero}
    if gastadas:
        print(f"Próximas jornadas de {titulo}: leídas {gastadas} actas ({inexistentes} no existen), "
              f"jornadas vistas: {sorted(jornadas)}.", file=sys.stderr)
    return gastadas


# ---------------------------------------------------------------------------
# Recorrido con navegador (Playwright)
#
# La web carga las demás jornadas con JavaScript al pulsar sus números, sin cambiar la
# dirección. Un navegador automático puede pulsarlos como una persona, y así se leen todas
# las jornadas de cada categoría: las pasadas con su marcador y las futuras con lo que haya
# (aunque el día o la hora aún no estén confirmados).
# ---------------------------------------------------------------------------
JS_SELECTOR = """
(n) => {
  const esNum = e => e.children.length === 0 && /^\\d{1,2}$/.test(e.textContent.trim());
  const hojas = [...document.querySelectorAll('body *')].filter(esNum);
  const vistos = new Set();
  for (const h of hojas) {
    let a = h.parentElement;
    for (let i = 0; i < 5 && a; i++, a = a.parentElement) {
      if (vistos.has(a)) continue;
      vistos.add(a);
      const lista = [...a.querySelectorAll('*')].filter(esNum);
      const nums = lista.map(e => parseInt(e.textContent.trim(), 10));
      if (nums.length >= 5 && nums.every((x, k) => x === k + 1)) {
        if (n > 0) { const el = lista[n - 1]; if (el) { el.click(); return nums.length; } return -1; }
        return nums.length;
      }
    }
  }
  return 0;
}
"""


class NavegadorPlaywright:
    """Envoltorio mínimo sobre Playwright (así se puede probar con uno falso)."""

    def __init__(self):
        from playwright.sync_api import sync_playwright  # solo se necesita con --navegador
        self._pw = sync_playwright().start()
        self._nav = self._pw.chromium.launch()
        self._pag = self._nav.new_page(locale="es-ES")
        self._pag.set_default_timeout(45000)

    def abrir(self, url):
        self._pag.goto(url, wait_until="domcontentloaded")

    def html(self):
        return self._pag.content()

    def total_jornadas(self):
        return int(self._pag.evaluate(JS_SELECTOR, 0))

    def ir_a(self, jornada):
        return int(self._pag.evaluate(JS_SELECTOR, jornada)) > 0

    def pausa(self, segundos):
        self._pag.wait_for_timeout(int(segundos * 1000))

    def cerrar(self):
        self._nav.close()
        self._pw.stop()


def _firma(soup):
    """Huella de los partidos que muestra la página (para saber si ya han cambiado)."""
    return tuple((tuple(e[0] for e in p["equipos"]), p["hora"], p["resultado"])
                 for p in parsear_jornada(BeautifulSoup(str(soup), "html.parser")))


def _esperar_jornada(nav, jornada, firma_previa=None, intentos=60):
    """Espera a que la página muestre la jornada pedida CON sus partidos ya cargados: la
    cabecera cambia antes que la lista, así que se espera a que la lista sea distinta de la
    jornada anterior y deje de cambiar. Devuelve el HTML, o None si no llega a mostrarse."""
    ultima, estables, soup = None, 0, None
    for _ in range(intentos):
        soup = BeautifulSoup(nav.html(), "html.parser")
        if cabecera(soup)[0] == jornada:
            firma = _firma(soup)
            estables = estables + 1 if (firma and firma == ultima) else 0
            ultima = firma
            if estables >= 2 and firma != firma_previa:
                return soup
        nav.pausa(0.5)
    # se acabó el tiempo: se acepta lo último visto si al menos la cabecera es la pedida
    if soup is not None and cabecera(soup)[0] == jornada and ultima:
        return soup
    return None


def escanear_con_navegador(nav, grupos, finalizadas, estado, errores):
    """Recorre todas las jornadas de cada grupo con el navegador y guarda los partidos de
    nuestros equipos."""
    for url, (nombre, ids) in grupos.items():
        if url in finalizadas:
            continue
        try:
            nav.abrir(url)
            inicial = None
            for _ in range(60):  # esperar a que la página muestre alguna jornada
                inicial = BeautifulSoup(nav.html(), "html.parser")
                if cabecera(inicial)[0]:
                    break
                nav.pausa(0.5)
            if not cabecera(inicial)[0]:
                print(f"Aviso: navegador: {nombre or url}: la página no llegó a mostrar ninguna jornada.",
                      file=sys.stderr)
                continue
            soup = _esperar_jornada(nav, cabecera(inicial)[0])
        except Exception as e:
            errores.append(nombre or url)
            print(f"Aviso: navegador: no se pudo abrir {nombre or url}: {e}", file=sys.stderr)
            continue
        if soup is None or temporada_finalizada(soup):
            continue
        titulo = titulo_grupo(soup)
        total = nav.total_jornadas()
        if total < 2:
            print(f"Aviso: navegador: no encuentro el selector de jornadas de {titulo}.", file=sys.stderr)
            continue
        guardados, vistas, sin_nuestro, por_jornada = 0, [], [], {}
        firma_previa = _firma(soup)
        for j in range(1, total + 1):
            try:
                if not nav.ir_a(j):
                    continue
                pagina_j = _esperar_jornada(nav, j, firma_previa)
            except Exception as e:
                print(f"Aviso: navegador: {titulo}, jornada {j}: {e}", file=sys.stderr)
                continue
            if pagina_j is None:
                print(f"Aviso: navegador: {titulo}: no se pudo mostrar la jornada {j}.", file=sys.stderr)
                continue
            firma_previa = _firma(pagina_j)
            _, fecha_j = cabecera(pagina_j)
            vistas.append(j)
            partidos_j = parsear_jornada(pagina_j)
            por_jornada[j] = len(partidos_j)
            hubo_nuestro = False
            for q in partidos_j:
                p = _con_fecha(q, fecha_j)
                if not p["fecha"] or p["fecha"] < inicio_temporada():
                    continue
                if any(e[0] in ids for e in p["equipos"]):
                    p["jornada"] = j
                    actualizar_estado(estado, p, titulo, url, None)
                    guardados += 1
                    hubo_nuestro = True
            if not hubo_nuestro:
                sin_nuestro.append(j)
        cuentas = sorted(set(por_jornada.values()))
        print(f"Navegador: {titulo}: jornadas leídas {len(vistas)} de {total}; "
              f"partidos por jornada {cuentas[0] if cuentas else 0}-{cuentas[-1] if cuentas else 0}; "
              f"partidos nuestros guardados: {guardados}; jornadas sin partido nuestro: {sin_nuestro}.",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# Partidos escritos a mano
#
# Si algún partido no se consigue leer de la web, se puede añadir a mano en el fichero
# partidos_manuales.json. Se usa solo mientras la web no lo tenga: en cuanto el script lo
# encuentre por sí mismo, el manual se descarta.
# ---------------------------------------------------------------------------
def aplicar_manuales(estado, ruta, grupos_vistos):
    """grupos_vistos: {título del grupo: (url, ids de nuestros equipos)}. Devuelve cuántos
    partidos manuales se han aplicado."""
    for k in [k for k in estado if k.startswith("manual|")]:
        del estado[k]  # se recalculan en cada ejecución
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return 0
    aplicados = 0
    for m in datos:
        titulo, jornada = m.get("grupo"), m.get("jornada")
        if titulo not in grupos_vistos:
            print(f"Aviso: partido manual de '{titulo}': no coincide con ninguna categoría "
                  f"({sorted(grupos_vistos)}).", file=sys.stderr)
            continue
        url, ids = grupos_vistos[titulo]
        existente = next((e for e in estado.values()
                          if e["url_grupo"] == url and e.get("jornada") == jornada), None)
        if existente is not None:
            # la web ya tiene el partido, pero puede faltarle algún dato (p. ej. la hora,
            # que a veces no publica aunque el partido ya se haya jugado): se completa sin
            # pisar lo que la web sí tiene.
            cambiado = False
            for campo_dato in ("hora", "campo"):
                if m.get(campo_dato) and not existente.get(campo_dato):
                    existente[campo_dato] = m[campo_dato]
                    cambiado = True
            if m.get("resultado") and not existente.get("resultado"):
                existente["resultado"] = m["resultado"]
                cambiado = True
            if cambiado:
                aplicados += 1
                print(f"Partido manual de '{titulo}' (jornada {jornada}): completados datos "
                      f"que faltaban en la web.", file=sys.stderr)
            continue
        propio = sorted(ids)[0]
        equipos = []
        for nombre in (m["local"], m["visitante"]):
            equipos.append([propio if _es_nuestro(("", nombre)) else f"manual-{nombre}", nombre])
        estado[f"manual|{url}|{jornada}"] = {
            "fecha": m["fecha"], "jornada": jornada, "hora": m.get("hora"),
            "grupo": titulo, "url_grupo": url, "campo": m.get("campo", ""),
            "local": equipos[0], "visitante": equipos[1],
            "resultado": m.get("resultado"), "pos": None, "acta": None,
        }
        aplicados += 1
    if aplicados:
        print(f"Partidos manuales aplicados: {aplicados}.", file=sys.stderr)
    return aplicados


# ---------------------------------------------------------------------------
# Mensajes
# ---------------------------------------------------------------------------
def sabado_proximo(sabado_arg=None):
    if sabado_arg:
        return datetime.strptime(sabado_arg, "%Y-%m-%d").date()
    hoy = ahora().date()
    wd = hoy.weekday()  # lunes=0 ... domingo=6
    return hoy - timedelta(days=1) if wd == 6 else hoy + timedelta(days=5 - wd)


def sabado_resultados(sabado_arg=None):
    """Sábado del fin de semana más reciente ya empezado."""
    if sabado_arg:
        return datetime.strptime(sabado_arg, "%Y-%m-%d").date()
    hoy = ahora().date()
    wd = hoy.weekday()
    if wd == 5:
        return hoy
    if wd == 6:
        return hoy - timedelta(days=1)
    return hoy - timedelta(days=wd + 2)


def _es_nuestro(par):
    return "CIUDAD LOS ANGELES" in par[1].upper()


def _equipo(par, pos=None):
    limpio = limpiar_equipo(par[1])
    if _es_nuestro(par):
        limpio = f"*_{limpio}_*"  # nuestro equipo: negrita y cursiva
        if ESCUDO:
            limpio = f"{ESCUDO} {limpio}"
    return f"*({pos}º)* {limpio}" if pos else limpio  # posición en negrita


def _emoji_resultado(e):
    """🟢 victoria, 🟡 empate, 🔴 derrota de nuestro equipo."""
    gl, gv = e["resultado"]
    local, visitante = _es_nuestro(e["local"]), _es_nuestro(e["visitante"])
    if local == visitante:
        return "⚽"
    propios, ajenos = (gl, gv) if local else (gv, gl)
    return "🟢" if propios > ajenos else ("🟡" if propios == ajenos else "🔴")


def bloque(e, con_resultado=False):
    """Un partido: hora y categoría, jornada, campo y enfrentamiento."""
    hora = e.get("hora")
    if hora:
        cabecera = f"⏰ *{hora}h* - {e['grupo']}"
    else:
        cabecera = f"🏆 {e['grupo']}"
    campo = limpiar_campo(e.get("campo")) or "por confirmar"
    pos = e.get("pos") or [None, None]
    local, visitante = _equipo(e["local"], pos[0]), _equipo(e["visitante"], pos[1])
    if con_resultado and e.get("resultado"):
        gl, gv = e["resultado"]
        linea = f"{_emoji_resultado(e)} {local} {gl} - {gv} {visitante}"
    else:
        linea = f"⚽ {local} - {visitante}"
    lineas = [cabecera]
    if e.get("jornada"):
        lineas.append(f"📌 _Jornada {e['jornada']}_")
    lineas += [f"📍 Campo: {campo}", linea]
    return "\n".join(lineas)


def sabado_de(d):
    """Sábado de la semana (lunes a domingo) a la que pertenece la fecha d."""
    return d - timedelta(days=1) if d.weekday() == 6 else d + timedelta(days=5 - d.weekday())


def seleccion(estado, sabado, con_resultado):
    """Partidos de la semana cuyo sábado es `sabado` (incluye los de entre semana)."""
    lunes, domingo = sabado - timedelta(days=5), sabado + timedelta(days=1)
    hoy = ahora().date()
    lista = []
    for e in estado.values():
        f = date.fromisoformat(e["fecha"])
        if not lunes <= f <= domingo:
            continue
        if con_resultado and not e.get("resultado"):
            continue
        # en la semana en curso, los partidos de entre semana ya jugados no van en "Partidos"
        if not con_resultado and domingo >= hoy and f.weekday() < 5 and f < hoy:
            continue
        lista.append(e)
    lista.sort(key=lambda e: (e["fecha"], e.get("hora") or "99:99", e["grupo"]))
    return lista


def _pendiente(e):
    """Partido sin hora confirmada y sin jugar: no se sabe ni el día ni la hora."""
    return not e.get("hora") and not e.get("resultado")


def orden_bloques(estado, sabado, con_resultado):
    """Los partidos de la semana en el MISMO orden en que aparecen sus bloques en el texto que
    genera componer(): primero los que ya tienen día asignado (orden de seleccion(), por fecha/
    hora/grupo), luego los pendientes de confirmar día y hora, con su propio orden (por
    categoría). Se usa tanto para el texto como para el detalle en JSON (escudos, goleadores,
    tarjetas...), para que el partido nº `i` del texto sea siempre el partido nº `i` del
    detalle -si cada uno se ordenara por su cuenta, en una semana con partidos pendientes de
    hora los índices no coincidirían y la web mostraría el escudo o los goles de otro partido."""
    lista = seleccion(estado, sabado, con_resultado)
    no_pendientes = [e for e in lista if not _pendiente(e)]
    pendientes = [e for e in lista if _pendiente(e)]
    if pendientes:
        orden = {f"{BASE}/{ruta}/jornadas": i for i, ruta in enumerate(CONOCIDOS)}
        pendientes = sorted(pendientes, key=lambda e: (orden.get(e["url_grupo"], 99), e.get("jornada") or 0))
    return no_pendientes + pendientes


def componer(estado, sabado, con_resultado):
    lista = seleccion(estado, sabado, con_resultado)
    if not lista:
        return ""
    partes = [] if con_resultado else [AVISO]  # la coletilla no va en los resultados
    dia_actual = None
    no_pendientes = [e for e in lista if not _pendiente(e)]
    pendientes = [e for e in lista if _pendiente(e)]
    for e in no_pendientes:
        if e["fecha"] != dia_actual:  # el día aparece una sola vez, como titular
            dia_actual = e["fecha"]
            f = date.fromisoformat(dia_actual)
            titular = f"📅 *{DIAS[f.weekday()].upper()} {f:%d/%m/%Y}*"
            partes.append(f"{LINEA}\n{titular}\n{LINEA}")
        partes.append(bloque(e, con_resultado))
    if pendientes:  # sin sábado ni domingo: aún no hay día y hora asignados
        partes.append(f"{LINEA}\n📅 *PENDIENTE DE CONFIRMAR DÍA Y HORA*\n{LINEA}")
        partes.extend(bloque(e, con_resultado) for e in orden_bloques(estado, sabado, con_resultado) if _pendiente(e))
    return "\n\n".join(partes)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sabado", help="fecha del sábado (AAAA-MM-DD); por defecto, el próximo")
    ap.add_argument("--abrir", action="store_true", help="abrir WhatsApp con el texto ya escrito")
    ap.add_argument("--salida", default="partidos_whatsapp.txt", help="fichero de salida")
    ap.add_argument("--json", help="además, guarda el resultado en este fichero JSON (para la página web)")
    ap.add_argument("--estado", default="estado.json", help="fichero donde se recuerdan los partidos vistos")
    ap.add_argument("--manuales", default="partidos_manuales.json",
                    help="fichero con partidos escritos a mano (opcional)")
    ap.add_argument("--navegador", action="store_true",
                    help="recorrer todas las jornadas con un navegador automático (necesita Playwright)")
    ap.add_argument("--sin-navegador-pendientes", action="store_true",
                    help="no usar el navegador automático para completar resultados que el acta "
                         "todavía no da (por si Playwright no está disponible)")
    args = ap.parse_args()

    sab_prox = sabado_proximo(args.sabado)
    sab_res = sabado_resultados(args.sabado)
    print(f"Partidos del {sab_prox:%d/%m/%Y} al {sab_prox + timedelta(days=1):%d/%m/%Y}; "
          f"resultados del {sab_res:%d/%m/%Y} al {sab_res + timedelta(days=1):%d/%m/%Y}...",
          file=sys.stderr)

    estado, clasif, meta = cargar_estado(args.estado)
    cupo = MAX_RELLENO
    errores, consultados, vistos, finalizadas, trabajos = [], 0, [], set(), []
    grupos, equipos_club = grupos_a_consultar(errores)
    for url, (nombre, ids) in grupos.items():
        try:
            soup = get(url)
        except Exception as e:
            errores.append(nombre or url)
            print(f"Aviso: no se pudo consultar {nombre or url}: {e}", file=sys.stderr)
            continue
        actual, fecha_cab = cabecera(soup)
        if temporada_finalizada(soup) or (fecha_cab and fecha_cab < inicio_temporada()):
            finalizadas.add(url)  # calendario de una temporada antigua: se ignora
            continue
        consultados += 1
        titulo = titulo_grupo(soup)
        vistos.append((url, titulo))
        todos = [p for p in (_con_fecha(q, fecha_cab) for q in parsear_jornada(soup))
                 if p["fecha"] and p["fecha"] >= inicio_temporada()]
        propios = [p for p in todos if any(e[0] in ids for e in p["equipos"])]

        filas = []
        try:
            soup_c = get(url.replace("/jornadas", "/clasificacion"))
            jornada_tabla, filas = tabla_de(soup_c)
            if filas:
                clasif[url] = {"titulo": titulo, "jornada": jornada_tabla, "filas": filas}
            else:
                print(f"Aviso: sin filas en la clasificación de {titulo} "
                      f"(tablas: {len(soup_c.find_all('table'))}, "
                      f"enlaces a equipos: {len(soup_c.find_all('a', href=re.compile(r'/equipo/')))})",
                      file=sys.stderr)
        except Exception as e:
            print(f"Aviso: no se pudo leer la clasificación de {titulo}: {e}", file=sys.stderr)
        # con todos los equipos a 0 partidos la tabla no significa nada
        posiciones = {f["id"]: f["pos"] for f in filas} if any(f["pj"] for f in filas) else {}
        for p in propios:
            actualizar_estado(estado, p, titulo, url, posiciones)
        actas = [int(a.group(1)) for a in (re.search(r"/acta/(\d+)", p["acta"] or "") for p in todos) if a]
        equipos_grupo = {e[0] for p in todos for e in p["equipos"]} | {f["id"] for f in filas}
        trabajos.append((url, ids, titulo, actual, actas, equipos_grupo))

    if consultados == 0:
        sys.exit("No se ha podido consultar ningún calendario; se mantiene el resultado anterior.")

    if args.navegador:
        nav = None
        try:
            nav = NavegadorPlaywright()
            escanear_con_navegador(nav, grupos, finalizadas, estado, errores)
        except Exception as e:
            print(f"Aviso: no se ha podido usar el navegador: {e}", file=sys.stderr)
        finally:
            if nav is not None:
                nav.cerrar()

    # Al cambiar la versión del histórico se reintentan los recorridos que acabaron sin resultados
    if meta.get("version_historico") != VERSION_HISTORICO:
        hoy_iso = ahora().date().isoformat()
        for url, ids, titulo, actual, actas, equipos_grupo in trabajos:
            hay_pasados = any(e["url_grupo"] == url and e["fecha"] < hoy_iso for e in estado.values())
            if not hay_pasados:
                meta.setdefault("terminado", {}).pop(url, None)
                meta.setdefault("cursor", {}).pop(url, None)
        for url, ids, titulo, actual, actas, equipos_grupo in trabajos:
            hay_futuros = any(e["url_grupo"] == url and e["fecha"] > hoy_iso for e in estado.values())
            if not hay_futuros:
                meta.setdefault("proximas", {}).pop(url, None)
        meta["version_historico"] = VERSION_HISTORICO

    # Histórico: primero las jornadas pasadas de TODAS las categorías (los resultados son lo
    # más importante) y con lo que sobre, las próximas jornadas.
    for url, ids, titulo, actual, actas, equipos_grupo in trabajos:
        if actual and actual > 1:
            cupo -= rellenar_jornadas(url, ids, titulo, actual, actas, equipos_grupo, estado, meta, cupo)
    for url, ids, titulo, actual, actas, equipos_grupo in trabajos:
        if actual:
            cupo -= explorar_proximas(url, ids, titulo, actual, actas, equipos_grupo, estado, meta, cupo)

    aplicar_manuales(estado, args.manuales,
                     {t: (u, grupos[u][1]) for u, t in vistos if u in grupos})
    completar_con_actas(estado, errores)
    if not args.sin_navegador_pendientes:
        completar_pendientes_con_navegador(estado, errores)

    # se guarda el histórico de la temporada en curso (lo de temporadas anteriores se descarta)
    limite = inicio_temporada().isoformat()
    estado = {k: v for k, v in estado.items() if v["fecha"] >= limite}
    guardar_estado(args.estado, estado, clasif, meta)

    urls_con_partido = {e["url_grupo"] for e in seleccion(estado, sab_prox, False)}
    sin_partido = sorted({t for u, t in vistos if u not in urls_con_partido})
    texto = componer(estado, sab_prox, con_resultado=False)
    resultados = componer(estado, sab_res, con_resultado=True)

    if sin_partido:
        print("Sin partido en el calendario mostrado para: " + "; ".join(sin_partido), file=sys.stderr)

    # Equipos de la ficha del club cuya competición de esta temporada aún no está publicada
    con_grupo = set()
    for u, (_, ids_u) in grupos.items():
        if u not in finalizadas:
            con_grupo |= ids_u
    pendientes = [{"titulo": limpiar_titulo(capitalizar(cat)), "jornada": None, "filas": [],
                   "equipo": limpiar_equipo(nombre), "pendiente": True}
                  for ident, (nombre, cat) in equipos_club.items()
                  if ident not in con_grupo and cat]

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "generado": ahora().strftime("%Y-%m-%d %H:%M"),
                "desde": sab_prox.isoformat(),
                "hasta": (sab_prox + timedelta(days=1)).isoformat(),
                "texto": texto,
                "resultados_desde": sab_res.isoformat(),
                "resultados_hasta": (sab_res + timedelta(days=1)).isoformat(),
                "resultados": resultados,
                "sin_partido": sin_partido,
                "sin_calendario": [f"{c['titulo']} ({c['equipo'].split()[-1]})"
                                   if re.search(r"\s[A-Z]$", c["equipo"]) else c["titulo"]
                                   for c in pendientes],
                "errores": errores,
                "semanas": [
                    {"sabado": sab.isoformat(),
                     "partidos": componer(estado, sab, False),
                     "resultados": componer(estado, sab, True)}
                    for sab in sorted({sabado_de(date.fromisoformat(e["fecha"])) for e in estado.values()})
                ],
                # Goleadores y tarjetas de cada partido ya jugado, en el mismo orden que el
                # texto de "resultados" de cada semana (así la web puede emparejar cada
                # partido del texto con sus goles/tarjetas por posición).
                "detalle_resultados": [
                    {"sabado": sab.isoformat(),
                     "partidos": [
                         {"fecha": e["fecha"], "grupo": e["grupo"], "jornada": e.get("jornada"),
                          "local": limpiar_equipo(e["local"][1]), "visitante": limpiar_equipo(e["visitante"][1]),
                          "escudo_local": e.get("escudo_local"), "escudo_visitante": e.get("escudo_visitante"),
                          "resultado": e.get("resultado"),
                          "goles": e.get("goles") or [], "tarjetas": e.get("tarjetas") or []}
                         for e in orden_bloques(estado, sab, True)
                     ]}
                    for sab in sorted({sabado_de(date.fromisoformat(e["fecha"])) for e in estado.values()})
                ],
                # Lo mismo que "detalle_resultados" pero para los partidos TODAVÍA SIN JUGAR
                # (pestaña "Partidos"): de momento solo lleva el escudo de cada equipo -el
                # nombre y la posición en la clasificación ya van dentro del propio texto del
                # bloque ("*(3º)* Equipo"), así que la web no necesita más que esto de aquí.
                "detalle_partidos": [
                    {"sabado": sab.isoformat(),
                     "partidos": [
                         {"fecha": e["fecha"], "grupo": e["grupo"], "jornada": e.get("jornada"),
                          "local": limpiar_equipo(e["local"][1]), "visitante": limpiar_equipo(e["visitante"][1]),
                          "escudo_local": e.get("escudo_local"), "escudo_visitante": e.get("escudo_visitante")}
                         for e in orden_bloques(estado, sab, False)
                     ]}
                    for sab in sorted({sabado_de(date.fromisoformat(e["fecha"])) for e in estado.values()})
                ],
                "clasificaciones": [
                    {**clasif[u], "equipo": next((f["nombre"] for f in clasif[u]["filas"] if f["nuestro"]), "")}
                    for u in grupos if u in clasif and u not in finalizadas
                ] + pendientes,
            }, f, ensure_ascii=False, indent=2)
        print(f"Guardado en {args.json}", file=sys.stderr)

    if not texto and not resultados:
        if args.json:
            print("No hay partidos ni resultados para esos fines de semana.", file=sys.stderr)
            return
        sys.exit("No hay partidos ni resultados para esos fines de semana.")

    if texto:
        print("\n" + texto + "\n")
        with open(args.salida, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
        print(f"Guardado en {args.salida}", file=sys.stderr)
    if resultados:
        print("\n----- RESULTADOS -----\n\n" + resultados + "\n")

    if args.abrir and texto:
        webbrowser.open("https://wa.me/?text=" + urllib.parse.quote(texto))


if __name__ == "__main__":
    main()
