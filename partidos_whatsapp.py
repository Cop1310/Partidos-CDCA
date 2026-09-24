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
JORNADA = re.compile(r"^Jornada\s*(\d{1,2})\s*(?:Actual)?\s*\|?\s*\d{2}-\d{2}-\d{4}$")


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
                actual = actual or nuevo()
                actual["equipos"].append((m.group(1), texto))
            elif "/campo/" in h and "google" not in h:
                if actual is not None:
                    actual["campo"] = texto
            elif "/acta/" in h:
                if actual is not None and len(actual["equipos"]) >= 2 and actual["fecha"]:
                    actual["acta"] = urllib.parse.urljoin(BASE, h)
                    partidos.append(actual)
                actual = None
    return partidos


def datos_de_acta(soup):
    """(marcador, hora) de un acta. El marcador solo se da si el acta ya tiene alineaciones."""
    normalizar(soup)
    textos = [t.strip() for t in soup.find_all(string=True)
              if t.parent is not None and t.parent.name not in ("script", "style")]
    marcador = None
    if "Titulares" in textos:
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
            "nuestro": _es_nuestro((ident, nombre)),
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


def cabecera_jornada(soup):
    """Número de la jornada que muestra la página (cabecera 'Jornada 3 ...')."""
    normalizar(soup)
    esperando = False
    for t in soup.find_all(string=True):
        if t.parent is not None and t.parent.name in ("script", "style"):
            continue
        txt = str(t).strip()
        mj = JORNADA.match(txt)
        if mj:
            return int(mj.group(1))
        if txt == "Jornada":
            esperando = True
        elif esperando and txt.isdigit():
            return int(txt)
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
    """Lee las actas para completar el marcador (partidos ya jugados) y la hora
    (partidos de los que solo se conoce el marcador)."""
    t = ahora()
    pendientes = []
    for e in estado.values():
        if not e.get("acta"):
            continue
        dias = (t.date() - date.fromisoformat(e["fecha"])).days
        sin_marcador = not e.get("resultado") and dias <= 10 and _ya_toca_mirar_acta(e, t)
        sin_hora = bool(e.get("resultado")) and not e.get("hora") and dias >= 0
        if sin_marcador or sin_hora:
            pendientes.append(e)
    for e in pendientes[:25]:
        try:
            marcador, hora = datos_de_acta(get(e["acta"]))
        except Exception as ex:
            print(f"Aviso: no se pudo leer un acta: {ex}", file=sys.stderr)
            continue
        if marcador and not e.get("resultado"):
            e["resultado"] = list(marcador)
        if hora and not e.get("hora"):
            e["hora"] = hora


# ---------------------------------------------------------------------------
# Jornadas anteriores (histórico)
# ---------------------------------------------------------------------------
PARAMS_JORNADA = ("jornada", "j", "jor", "numJornada", "round")
MAX_RELLENO = 30  # peticiones de jornadas antiguas por ejecución


def descubrir_parametro(url, objetivo):
    """Averigua con qué parámetro de la dirección se pide otra jornada. Solo da por bueno
    un parámetro si la página devuelve de verdad la jornada pedida."""
    for nombre in PARAMS_JORNADA:
        try:
            if cabecera_jornada(get(f"{url}?{nombre}={objetivo}")) == objetivo:
                return nombre
        except Exception:
            continue
    return ""


def rellenar_jornadas(url, ids, titulo, actual, estado, meta, cupo):
    """Guarda los partidos de las jornadas anteriores a la que muestra la web. Devuelve
    cuántas peticiones ha gastado."""
    hechas = set(meta["hechas"].get(url, []))
    pendientes = [j for j in range(1, actual) if j not in hechas]
    if not pendientes or cupo <= 0:
        return 0
    param = meta.get("param")
    gastadas = 0
    if not param:
        reciente = meta.get("probado") and \
            (ahora().date() - date.fromisoformat(meta["probado"])).days < 3
        if param == "" and reciente:
            return 0  # ya se probó hace poco y no funcionó
        param = descubrir_parametro(url, actual - 1)
        gastadas += len(PARAMS_JORNADA) if not param else 1
        meta["param"], meta["probado"] = param, ahora().date().isoformat()
        if not param:
            print("Aviso: no he encontrado cómo pedir otra jornada; el histórico se irá "
                  "completando con las jornadas nuevas.", file=sys.stderr)
            return gastadas
    for j in pendientes:
        if gastadas >= cupo:
            break
        gastadas += 1
        try:
            soup = get(f"{url}?{param}={j}")
        except Exception as e:
            print(f"Aviso: no se pudo leer la jornada {j} de {titulo}: {e}", file=sys.stderr)
            continue
        if cabecera_jornada(soup) != j:
            continue  # la web no ha devuelto la jornada pedida: no se guarda nada
        for p in parsear_jornada(soup):
            if any(e[0] in ids for e in p["equipos"]):
                actualizar_estado(estado, p, titulo, url, None)
        hechas.add(j)
    meta["hechas"][url] = sorted(hechas)
    return gastadas


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
    return f"({pos}º) {limpio}" if pos else limpio


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
    elif con_resultado or e.get("resultado"):
        cabecera = f"🏆 {e['grupo']}"
    else:
        cabecera = f"⏰ *Hora por confirmar* - {e['grupo']}"
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


def componer(estado, sabado, con_resultado):
    lista = seleccion(estado, sabado, con_resultado)
    if not lista:
        return ""
    partes = [AVISO]
    dia_actual = None
    for e in lista:
        if e["fecha"] != dia_actual:  # el día aparece una sola vez, como titular
            dia_actual = e["fecha"]
            f = date.fromisoformat(dia_actual)
            titular = f"📅 *{DIAS[f.weekday()].upper()} {f:%d/%m/%Y}*"
            partes.append(f"{LINEA}\n{titular}\n{LINEA}")
        partes.append(bloque(e, con_resultado))
    return "\n\n".join(partes)


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

    estado, clasif, meta = cargar_estado(args.estado)
    cupo = MAX_RELLENO
    errores, consultados, vistos, finalizadas = [], 0, [], set()
    grupos = grupos_a_consultar(errores)
    for url, (nombre, ids) in grupos.items():
        try:
            soup = get(url)
        except Exception as e:
            errores.append(nombre or url)
            print(f"Aviso: no se pudo consultar {nombre or url}: {e}", file=sys.stderr)
            continue
        if temporada_finalizada(soup):  # calendario de una temporada antigua: se ignora
            finalizadas.add(url)
            continue
        consultados += 1
        titulo = titulo_grupo(soup)
        vistos.append((url, titulo))
        actual = cabecera_jornada(soup)
        propios = [p for p in parsear_jornada(soup) if any(e[0] in ids for e in p["equipos"])]

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
        if actual and actual > 1:
            cupo -= rellenar_jornadas(url, ids, titulo, actual, estado, meta, cupo)

    if consultados == 0:
        sys.exit("No se ha podido consultar ningún calendario; se mantiene el resultado anterior.")

    completar_con_actas(estado, errores)

    # se guarda todo el histórico de la temporada (solo se descartan partidos de hace más de un año)
    limite = (ahora().date() - timedelta(days=400)).isoformat()
    estado = {k: v for k, v in estado.items() if v["fecha"] >= limite}
    guardar_estado(args.estado, estado, clasif, meta)

    urls_con_partido = {e["url_grupo"] for e in seleccion(estado, sab_prox, False)}
    sin_partido = sorted({t for u, t in vistos if u not in urls_con_partido})
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
                "semanas": [
                    {"sabado": sab.isoformat(),
                     "partidos": componer(estado, sab, False),
                     "resultados": componer(estado, sab, True)}
                    for sab in sorted({sabado_de(date.fromisoformat(e["fecha"])) for e in estado.values()})
                ],
                "clasificaciones": [
                    {**clasif[u], "equipo": next((f["nombre"] for f in clasif[u]["filas"] if f["nuestro"]), "")}
                    for u in grupos if u in clasif and u not in finalizadas
                ],
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
