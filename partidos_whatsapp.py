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
import json
import re
import sys
import time
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
AVISO = "Información sacada de la web https://www.elbalondemadrid.es/"
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
    """Devuelve {url_jornadas: (nombre o None, {ids de equipos del club})}."""
    grupos, conocidos = {}, set()
    for ruta, (nombre, ids) in CONOCIDOS.items():
        grupos[f"{BASE}/{ruta}/jornadas"] = (nombre, set(ids))
        conocidos |= ids

    # Equipos del club que no están en la lista anterior: se buscan en la web.
    try:
        club = get(CLUB_URL)
    except Exception as e:
        errores.append("ficha del club (no se han buscado equipos nuevos)")
        print(f"Aviso: {e}", file=sys.stderr)
        return grupos
    nuevos = set()
    for a in club.find_all("a", href=True):
        m = re.search(r"/equipo/(\d+)/?$", a["href"])
        if m and m.group(1) not in conocidos:
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
    return grupos


FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
HORA = re.compile(r"^(\d{2}:\d{2}|--:--)$")
RESULTADO = re.compile(r"^(\d{1,2})\s*[·–\-]\s*(\d{1,2})$")


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

    def nuevo():
        return {"fecha": fecha, "hora": None, "resultado": None,
                "equipos": [], "campo": "", "acta": None}

    for nodo in soup.descendants:
        if isinstance(nodo, NavigableString):
            if nodo.parent is not None and nodo.parent.name in ("script", "style"):
                continue
            t = str(nodo).strip()
            if FECHA.match(t):
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
    return partidos


def resultado_de_acta(soup):
    """Marcador de un acta. Solo se fía si el acta ya tiene alineaciones."""
    normalizar(soup)
    textos = [t.strip() for t in soup.find_all(string=True)
              if t.parent is not None and t.parent.name not in ("script", "style")]
    if "Titulares" not in textos:
        return None
    for t in textos:
        m = RESULTADO.match(t)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    return None


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
            return json.load(f).get("partidos", {})
    except (OSError, ValueError):
        return {}


def guardar_estado(ruta, partidos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"partidos": partidos}, f, ensure_ascii=False, indent=1, sort_keys=True)


def clave(p):
    m = re.search(r"/acta/(\d+)", p["acta"] or "")
    return m.group(1) if m else f"{p['fecha']}|{p['equipos'][0][0]}|{p['equipos'][1][0]}"


def actualizar_estado(estado, p, titulo, url):
    k = clave(p)
    anterior = estado.get(k, {})
    estado[k] = {
        "fecha": p["fecha"].isoformat(),
        "hora": p["hora"] or anterior.get("hora"),
        "grupo": titulo,
        "url_grupo": url,
        "campo": p["campo"] or anterior.get("campo", ""),
        "local": list(p["equipos"][0]),
        "visitante": list(p["equipos"][1]),
        "resultado": list(p["resultado"]) if p["resultado"] else anterior.get("resultado"),
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
    """Para partidos ya jugados sin marcador, intenta leerlo del acta."""
    t = ahora()
    pendientes = [e for e in estado.values()
                  if not e.get("resultado") and e.get("acta")
                  and (t.date() - date.fromisoformat(e["fecha"])).days <= 10
                  and _ya_toca_mirar_acta(e, t)]
    for e in pendientes[:20]:
        try:
            r = resultado_de_acta(get(e["acta"]))
        except Exception as ex:
            print(f"Aviso: no se pudo leer un acta: {ex}", file=sys.stderr)
            continue
        if r:
            e["resultado"] = list(r)


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


def _equipo(par):
    ident, nombre = par
    limpio = limpiar_equipo(nombre)
    if "CIUDAD LOS ANGELES" in nombre.upper():
        return f"*_{limpio}_*"  # nuestro equipo: negrita y cursiva
    return limpio


def bloque(e, con_resultado=False):
    f = date.fromisoformat(e["fecha"])
    dia = DIAS[f.weekday()]
    hora = e.get("hora")
    if hora:
        h = f" {hora}h"
    else:
        h = "" if con_resultado else " hora por confirmar"
    campo = limpiar_campo(e.get("campo")) or "por confirmar"
    local, visitante = _equipo(e["local"]), _equipo(e["visitante"])
    if con_resultado and e.get("resultado"):
        gl, gv = e["resultado"]
        linea = f"{local} {gl} - {gv} {visitante}"
    else:
        linea = f"{local} - {visitante}"
    return "\n".join([e["grupo"], f"*{dia} {f:%d/%m/%Y}{h}*", f"Campo: {campo}", linea])


def componer(estado, sabado, con_resultado):
    fechas = {sabado.isoformat(), (sabado + timedelta(days=1)).isoformat()}
    lista = [e for e in estado.values() if e["fecha"] in fechas
             and (e.get("resultado") if con_resultado else True)]
    lista.sort(key=lambda e: (e["fecha"], e.get("hora") or "99:99", e["grupo"]))
    if not lista:
        return ""
    return AVISO + "\n\n" + "\n\n".join(bloque(e, con_resultado) for e in lista)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sabado", help="fecha del sábado (AAAA-MM-DD); por defecto, el próximo")
    ap.add_argument("--abrir", action="store_true", help="abrir WhatsApp con el texto ya escrito")
    ap.add_argument("--salida", default="partidos_whatsapp.txt", help="fichero de salida")
    ap.add_argument("--json", help="además, guarda el resultado en este fichero JSON (para la página web)")
    ap.add_argument("--estado", default="estado.json", help="fichero donde se recuerdan los partidos vistos")
    args = ap.parse_args()

    sab_prox = sabado_proximo(args.sabado)
    sab_res = sabado_resultados(args.sabado)
    print(f"Partidos del {sab_prox:%d/%m/%Y} al {sab_prox + timedelta(days=1):%d/%m/%Y}; "
          f"resultados del {sab_res:%d/%m/%Y} al {sab_res + timedelta(days=1):%d/%m/%Y}...",
          file=sys.stderr)

    estado = cargar_estado(args.estado)
    errores, consultados, vistos = [], 0, []
    for url, (nombre, ids) in grupos_a_consultar(errores).items():
        try:
            soup = get(url)
        except Exception as e:
            errores.append(nombre or url)
            print(f"Aviso: no se pudo consultar {nombre or url}: {e}", file=sys.stderr)
            continue
        consultados += 1
        titulo = titulo_grupo(soup)
        vistos.append((url, titulo))
        for p in parsear_jornada(soup):
            if any(e[0] in ids for e in p["equipos"]):
                actualizar_estado(estado, p, titulo, url)

    if consultados == 0:
        sys.exit("No se ha podido consultar ningún calendario; se mantiene el resultado anterior.")

    completar_con_actas(estado, errores)

    # limpiar partidos antiguos
    limite = (ahora().date() - timedelta(days=21)).isoformat()
    estado = {k: v for k, v in estado.items() if v["fecha"] >= limite}
    guardar_estado(args.estado, estado)

    fechas_prox = {sab_prox.isoformat(), (sab_prox + timedelta(days=1)).isoformat()}
    sin_partido = sorted({t for u, t in vistos
                          if not any(e["url_grupo"] == u and e["fecha"] in fechas_prox
                                     for e in estado.values())})
    texto = componer(estado, sab_prox, con_resultado=False)
    resultados = componer(estado, sab_res, con_resultado=True)

    if sin_partido:
        print("Sin partido en el calendario mostrado para: " + "; ".join(sin_partido), file=sys.stderr)

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
                "errores": errores,
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
