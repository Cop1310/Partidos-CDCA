#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Partidos del fin de semana del C.D. Ciudad de los Ángeles, listos para WhatsApp.

Lee elbalondemadrid.es (datos públicos de la RFFM), localiza automáticamente
todos los equipos del club, busca sus partidos del fin de semana y genera el
mensaje con el formato de WhatsApp (negrita con *asteriscos*).

Uso:
    pip install requests beautifulsoup4
    python partidos_whatsapp.py                    # próximo fin de semana
    python partidos_whatsapp.py --sabado 2026-10-10  # un fin de semana concreto
    python partidos_whatsapp.py --abrir            # además abre WhatsApp con el texto

Resultado: se imprime por pantalla y se guarda en partidos_whatsapp.txt
(con --json también se guarda en JSON para la página web de GitHub Pages)
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString

BASE = "https://www.elbalondemadrid.es"
CLUB_ID = 4427  # C.D. Ciudad de los Ángeles
CLUB_URL = f"{BASE}/club/{CLUB_ID}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; partidos-CLA/1.0)"}
PAUSA = 0.4  # segundos entre peticiones (cortesía con la web)

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
}
NOMBRES_CAMPO = {
    # "IDB DAVID DIEZ DE LA CRUZ": "IDB David Diez de la Cruz",
}
QUITAR_PREFIJOS = {
    "C.D.", "C.F.", "S.A.D.", "A.D.", "C.D.E.", "CDE", "CDB", "C.D.B.",
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
    return f"{base} {letra}".strip()


def limpiar_campo(campo):
    c = re.sub(r"\s*\([^)]*\)", "", campo or "").strip()
    if not c:
        return ""
    return NOMBRES_CAMPO.get(c.upper()) or capitalizar(c)


def limpiar_titulo(t):
    for k, v in ACENTOS_TITULO.items():
        t = t.replace(k, v)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# Descarga y análisis
# ---------------------------------------------------------------------------
def get(url):
    time.sleep(PAUSA)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.content, "html.parser")


def descubrir_grupos():
    """Devuelve {url_jornadas: {ids de equipos del club en ese grupo}}."""
    club = get(CLUB_URL)
    ids = {}
    for a in club.find_all("a", href=True):
        m = re.search(r"/equipo/(\d+)/?$", a["href"])
        if m:
            ids[m.group(1)] = a.get_text(" ", strip=True)
    if not ids:
        sys.exit("No he encontrado equipos en la ficha del club. ¿Ha cambiado la web?")

    grupos = {}
    for eid in ids:
        pag = get(f"{BASE}/equipo/{eid}")
        enlace = None
        for a in pag.find_all("a", href=True):
            if re.search(r"/competicion/\d+/grupo/\d+/clasificacion", a["href"]):
                enlace = a["href"]
                break
        if enlace:
            url = urllib.parse.urljoin(BASE, enlace).replace("/clasificacion", "/jornadas")
            grupos.setdefault(url, set()).add(eid)
    return grupos


FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
HORA = re.compile(r"^(\d{2}:\d{2}|--:--)$")


def parsear_jornada(soup):
    """Recorre la página en orden y reconstruye los partidos:
    fecha -> local -> hora -> visitante -> campo -> enlace 'Ver acta' (fin)."""
    partidos, fecha, actual = [], None, None

    def nuevo():
        return {"fecha": fecha, "hora": None, "equipos": [], "campo": ""}

    for nodo in soup.descendants:
        if isinstance(nodo, NavigableString):
            if nodo.parent is not None and nodo.parent.name in ("script", "style"):
                continue
            t = str(nodo).strip()
            if FECHA.match(t):
                fecha = datetime.strptime(t, "%d/%m/%Y").date()
            elif HORA.match(t):
                actual = actual or nuevo()
                actual["hora"] = t
        elif getattr(nodo, "name", None) == "a" and nodo.get("href"):
            h = nodo["href"]
            texto = nodo.get_text(" ", strip=True)
            m = re.search(r"/equipo/(-?\d+)", h)
            if m:
                actual = actual or nuevo()
                actual["equipos"].append((m.group(1), texto))
            elif "/campo/" in h and "google" not in h:
                if actual is not None:
                    actual["campo"] = texto
            elif "/acta/" in h:
                if actual is not None and len(actual["equipos"]) >= 2:
                    partidos.append(actual)
                actual = None
    return partidos


def titulo_grupo(soup):
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return limpiar_titulo(h1.get_text(" ", strip=True))
    t = soup.title.get_text() if soup.title else ""
    m = re.search(r"resultados (.+?) \d{4}/\d{2}", t)
    return limpiar_titulo(m.group(1)) if m else "Partido"


# ---------------------------------------------------------------------------
# Mensaje
# ---------------------------------------------------------------------------
def fin_de_semana(sabado_arg=None):
    if sabado_arg:
        sab = datetime.strptime(sabado_arg, "%Y-%m-%d").date()
    else:
        hoy = datetime.now(ZoneInfo("Europe/Madrid")).date()
        wd = hoy.weekday()  # lunes=0 ... domingo=6
        sab = hoy - timedelta(days=1) if wd == 6 else hoy + timedelta(days=5 - wd)
    return {sab, sab + timedelta(days=1)}


def bloque(titulo, p):
    dia = DIAS[p["fecha"].weekday()]
    hora = p["hora"]
    h = f"{hora}h" if hora and hora != "--:--" else "hora por confirmar"
    campo = limpiar_campo(p["campo"]) or "por confirmar"
    local, visitante = p["equipos"][0][1], p["equipos"][1][1]
    return "\n".join([
        titulo,
        f"*{dia} {p['fecha']:%d/%m/%Y} {h}*",
        f"Campo: {campo}",
        f"{limpiar_equipo(local)} - {limpiar_equipo(visitante)}",
    ])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sabado", help="fecha del sábado (AAAA-MM-DD); por defecto, el próximo")
    ap.add_argument("--abrir", action="store_true", help="abrir WhatsApp con el texto ya escrito")
    ap.add_argument("--salida", default="partidos_whatsapp.txt", help="fichero de salida")
    ap.add_argument("--json", help="además, guarda el resultado en este fichero JSON (para la página web)")
    args = ap.parse_args()

    fechas = fin_de_semana(args.sabado)
    print(f"Buscando partidos del {min(fechas):%d/%m/%Y} al {max(fechas):%d/%m/%Y}...", file=sys.stderr)

    encontrados, sin_partido = [], []
    for url, ids in descubrir_grupos().items():
        soup = get(url)
        titulo = titulo_grupo(soup)
        mios = [p for p in parsear_jornada(soup)
                if p["fecha"] in fechas and any(e[0] in ids for e in p["equipos"])]
        if not mios:
            sin_partido.append(titulo)
        for p in mios:
            encontrados.append((p["fecha"], p["hora"] if p["hora"] != "--:--" else "99:99", titulo, p))

    encontrados.sort(key=lambda x: (x[0], x[1], x[2]))
    texto = "\n\n".join(bloque(t, p) for _, _, t, p in encontrados)
    sin_partido = sorted(set(sin_partido))

    if sin_partido:
        print("Sin partido en el calendario mostrado para: " + "; ".join(sin_partido),
              file=sys.stderr)

    if args.json:
        ahora = datetime.now(ZoneInfo("Europe/Madrid"))
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "generado": ahora.strftime("%Y-%m-%d %H:%M"),
                "desde": min(fechas).isoformat(),
                "hasta": max(fechas).isoformat(),
                "texto": texto,
                "sin_partido": sin_partido,
            }, f, ensure_ascii=False, indent=2)
        print(f"Guardado en {args.json}", file=sys.stderr)

    if not texto:
        if args.json:
            print("No se ha encontrado ningún partido para ese fin de semana.", file=sys.stderr)
            return
        sys.exit("No se ha encontrado ningún partido para ese fin de semana.")

    print("\n" + texto + "\n")
    with open(args.salida, "w", encoding="utf-8") as f:
        f.write(texto + "\n")
    print(f"Guardado en {args.salida}", file=sys.stderr)

    if args.abrir:
        webbrowser.open("https://wa.me/?text=" + urllib.parse.quote(texto))


if __name__ == "__main__":
    main()
