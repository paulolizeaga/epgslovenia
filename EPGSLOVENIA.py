#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delo_epg_scraper.py
-------------------
Genera un archivo XMLTV (EPG) limpio a partir de tvspored.delo.si con soporte para logos.
"""

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Faltan dependencias. Instala con:\n  pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

BASE_URL = "https://tvspored.delo.si/oddaje/{canal}/vsi/vsi/{fecha}"
TZ_LJ = timezone(timedelta(hours=2))  # UTC+2 (Slovenia CEST)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sl-SI,sl;q=0.9,en;q=0.8",
}

# Mapa de logos para los canales
LOGOS_CANALES = {
    "sk1": "https://thumb.wikimedia.org/wikipedia/commons/thumb/5/54/Sportklub_Logo.svg/330px-Sportklub_Logo.svg.png",
    "sporttv1": "https://upload.wikimedia.org/wikipedia/en/1/1b/%C5%A0port_TV_%28Slovenia%29.png?utm_source=en.wikipedia.org&utm_campaign=index&utm_content=original",
    "sport-tv-1": "https://upload.wikimedia.org/wikipedia/en/1/1b/%C5%A0port_TV_%28Slovenia%29.png?utm_source=en.wikipedia.org&utm_campaign=index&utm_content=original",
    "sporttv-1": "https://upload.wikimedia.org/wikipedia/en/1/1b/%C5%A0port_TV_%28Slovenia%29.png?utm_source=en.wikipedia.org&utm_campaign=index&utm_content=original"
}

TIME_REGEX = re.compile(r"(\d{1,2}:\d{2})(?:\s*[-–]\s*(\d{1,2}:\d{2}))?")
CATEGORY_REGEX = re.compile(
    r"\s*(Šport\b.*|Razvedrilni program\b.*|Propagandni program\b.*|Informativna oddaja\b.*|Kviz\b.*)$",
    re.IGNORECASE
)


def fetch_dia(canal: str, fecha_str: str) -> str:
    url = BASE_URL.format(canal=canal, fecha=fecha_str)
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def limpiar_titulo_y_categoria(texto_raw: str):
    text = texto_raw.strip()

    # 1. Eliminar contadores de tiempo restante ("še 01:59 min")
    text = re.sub(r"\s*še\s*\d+:\d+\s*min.*$", "", text, flags=re.IGNORECASE)

    # 2. Eliminar horas duplicadas o pegadas al principio ("00:00", "14:1514:15")
    text = re.sub(r"^(\d{1,2}:\d{2})+", "", text).strip()

    # 3. Extraer categoría del final si está pegada al título
    categoria = ""
    m_cat = CATEGORY_REGEX.search(text)
    if m_cat:
        categoria = m_cat.group(1).strip()
        text = text[:m_cat.start()].strip()

    # 4. Normalizar espacios
    text = re.sub(r"\s+", " ", text).strip()
    categoria = re.sub(r"\s+", " ", categoria).strip()

    return text, categoria


def parsear_canal(canal: str, num_dias: int) -> list:
    hoy = datetime.now(TZ_LJ).date()
    eventos_crudos = []

    for d in range(num_dias):
        fecha_target = hoy + timedelta(days=d)
        fecha_str = fecha_target.strftime("%Y%m%d")

        try:
            html = fetch_dia(canal, fecha_str)
        except Exception as e:
            print(f"[AVISO] No se pudo obtener {canal} para el día {fecha_str}: {e}", file=sys.stderr)
            continue

        soup = BeautifulSoup(html, "html.parser")
        oddaja_links = soup.find_all("a", href=re.compile(r"/oddaja/"))
        vistos_en_dia = set()

        for a_tag in oddaja_links:
            raw_title = a_tag.get_text(" ", strip=True)
            if not raw_title or len(raw_title) < 2:
                continue

            container = a_tag.parent
            m_time = None

            for _ in range(4):
                if container:
                    txt = container.get_text(" ", strip=True)
                    m = TIME_REGEX.search(txt)
                    if m:
                        m_time = m
                        break
                    if container.parent:
                        container = container.parent

            if not m_time:
                continue

            hora_start_str = m_time.group(1)
            hora_end_str = m_time.group(2) if m_time.group(2) else None

            # Limpiar el título y extraer categoría
            titulo, categoria = limpiar_titulo_y_categoria(raw_title)

            if not titulo:
                continue

            hh, mm = [int(x) for x in hora_start_str.split(":")]
            inicio_dt = datetime(fecha_target.year, fecha_target.month, fecha_target.day, hh, mm, tzinfo=TZ_LJ)

            fin_dt = None
            if hora_end_str:
                hh_e, mm_e = [int(x) for x in hora_end_str.split(":")]
                fin_dt = datetime(fecha_target.year, fecha_target.month, fecha_target.day, hh_e, mm_e, tzinfo=TZ_LJ)
                if fin_dt <= inicio_dt:
                    fin_dt += timedelta(days=1)

            clave = (inicio_dt, titulo)
            if clave not in vistos_en_dia:
                vistos_en_dia.add(clave)
                eventos_crudos.append({
                    "canal": canal,
                    "inicio": inicio_dt,
                    "fin_explicit": fin_dt,
                    "titulo": titulo,
                    "categoria": categoria,
                })

    # Ordenar y desduplicar por hora de inicio
    eventos_ordenados = sorted(eventos_crudos, key=lambda x: x["inicio"])
    eventos_unicos = []
    horas_vistas = set()

    for ev in eventos_ordenados:
        if ev["inicio"] not in horas_vistas:
            horas_vistas.add(ev["inicio"])
            eventos_unicos.append(ev)

    # Ajustar horas de fin
    for i in range(len(eventos_unicos)):
        ev = eventos_unicos[i]
        if ev["fin_explicit"] and ev["fin_explicit"] > ev["inicio"]:
            ev["fin"] = ev["fin_explicit"]
        elif i + 1 < len(eventos_unicos):
            ev["fin"] = eventos_unicos[i + 1]["inicio"]
        else:
            ev["fin"] = ev["inicio"] + timedelta(hours=2)

        if ev["fin"] <= ev["inicio"]:
            ev["fin"] = ev["inicio"] + timedelta(minutes=30)

    return eventos_unicos


def xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def construir_xmltv(canales: list, eventos_por_canal: dict) -> str:
    lineas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
        '<tv generator-info-name="delo.si-epg-scraper" generator-info-url="https://tvspored.delo.si">'
    ]

    for canal in canales:
        chan_id = f"{canal}.delo.si"
        nombre = canal.upper()
        lineas.append(f'  <channel id="{escape(chan_id)}">')
        lineas.append(f'    <display-name lang="sl">{escape(nombre)}</display-name>')
        
        # Inserción de la URL del logo si existe para el canal
        logo_url = LOGOS_CANALES.get(canal.lower())
        if logo_url:
            lineas.append(f'    <icon src="{escape(logo_url)}" />')

        lineas.append('  </channel>')

    for canal in canales:
        chan_id = f"{canal}.delo.si"
        for ev in eventos_por_canal.get(canal, []):
            lineas.append(
                f'  <programme start="{xmltv_time(ev["inicio"])}" '
                f'stop="{xmltv_time(ev["fin"])}" channel="{escape(chan_id)}">'
            )
            lineas.append(f'    <title lang="sl">{escape(ev["titulo"])}</title>')
            if ev["categoria"]:
                lineas.append(f'    <category lang="sl">{escape(ev["categoria"])}</category>')
            lineas.append('  </programme>')

    lineas.append('</tv>')
    return "\n".join(lineas)


def main():
    ap = argparse.ArgumentParser(description="Scraper EPG XMLTV desde tvspored.delo.si")
    # Los canales por defecto son sk1 y sporttv1 sin ser obligatorio pasar argumentos
    ap.add_argument("--canal", nargs="+", default=["sk1", "sporttv1"], help="Identificador del canal (por defecto: sk1 sporttv1)")
    ap.add_argument("--dias", type=int, default=7, help="Número de días a procesar (por defecto 7)")
    ap.add_argument("--salida", default="epg.xml", help="Ruta del archivo de salida XMLTV")
    args = ap.parse_args()

    eventos_por_canal = {}
    total = 0

    for canal in args.canal:
        print(f"Obteniendo datos para {canal} ({args.dias} días)...")
        eventos = parsear_canal(canal, args.dias)
        eventos_por_canal[canal] = eventos
        total += len(eventos)
        print(f" -> {len(eventos)} eventos procesados para {canal}")

    xml = construir_xmltv(args.canal, eventos_por_canal)
    with open(args.salida, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"\nProceso finalizado: {total} eventos escritos en {args.salida}")


if __name__ == "__main__":
    main()