"""
Canonical pollster names for the Wikipedia vote-intention tables.

Wikipedia writes the same house a dozen ways ("Buendía & Laredo",
"El Universal/Buendía & Laredo", "Buendía-Notimex") and glues the commissioning
outlet onto the name. canonical_pollster() splits a raw cell into

    pollster  the firm that fielded the poll
    cliente   the outlet or party that commissioned or published it
    metodo    collection mode, when the page states one in parentheses

so house effects can be estimated per firm while the client stays available.
FAMILIA groups firms the same way dim_approval_pollster.familia does, so the
two pollster dimensions can be joined on it.

A raw name not listed in ALIASES aborts the scrape. Add it here rather than
letting it through under its raw spelling, which would split one house in two.
"""

from __future__ import annotations

import re

# Normalized raw name (footnotes and parentheticals removed) -> (pollster, cliente)
ALIASES: dict[str, tuple[str, str | None]] = {
    "24 Horas": ("24 Horas", None),
    "24 horas": ("24 Horas", None),
    "ARCOP": ("Arcop", None),
    "Arcop": ("Arcop", None),
    "Alduncin": ("Alduncin y Asociados", None),
    "Alducin y Asociados": ("Alduncin y Asociados", None),
    "Algoritmo": ("Algoritmo", None),
    "Altica": ("Áltica Research", None),
    "Áltica Research": ("Áltica Research", None),
    "Áltica": ("Áltica Research", None),
    "AtlasIntel": ("AtlasIntel", None),
    "Arias Consultores": ("Arias Consultores", None),
    "BGC": ("BGC", None),
    "BGC-Excelsior": ("BGC", "Excélsior"),
    "BGC- Excelsior": ("BGC", "Excélsior"),
    "BGC- Excélsior": ("BGC", "Excélsior"),
    "BGC, Beltrán y Asociados": ("BGC", None),
    "Ulises Beltran y Asociados": ("BGC", None),
    "BGC–Excélsior": ("BGC", "Excélsior"),
    # Ulises Beltrán founded BGC (Beltrán, Juárez y Asociados).
    "Ulises Beltrán- La Crónica": ("BGC", "La Crónica"),
    "Belden": ("Belden", None),
    "Berumen": ("Berumen y Asociados", None),
    "Berumen-El Universal": ("Berumen y Asociados", "El Universal"),
    "Berumen y Asociados": ("Berumen y Asociados", None),
    "Berumen y asociados": ("Berumen y Asociados", None),
    "Berumen/Ipsos": ("Berumen/Ipsos", None),
    "Becerra Mizuno y Asociados": ("Becerra Mizuno y Asociados", None),
    "Bloomberg": ("Bloomberg", None),
    "Buendia & Laredo": ("Buendía y Laredo", None),
    "Buendía & Laredo": ("Buendía y Laredo", None),
    "Buendía y Laredo": ("Buendía y Laredo", None),
    "Buendía-Notimex": ("Buendía y Laredo", "Notimex"),
    "El Universal /Buendía & Laredo": ("Buendía y Laredo", "El Universal"),
    "El Universal/Buendía & Laredo": ("Buendía y Laredo", "El Universal"),
    "El Universal / Buendía & Laredo": ("Buendía y Laredo", "El Universal"),
    "Buendía & Márquez": ("Buendía y Márquez", None),
    "Buendía y Márquez": ("Buendía y Márquez", None),
    "CE Research": ("CE Research", None),
    "CEDE": ("CEDE", None),
    "CEO": ("CEO (UdeG)", None),
    "CEPROSEPP": ("CEPROSEPP", None),
    "CM Político": ("CM Político", None),
    "CNA/EPI": ("CNA/EPI", None),
    "CNIRT": ("CNIRT", None),
    "CONTEO": ("Conteo", None),
    "Conteo SC": ("Conteo", None),
    "Campaigns & Elections": ("Campaigns & Elections", None),
    "C&E Mexico": ("Campaigns & Elections", None),
    "CELAG": ("CELAG", None),
    "Consulta Mitofsky": ("Consulta Mitofsky", None),
    "Mitofsky": ("Consulta Mitofsky", None),
    "Mitofsky - Televisa": ("Consulta Mitofsky", "Televisa"),
    "Mitofsky-Televisa": ("Consulta Mitofsky", "Televisa"),
    "Mitofksy": ("Consulta Mitofsky", None),
    "Covarrubias": ("Covarrubias y Asociados", None),
    "Covarrubias y Asociados": ("Covarrubias y Asociados", None),
    "Cripeso": ("Cripeso", None),
    "Culmen Consultores": ("Culmen Consultores", None),
    "D. Watch": ("D. Watch", None),
    "Defoe Spin": ("Defoe Spin", None),
    # María de las Heras founded Demotecnia.
    "De la Heras Demotecnia": ("Demotecnia", None),
    "De las Heras Demotecnia": ("Demotecnia", None),
    "De Las Heras Demotecnia": ("Demotecnia", None),
    "Demotecnia": ("Demotecnia", None),
    "Demotecnia-Milenio": ("Demotecnia", "Milenio"),
    "María de las Heras- Milenio": ("Demotecnia", "Milenio"),
    "Uno TV/María de la Heras": ("Demotecnia", "Uno TV"),
    "Uno TV/María de las Heras": ("Demotecnia", "Uno TV"),
    "Demoscopia": ("Demoscopia Digital", None),
    "Demoscopia Digital": ("Demoscopia Digital", None),
    "Efekto TV": ("Efekto TV", None),
    "Eje Central": ("Eje Central", None),
    "El Diario de Ciudad Juárez": ("El Diario de Ciudad Juárez", None),
    "El Economista": ("El Economista", None),
    "El Financiero": ("El Financiero", None),
    "El Heraldo": ("El Heraldo", None),
    "El Heraldo Group": ("El Heraldo", None),
    "El Imparcial": ("El Imparcial", None),
    "El Imparcial de Hermosillo": ("El Imparcial", None),
    "El Norte": ("El Norte", None),
    "El País": ("El País", None),
    "El Pais": ("El País", None),
    "El Universal": ("El Universal", None),
    "Universal": ("El Universal", None),
    "El menos peor": ("El menos peor", None),
    "Elección 2012 México": ("Elección 2012 México", None),
    "Electoralia": ("Electoralia", None),
    "Enkoll": ("Enkoll", None),
    "Excélsior": ("Excélsior", None),
    "Excelsior": ("Excélsior", None),
    "Focus Asesores": ("Focus Asesores", None),
    "Fox News": ("Fox News", None),
    "Expansión Política": ("Expansión Política", None),
    "FactoMetrica": ("FactoMétrica", None),
    "FactoMétrica": ("FactoMétrica", None),
    "Factométrica": ("FactoMétrica", None),
    "Fishers": ("Fishers", None),
    "GANA": ("GANA", None),
    "GAUSCC": ("GAUSSC", None),
    "GAUSSC": ("GAUSSC", None),
    "GCE": ("GCE", None),
    "GCE- Diario Monitor": ("GCE", "Diario Monitor"),
    "GCE-Milenio": ("GCE", "Milenio"),
    "GEA": ("GEA-ISA", None),
    "GEA-ISA": ("GEA-ISA", None),
    "GEA/ISA- Milenio": ("GEA-ISA", "Milenio"),
    "GEA/ISA-Milenio": ("GEA-ISA", "Milenio"),
    "GEO/ETC": ("GEO/ETC", None),
    "Gii 360": ("Gii360", None),
    "Gii360": ("Gii360", None),
    "Grupo Impacto Inteligente 360°": ("Gii360", None),
    "Grupo Impacto Inteligente 360º": ("Gii360", None),
    "UNOTV/Grupo Impacto Inteligente 360°": ("Gii360", "Uno TV"),
    "GobernArte": ("GobernArte", None),
    "Gobernarte": ("GobernArte", None),
    "Grupo Reforma": ("Reforma", None),
    "Reforma": ("Reforma", None),
    "IPSOS-BIMSA": ("Ipsos-Bimsa", None),
    "Ipsos-Bimsa": ("Ipsos-Bimsa", None),
    "Ipsos/Bimsa": ("Ipsos-Bimsa", None),
    "Ipsos": ("Ipsos", None),
    # BIMSA became Ipsos-Bimsa when Ipsos bought it
    "BIMSA": ("Ipsos-Bimsa", None),
    "Indemerc": ("Indemerc", None),
    "Indermec": ("Indemerc", None),
    "Indermerc": ("Indemerc", None),
    "Kapitolio": ("Kapitolio", None),
    "La Crónica de Baja California": ("La Crónica de Baja California", None),
    "La Jornada /": ("La Jornada", None),
    "La Jornada": ("La Jornada", None),
    "La Silla Rota": ("La Silla Rota", None),
    "LaEncuesta.mx": ("LaEncuesta.mx", None),
    "LaEncuesta.MX": ("LaEncuesta.mx", None),
    "MEBA": ("MEBA", None),
    "Massive Caller": ("Massive Caller", None),
    "Marketing Político": ("Marketing Político", None),
    "Mendoza Blanco": ("Mendoza Blanco & Asociados", None),
    "Mendoza Blanco & Asociados": ("Mendoza Blanco & Asociados", None),
    "MetaMetrics": ("MetaMetrics", None),
    "Metrics MX": ("Metrics MX", None),
    "MetricsMX": ("Metrics MX", None),
    "Milenio": ("Milenio", None),
    "Milenio Diario": ("Milenio", None),
    "Milenio/Nielsen": ("Nielsen", "Milenio"),
    "Mund Opinion": ("Mund", None),
    "Mund/Dalla": ("Mund", None),
    "México Elige": ("México Elige", None),
    "Oraculus": ("Oraculus", None),
    "Parametria": ("Parametría", None),
    "Parametría": ("Parametría", None),
    "El Sol de México/Parametría": ("Parametría", "El Sol de México"),
    "El Sol de México /Parametría": ("Parametría", "El Sol de México"),
    "OEM-Parametría": ("Parametría", "OEM"),
    "Parametría- Excélsior": ("Parametría", "Excélsior"),
    # Parámetro Investigación is a different house from Parametría.
    "Parámetro": ("Parámetro", None),
    "Pearson": ("Pearson", None),
    "Poder360": ("Poder360", None),
    "Polls.mx": ("Polls.mx", None),
    "Pop Group": ("Pop Group", None),
    "Quantum": ("Quantum", None),
    "Radio sin Lema": ("Radio sin Lema", None),
    "Reuters": ("Reuters", None),
    "Reuters/Zogby": ("Zogby", "Reuters"),
    "U. Miami /Zogby": ("Zogby", "U. Miami"),
    "Zogby": ("Zogby", None),
    "Revista 32": ("Revista 32", None),
    "Revista EMET": ("Revista EMET", None),
    "Rubrum": ("Rubrum", None),
    "SABA": ("SABA", None),
    "SDP Noticias": ("SDP Noticias", None),
    "SNTE-PANAL": ("SNTE-PANAL", None),
    "Simulacro ITAM": ("Simulacro ITAM", None),
    "Simulacro Nacional Universitario": ("Simulacro Nacional Universitario", None),
    "Simulacro Universitario 2024": ("Simulacro Universitario 2024", None),
    "Statistical Research Corporation": ("Statistical Research Corporation", None),
    "TResearch": ("TResearch", None),
    "Technomgmt": ("Technomgmt", None),
    "Telemundo": ("Telemundo", None),
    "TV Azteca": ("TV Azteca", None),
    "Tendencia mx": ("Tendencia mx", None),
    "UNAM": ("UNAM", None),
    "UNAM , Universidad Harvard y la Universidad Autónoma de Guadalajara": (
        "UNAM-Harvard-UAG", None),
    "Univision": ("Univision", None),
    "Urna abierta": ("Urna Abierta", None),
    "V. Voto": ("V. Voto", None),
    "Varela y Asociados": ("Varela y Asociados", None),
    "Votia": ("Votia Research", None),
    "Votia Research": ("Votia Research", None),
}

# Same grouping values as dim_approval_pollster.familia, where the house overlaps.
FAMILIA = {
    "Buendía y Laredo": "Buendía (Laredo / Márquez)",
    "Buendía y Márquez": "Buendía (Laredo / Márquez)",
    "Ipsos": "Ipsos",
    "Ipsos-Bimsa": "Ipsos",
    "Berumen/Ipsos": "Ipsos",
    "Consulta Mitofsky": "Consulta",
    "Covarrubias y Asociados": "Covarrubias y Asoc",
    "Parametría": "Parametria",
    "Varela y Asociados": "Varela y Asoc",
}

# Parenthetical notes that describe how the poll was collected.
METODO = [
    (r"redes sociales|twitter|monitoreo en red|monitereo", "redes_sociales"),
    (r"v[ií]a web", "web"),
    (r"plazas p[uú]blicas", "plazas_publicas"),
    (r"urna simulada", "urna_simulada"),
    (r"cara a cara", "cara_a_cara"),
]
# Parentheticals naming the party that commissioned the poll.
PARTY_CLIENT = {"PAN", "PRI", "PRD"}


def familia(pollster: str) -> str:
    return FAMILIA.get(pollster, pollster)


def canonical_pollster(raw: str) -> tuple[str | None, str | None, str | None]:
    """(pollster, cliente, metodo) for a raw table cell; pollster None if unknown."""
    text = re.sub(r"\[[^\]]*\]", " ", raw or "")
    # English Wikipedia leaves archive notices and footnote marks in the cell
    text = re.sub(r"\s*Archived\b.*$", "", text)
    text = re.sub(r"\s*[*%]+\s*$", "", text)
    notes = re.findall(r"\(([^)]*)\)", text)
    text = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", text)).strip(" .")

    metodo = None
    party = None
    for note in notes:
        note = note.strip()
        if note in PARTY_CLIENT:
            party = note
        for pattern, label in METODO:
            if re.search(pattern, note, re.I) and metodo is None:
                metodo = label

    match = ALIASES.get(text)
    if match is None:
        return None, None, metodo
    pollster, cliente = match
    return pollster, cliente or party, metodo
