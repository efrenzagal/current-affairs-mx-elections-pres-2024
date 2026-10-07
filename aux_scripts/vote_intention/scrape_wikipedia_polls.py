"""
Scrape federal vote-intention polls from Wikipedia into one long CSV.

Spanish Wikipedia is the primary source. English Wikipedia is scraped as well
because neither edition is complete — the Spanish 2024 annex misses Reforma,
AtlasIntel and MEBA's final polls, the English page misses Buendía y Márquez
and Enkoll's. An English poll is kept only when no Spanish row is the same
poll (same election, question level and house, fieldwork ending within
MATCH_DAYS); see match_sources(). Every pairing is written to
en_es_matches.csv for review.

Output: aux_scripts/vote_intention/vote_intention_polls.csv, one row per
(poll, option). Poll metadata repeats on every option row so the file can be
reviewed and filtered on its own; vote_intention/ingest.py splits it back into
a poll table and an option table.

Every page is pinned to a revision (oldid), so a rerun reproduces the same CSV
and table indices in TABLES keep pointing at the same tables. To pick up later
edits, bump the revid, rerun with --list-tables, and check the section names
still match what TABLES expects — the scrape aborts when they don't.

Elections still ahead (LIVE, today DIP_MR_2027) work differently because their
pages gain a poll every week or two:
  * their revisions are pinned in live_pages.json, and --refresh moves the pin
    to each page's latest revision, rewriting the JSON only when the scrape
    comes out clean;
  * their tables are found by heading ("Voting intention > 2026"), so the
    table a new year adds at the top is picked up without re-indexing, and a
    table with a pollster column that no spec covers aborts the scrape;
  * their poll_ids come from house and date, not table row, so a poll keeps
    its id when newer rows are added above it;
  * the English page is the primary source (PRIMARY): it is updated first and
    lists the parties registered in 2026 (PAZ, SOMOS).

Each option carries party_keys: the fact_casilla_vote.party_key values whose
votes add up to that option on election day, separated by "|". A candidate
backed by a coalition sums every party and combination key of that coalition,
so the poll can be compared to the result without knowing how each year's
returns encode coalition ballots. Options with no counterpart on the ballot
(hypothetical candidates, "otros", undecided) leave it blank.

Usage:
    /usr/bin/python3 aux_scripts/vote_intention/scrape_wikipedia_polls.py
    /usr/bin/python3 aux_scripts/vote_intention/scrape_wikipedia_polls.py --refresh
    /usr/bin/python3 aux_scripts/vote_intention/scrape_wikipedia_polls.py --list-tables [es|en]
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pollsters import canonical_pollster, familia  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "raw_vote_intention"
OUT_CSV = Path(__file__).resolve().parent / "vote_intention_polls.csv"
MATCHES_CSV = Path(__file__).resolve().parent / "en_es_matches.csv"
LIVE_PAGES_JSON = Path(__file__).resolve().parent / "live_pages.json"

USER_AGENT = "mx-elections-research/0.1 (efrenzagal@gmail.com)"

# An English poll is the same as a Spanish one when the house matches and the
# fieldwork ends within this many days (same month when either side only
# gives a month); the shares then must agree within MATCH_TOLERANCE points.
MATCH_DAYS = 3
MATCH_TOLERANCE = 1.0
# One edition may date a poll by publication and the other by fieldwork, so
# pairs up to this far apart still count as the same poll when the shares
# agree; disagreeing shares only make a conflict within MATCH_DAYS.
MATCH_DAYS_IF_EQUAL = 14

ELECTION_DATE = {
    "PRE_1994": date(1994, 8, 21),
    "PRE_2000": date(2000, 7, 2),
    "PRE_2006": date(2006, 7, 2),
    "DIP_MR_2009": date(2009, 7, 5),
    "PRE_2012": date(2012, 7, 1),
    "DIP_MR_2015": date(2015, 6, 7),
    "PRE_2018": date(2018, 7, 1),
    "DIP_MR_2021": date(2021, 6, 6),
    "PRE_2024": date(2024, 6, 2),
    "DIP_MR_2027": date(2027, 6, 6),
}

# Elections whose pages are pinned in live_pages.json and moved by --refresh.
LIVE = {"DIP_MR_2027"}

# Which edition's poll wins when both list it; Spanish unless named here.
PRIMARY = {"DIP_MR_2027": "en"}

# 1997 and 2003 have no poll tables in either language edition.
PAGES_ES = {
    "PRE_1994": ("Elecciones federales de México de 1994", 175411817),
    "PRE_2000": ("Elecciones federales de México de 2000", 175533078),
    "PRE_2006": ("Elecciones federales de México de 2006", 175399770),
    "DIP_MR_2009": ("Elecciones federales de México de 2009", 175177845),
    "PRE_2012": ("Elecciones federales de México de 2012", 173784466),
    "DIP_MR_2015": ("Elecciones federales de México de 2015", 174950575),
    "PRE_2018": (
        "Anexo:Encuestas de intención de voto para la elección presidencial "
        "de México de 2018", 175104723,
    ),
    "DIP_MR_2021": ("Elecciones federales de México de 2021", 171571136),
    "PRE_2024": (
        "Anexo:Encuestas y sondeos de intención de voto para las elecciones "
        "presidenciales de México de 2024", 174713499,
    ),
}

# 2009 has no poll table in English.
PAGES_EN = {
    "PRE_1994": ("1994 Mexican general election", 1371161917),
    "PRE_2000": ("2000 Mexican general election", 1376785492),
    "PRE_2006": ("2006 Mexican general election", 1374404132),
    "PRE_2012": ("2012 Mexican general election", 1371061971),
    "DIP_MR_2015": ("2015 Mexican legislative election", 1372681724),
    "PRE_2018": ("Opinion polling for the 2018 Mexican general election", 1306337786),
    "DIP_MR_2021": ("2021 Mexican legislative election", 1375998794),
    "PRE_2024": ("Opinion polling for the 2024 Mexican general election", 1374086463),
}

PAGES = {"es": PAGES_ES, "en": PAGES_EN}

# {election_id: {lang: {"title": ..., "revid": ...}}}
LIVE_PAGES = json.loads(LIVE_PAGES_JSON.read_text(encoding="utf-8"))
for _election_id, _by_lang in LIVE_PAGES.items():
    for _lang, _page in _by_lang.items():
        PAGES[_lang][_election_id] = (_page["title"], _page["revid"])

# Which tables on each pinned revision hold polls, and how to read them.
#   section       substring the table's heading path must contain (guards the index)
#   level         candidato | coalicion | partido — what respondents were offered
#   phase         campaign stage the page files the table under, or
#                 "por_fecha" for tables that span stages (set from PHASE_START)
#   kind          encuesta | sondeo | agregador | simulacro
#   basis         "efectiva" when the page says undecided were removed;
#                 otherwise None and share_basis() reads it off the numbers
#   fecha         what a bare "Fecha" column means on this table
# A live page's spec gives `match`, a regex on the heading path, in place of
# `table` and `section`: every table under a matching heading is scraped.
# Internal party primaries (2018) are left out: they are not a general-election
# question and have no counterpart in the returns.
TABLES_ES: dict[str, list[dict]] = {
    "PRE_1994": [
        dict(table=1, section="Encuestas", level="partido", phase="campaña",
             kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "PRE_2000": [
        dict(table=1, section="Encuestas", level="candidato", phase="campaña",
             kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "PRE_2006": [
        dict(table=2, section="Encuestas", level="candidato", phase="campaña",
             kind="encuesta", basis=None, fecha="publicacion"),
    ],
    "DIP_MR_2009": [
        dict(table=0, section="Encuestas", level="partido", phase="campaña",
             kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "PRE_2012": [
        # The page says this table is "preferencia efectiva (sin indecisos)".
        dict(table=1, section="Resultados de compañías encuestadoras",
             level="candidato", phase="campaña", kind="encuesta",
             basis="efectiva", fecha="publicacion"),
        # Newspaper, university and web polls of unstated design.
        dict(table=2, section="Sondeos, monitoreos y encuestas independientes",
             level="candidato", phase="campaña", kind="sondeo",
             basis=None, fecha="publicacion"),
    ],
    "DIP_MR_2015": [
        dict(table=0, section="Encuesta de opinión", level="partido",
             phase="campaña", kind="encuesta", basis=None,
             fecha="sin_especificar"),
    ],
    "PRE_2018": [
        dict(table=0, section="Campañas > Por partido político", level="partido",
             phase="campaña", kind="encuesta", basis=None, fecha="levantamiento"),
        dict(table=1, section="Campañas > Por candidato", level="candidato",
             phase="campaña", kind="encuesta", basis=None, fecha="levantamiento"),
        dict(table=2, section="Precampañas e intercampaña > Por coalición",
             level="coalicion", phase="precampaña", kind="encuesta",
             basis=None, fecha="levantamiento"),
        dict(table=3, section="Precampañas e intercampaña > Por candidato",
             level="candidato", phase="precampaña", kind="encuesta",
             basis=None, fecha="levantamiento"),
        dict(table=4, section="Anteriores al periodo electoral > Por partido",
             level="partido", phase="pre_electoral", kind="encuesta",
             basis=None, fecha="sin_especificar"),
        dict(table=5, section="Anteriores al periodo electoral > Por probable coalición",
             level="coalicion", phase="pre_electoral", kind="encuesta",
             basis=None, fecha="sin_especificar"),
        dict(table=6, section="Anteriores al periodo electoral > Por probable candidato",
             level="candidato", phase="pre_electoral", kind="encuesta",
             basis=None, fecha="sin_especificar"),
    ],
    "DIP_MR_2021": [
        dict(table=0, section="Encuestas", level="partido", phase="campaña",
             kind="encuesta", basis=None, fecha="levantamiento"),
    ],
    "PRE_2024": [
        dict(table=0, section="Encuestas por candidatos > 2024", level="candidato",
             phase="campaña", kind="encuesta", basis=None, fecha="levantamiento"),
        dict(table=1, section="Encuestas por candidatos > 2023", level="candidato",
             phase="precampaña", kind="encuesta", basis=None, fecha="levantamiento"),
        dict(table=2, section="Encuestas por coalición o partido político > 2024",
             level="coalicion", phase="campaña", kind="encuesta", basis=None,
             fecha="levantamiento"),
        dict(table=3, section="Encuestas por coalición o partido político > 2023",
             level="coalicion", phase="precampaña", kind="encuesta", basis=None,
             fecha="levantamiento"),
        dict(table=4, section="Agregaciones de sondeos", level="candidato",
             phase="campaña", kind="agregador", basis="efectiva",
             fecha="publicacion"),
        dict(table=5, section="Simulacros", level="candidato", phase="campaña",
             kind="simulacro", basis=None, fecha="levantamiento"),
        dict(table=6, section="Encuestas adicionales > Por candidatos",
             level="candidato", phase="campaña", kind="encuesta", basis=None,
             fecha="levantamiento"),
        dict(table=7, section="Encuestas adicionales > Por candidatos",
             level="candidato", phase="precampaña", kind="encuesta", basis=None,
             fecha="levantamiento"),
        dict(table=8, section="Encuestas adicionales > Por coalición",
             level="coalicion", phase="campaña", kind="encuesta", basis=None,
             fecha="levantamiento"),
    ],
    # One table per year; the page also has a party-list table, skipped
    # because it has no pollster column.
    "DIP_MR_2027": [
        dict(match=r"^Encuestas de opinión > \d{4}$", level="partido", phase="por_fecha",
             kind="encuesta", basis=None, fecha="levantamiento"),
    ],
}

# Hypothetical-candidate tables ("Possible candidates", 2024) and primaries
# are left out, as on the Spanish side.
TABLES_EN: dict[str, list[dict]] = {
    "PRE_1994": [
        dict(table=3, section="Opinion polls", level="partido", phase="campaña",
             kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "PRE_2000": [
        dict(table=3, section="Opinion polls", level="candidato", phase="campaña",
             kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "PRE_2006": [
        dict(table=4, section="Opinion polls", level="candidato", phase="campaña",
             kind="encuesta", basis=None, fecha="publicacion"),
    ],
    "PRE_2012": [
        dict(table=3, section="Opinion polls", level="candidato", phase="campaña",
             kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "DIP_MR_2015": [
        dict(table=1, section="Opinion polls", level="partido", phase="campaña",
             kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "PRE_2018": [
        dict(table=0, section="By coalitions", level="coalicion", phase="pre_electoral",
             kind="encuesta", basis=None, fecha="sin_especificar"),
        dict(table=1, section="By candidates (January–June 2018)", level="candidato",
             phase="por_fecha", kind="encuesta", basis=None, fecha="sin_especificar"),
        dict(table=2, section="By candidates (April–December 2017)", level="candidato",
             phase="pre_electoral", kind="encuesta", basis=None, fecha="sin_especificar"),
        dict(table=3, section="By political parties", level="partido",
             phase="pre_electoral", kind="encuesta", basis=None, fecha="sin_especificar"),
    ],
    "DIP_MR_2021": [
        dict(table=2, section="Opinion polls", level="partido", phase="campaña",
             kind="encuesta", basis=None, fecha="levantamiento"),
    ],
    "PRE_2024": [
        dict(table=0, section="Polling aggregations", level="candidato", phase="campaña",
             kind="agregador", basis="efectiva", fecha="levantamiento"),
        dict(table=1, section="Campaigning period", level="candidato", phase="campaña",
             kind="encuesta", basis=None, fecha="levantamiento"),
        dict(table=2, section="Precampaigning and intercampaigning", level="candidato",
             phase="precampaña", kind="encuesta", basis=None, fecha="levantamiento"),
        dict(table=3, section="Prior to the electoral period", level="candidato",
             phase="pre_electoral", kind="encuesta", basis=None, fecha="levantamiento"),
        dict(table=5, section="By alliances", level="coalicion", phase="por_fecha",
             kind="encuesta", basis=None, fecha="levantamiento"),
    ],
    "DIP_MR_2027": [
        dict(match=r"^Voting intention > \d{4}$", level="partido", phase="por_fecha",
             kind="encuesta", basis=None, fecha="levantamiento"),
    ],
}

TABLES = {"es": TABLES_ES, "en": TABLES_EN}

# Start of precampaign and campaign, for tables filed across stages.
PHASE_START = {
    "PRE_2018": (date(2017, 12, 14), date(2018, 3, 30)),
    "PRE_2024": (date(2023, 11, 20), date(2024, 3, 1)),
    # INE's calendar for the 2026-2027 federal process (approved July 2026)
    "DIP_MR_2027": (date(2027, 1, 4), date(2027, 4, 4)),
}

# Warehouse keys per ballot option. A coalition candidate sums every key that
# appears on the ballot for any of its parties, alone or combined.
K_2012_EPN = "PRI|PVEM|C_PRI_PVEM"
K_2012_AMLO = "PRD|PT|MC|C_PRD_PT_MC|C_PRD_PT|C_PRD_MC|C_PT_MC"
K_2018_AMLO = "MORENA|PT|ENCUENTRO SOCIAL|PT_MORENA_PES|PT_MORENA|MORENA_PES|PT_PES"
K_2018_ANAYA = "PAN|PRD|MOVIMIENTO CIUDADANO|PAN_PRD_MC|PAN_PRD|PAN_MC|PRD_MC"
K_2018_MEADE = "PRI|PVEM|NUEVA ALIANZA|PRI_PVEM_NA|PRI_PVEM|PRI_NA|PVEM_NA"
K_2024_SHH = "MORENA|PT|PVEM|PT_MORENA|PVEM_MORENA|PVEM_PT|PVEM_PT_MORENA"
K_2024_FCM = "PAN|PRI|PRD|PAN_PRD|PAN_PRI|PAN_PRI_PRD|PRI_PRD"

# (pattern, option_kind, candidato, partido_coalicion, party_keys). The pattern
# is matched against "label || link titles" of the column header; first match
# wins, so person-specific patterns go before party ones. Hypothetical
# candidates who never reached the ballot get no party_keys.
OPTIONS: dict[str, list[tuple[str, str, str, str, str]]] = {
    "PRE_1994": [
        (r"^PRI\b", "partido", "Ernesto Zedillo", "PRI", "PRI"),
        (r"^PAN\b", "partido", "Diego Fernández de Cevallos", "PAN", "PAN"),
        (r"^PRD\b", "partido", "Cuauhtémoc Cárdenas", "PRD", "PRD"),
    ],
    "PRE_2000": [
        (r"Fox", "candidato", "Vicente Fox", "Alianza por el Cambio", "A. CAM."),
        (r"Labastida", "candidato", "Francisco Labastida", "PRI", "PRI"),
        (r"Cárdenas", "candidato", "Cuauhtémoc Cárdenas", "Alianza por México", "A. MEX."),
    ],
    "PRE_2006": [
        (r"Calderón", "candidato", "Felipe Calderón", "PAN", "PAN"),
        (r"Madrazo", "candidato", "Roberto Madrazo", "Alianza por México", "APM"),
        (r"Obrador", "candidato", "Andrés Manuel López Obrador",
         "Por el Bien de Todos", "PBT"),
        (r"Mercado", "candidato", "Patricia Mercado", "Alternativa", "ASDC"),
        (r"Campa", "candidato", "Roberto Campa", "Nueva Alianza", "NVA_A"),
    ],
    "DIP_MR_2009": [
        (r"^PAN\b", "partido", "", "PAN", "PAN"),
        (r"^PRD\b", "partido", "", "PRD", "PRD"),
        (r"^PRI\b", "partido", "", "PRI", "PRI"),
        (r"^SaM", "coalicion", "", "Salvemos a México (PT-Convergencia)",
         "PT|MC|C_PT_MC"),
        (r"^PVEM\b", "partido", "", "PVEM", "PVEM"),
        (r"^PSD\b", "partido", "", "PSD", "PSD"),
        (r"^PANAL\b", "partido", "", "PANAL", "PANAL"),
    ],
    "PRE_2012": [
        (r"Vázquez", "candidato", "Josefina Vázquez Mota", "PAN", "PAN"),
        (r"Peña Nieto", "candidato", "Enrique Peña Nieto",
         "Compromiso por México", K_2012_EPN),
        (r"Obrador", "candidato", "Andrés Manuel López Obrador",
         "Movimiento Progresista", K_2012_AMLO),
        (r"Quadri", "candidato", "Gabriel Quadri", "Nueva Alianza", "PANAL"),
    ],
    "DIP_MR_2015": [
        (r"^PRI\b", "partido", "", "PRI", "PRI"),
        (r"^PAN\b", "partido", "", "PAN", "PAN"),
        (r"^PRD\b", "partido", "", "PRD", "PRD"),
        (r"^PVEM\b", "partido", "", "PVEM", "PVEM"),
        (r"^PT\b", "partido", "", "PT", "PT"),
        (r"^PANAL\b", "partido", "", "PANAL", "PANAL"),
        (r"^MC\b", "partido", "", "MC", "MC"),
        (r"^Morena\b", "partido", "", "MORENA", "MORENA"),
        (r"^PH\b", "partido", "", "PH", "PH"),
        (r"^PES\b", "partido", "", "PES", "PES"),
    ],
    "PRE_2018": [
        # People first: a candidate's link also names their party in some headers.
        (r"López Obrador|^Obrador", "candidato", "Andrés Manuel López Obrador",
         "Juntos Haremos Historia", K_2018_AMLO),
        (r"Ricardo Anaya|^Anaya", "candidato", "Ricardo Anaya",
         "Por México al Frente", K_2018_ANAYA),
        (r"Meade", "candidato", "José Antonio Meade", "Todos por México",
         K_2018_MEADE),
        (r"Margarita Zavala|^Zavala", "candidato", "Margarita Zavala",
         "Independiente", "CAND_IND_01"),
        (r"Jaime Rodríguez Calderón", "candidato", "Jaime Rodríguez Calderón",
         "Independiente", "CAND_IND_02"),
        (r"Ríos Piter|^Piter", "candidato", "Armando Ríos Piter", "Independiente", ""),
        (r"Osorio Chong|^Chong", "candidato", "Miguel Ángel Osorio Chong", "PRI", ""),
        (r"Mancera", "candidato", "Miguel Ángel Mancera", "PRD", ""),
        (r"Eruviel|^Ávila", "candidato", "Eruviel Ávila", "PRI", ""),
        (r"Moreno Valle|^Valle", "candidato", "Rafael Moreno Valle", "PAN", ""),
        (r"Nuño", "candidato", "Aurelio Nuño", "PRI", ""),
        (r"Por México al Frente", "coalicion", "Ricardo Anaya",
         "Por México al Frente", K_2018_ANAYA),
        (r"Juntos Haremos Historia", "coalicion", "Andrés Manuel López Obrador",
         "Juntos Haremos Historia", K_2018_AMLO),
        (r"Todos por México", "coalicion", "José Antonio Meade",
         "Todos por México", K_2018_MEADE),
        # Probable alliances polled in 2017, named only by their party logos.
        # PAN-PRD-MC and PRI-PVEM-NA became the actual Anaya and Meade
        # coalitions; MORENA-PT is mapped to its own keys because PES joined
        # only later.
        (r"\|\| PAN \| PRD \| MC$", "coalicion", "", "PAN-PRD-MC", K_2018_ANAYA),
        (r"\|\| MORENA \| PT$", "coalicion", "", "MORENA-PT", "MORENA|PT|PT_MORENA"),
        (r"\|\| PRI \| PVEM \| NA$", "coalicion", "", "PRI-PVEM-NA", K_2018_MEADE),
        # English page: the alliance as finally formed, with PES
        (r"\|\| MORENA \| PT \| PES$", "coalicion", "Andrés Manuel López Obrador",
         "Juntos Haremos Historia", K_2018_AMLO),
        (r"[Ii]ndependiente|[Ii]ndependent politician", "candidato", "", "Independiente",
         "CAND_IND_01|CAND_IND_02"),
        (r"Revolucionario Institucional|^PRI\b", "partido", "", "PRI", "PRI"),
        (r"Acción Nacional|^PAN\b", "partido", "", "PAN", "PAN"),
        (r"Revolución Democrática|^PRD\b", "partido", "", "PRD", "PRD"),
        (r"Partido del Trabajo|^PT\b", "partido", "", "PT", "PT"),
        (r"Verde Ecologista|^PVEM\b", "partido", "", "PVEM", "PVEM"),
        (r"Movimiento Ciudadano|^MC\b", "partido", "", "MC", "MOVIMIENTO CIUDADANO"),
        (r"Nueva Alianza|^NA\b", "partido", "", "PANAL", "NUEVA ALIANZA"),
        (r"Morena|^MORENA\b", "partido", "", "MORENA", "MORENA"),
        (r"Encuentro Social|^PES\b", "partido", "", "PES", "ENCUENTRO SOCIAL"),
    ],
    "DIP_MR_2021": [
        (r"Morena|Regeneración Nacional", "partido", "", "MORENA", "MORENA"),
        (r"Revolucionario Institucional|Institutional Revolutionary", "partido", "", "PRI", "PRI"),
        (r"Acción Nacional|National Action", "partido", "", "PAN", "PAN"),
        (r"Revolución Democrática|Democratic Revolution", "partido", "", "PRD", "PRD"),
        (r"Movimiento Ciudadano|Citizens' Movement", "partido", "", "MC", "MC"),
    ],
    # Solo keys as fact_casilla_vote spells them for DIP_MR_2024; the ingest
    # checks them once 2027 returns are loaded. PAZ and SOMOS, registered in
    # 2026, get keys when INE publishes how it codes them.
    "DIP_MR_2027": [
        (r"Morena", "partido", "", "MORENA", "MORENA"),
        (r"Revolucionario Institucional|Institutional Revolutionary", "partido", "", "PRI", "PRI"),
        (r"Acción Nacional|National Action", "partido", "", "PAN", "PAN"),
        (r"Verde Ecologista|Ecologist Green", "partido", "", "PVEM", "PVEM"),
        (r"Partido del Trabajo|Labor Party", "partido", "", "PT", "PT"),
        (r"Movimiento Ciudadano|Citizens' Movement", "partido", "", "MC", "MC"),
        (r"^PAZ\b|Partido Paz|Societies of Peace", "partido", "", "PAZ", ""),
        (r"^SOMOS\b|^Somos\b|We Are", "partido", "", "SOMOS", ""),
    ],
    "PRE_2024": [
        (r"Sheinbaum", "candidato", "Claudia Sheinbaum",
         "Sigamos Haciendo Historia", K_2024_SHH),
        (r"Gálvez", "candidato", "Xóchitl Gálvez",
         "Fuerza y Corazón por México", K_2024_FCM),
        (r"Máynez", "candidato", "Jorge Álvarez Máynez", "MC", "MC"),
        # MC's first nominee; withdrew in Dec 2023 and was replaced by Máynez.
        (r"Samuel García|^García", "candidato", "Samuel García", "MC", "MC"),
        # Never qualified for the ballot.
        (r"Verástegui", "candidato", "Eduardo Verástegui", "Independiente", ""),
        (r"Sigamos Haciendo Historia|^SHH$", "coalicion", "Claudia Sheinbaum",
         "Sigamos Haciendo Historia", K_2024_SHH),
        (r"Fuerza y Corazón|^FCM$", "coalicion", "Xóchitl Gálvez",
         "Fuerza y Corazón por México", K_2024_FCM),
        (r"Movimiento Ciudadano|Citizens' Movement|^MC$", "coalicion", "", "MC", "MC"),
        (r"[Ii]ndependiente", "coalicion", "", "Independiente", ""),
    ],
}

# Columns that are not ballot options. Checked against the bare label.
META_COLUMNS = [
    (r"^(Encuestadora|Agregadora|Organizador|Fuente de agregado)$", "pollster"),
    (r"^(Poll [Ss]ource|Polling (firm|company)|Pollsters?|Agency|Publisher)$", "pollster"),
    (r"^Source of poll aggregation", "pollster"),
    (r"^(Levantamiento|Fecha de realizaci|Fecha de encuestas)", "levantamiento"),
    (r"^(Fieldwork date|Date of Poll|Dates administered|Date\(s\) conducted)$", "levantamiento"),
    (r"^(Publicaci|Date Published|Dates updated)", "publicacion"),
    (r"^(Fecha|Date)$", "fecha"),
    (r"^(Muestra|Sample|Sample [Ss]ize)$", "muestra"),
    (r"^(Margen de error|Margin of [Ee]rror)$", "margen"),
    (r"^(Notas|Remarks)$", "notas"),
    (r"^(Ventaja|Diferencia|Ref\.|Fuente|Cualquiera|Lead)$", "skip"),
]
RESIDUAL_OPTIONS = [
    # "Others/ Undecided", "Other/None", "Otro/Ninguno Nulo/Blanco": a minor-
    # candidate share fused with non-choices. Counting it as "otros" would put
    # undecided back into the base when shares are made effective, so it is a
    # residual like undecided.
    (r"^(Otros?|Others?)\s*/\s*(Ninguno|None|Undecided)", "otros_indecisos"),
    (r"^(Otros?|Others?)\b", "otros"),
    (r"^(Ninguno|None|No-one)\b", "ninguno"),
    (r"^(Blanco|Nulo)$", "nulo_blanco"),
    (r"^(Indecisos|Indefinidos|NS/NC|NC/NS|N/NS/NC|Undecided|Undeclared|Not mentioned"
     r"|Und\. / no ans)\b", "indecisos"),
]

# Dates that contradict the table they sit in. Keyed by poll_id; the raw text
# stays in the CSV and the correction is noted in notas.
DATE_CORRECTIONS = {
    # Listed between 6-8 Jan 2024 and 3-5 Nov 2023 in a newest-first table, and
    # a November 2024 field date would fall after the election.
    "PRE_2024_t08_r006": ("fecha", "15 a 21 de noviembre de 2023"),
}

# Rows whose note says they are not vote intention: 2012's table mixes in
# post-debate snap polls on who won the debate.
NON_POLL_NOTE = re.compile(r"conteo rápido al cierre del debate", re.I)

# Rows that sit in a poll table but are not polls.
NON_POLL_ROW = re.compile(
    r"^(Promedio|RESULTADO|Resultado|Elecciones federales|Average|Election result"
    r"|Federal election|Presidential election)", re.I
)

MONTHS = {
    "enero": 1, "ene": 1, "jan": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4, "apr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
    "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "aug": 8, "septiembre": 9,
    "setiembre": 9, "sept": 9, "sep": 9, "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

CSV_COLUMNS = [
    "source", "source_url", "source_revid", "source_section", "source_table",
    "source_row", "election_id", "election_date", "poll_id", "poll_kind",
    "intention_level", "phase", "share_basis", "pollster_raw", "pollster",
    "cliente", "metodo", "familia", "fecha_levantamiento_raw", "fecha_publicacion_raw", "fecha_raw",
    "fieldwork_start", "fieldwork_end", "published_date", "reference_date",
    "reference_date_type", "date_precision", "sample_raw", "sample_size",
    "margin_raw", "margin_of_error", "notas", "poll_source_url", "option_order", "opcion",
    "option_kind", "candidato", "partido_coalicion", "party_keys", "pct_raw", "pct",
]


# ── fetching ──────────────────────────────────────────────────────────────────

def fetch(lang: str, title: str, revid: int) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{lang}_{revid}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(dict(
        action="parse", oldid=revid, prop="text|sections|revid",
        format="json", formatversion=2,
    ))
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            payload = json.load(urllib.request.urlopen(request, timeout=60))
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            time.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(f"rate-limited fetching {title} ({revid})")
    if "error" in payload:
        raise RuntimeError(f"{title} ({revid}): {payload['error']}")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    time.sleep(3)
    return payload


def source_url(lang: str, title: str, revid: int) -> str:
    return (
        f"https://{lang}.wikipedia.org/w/index.php?"
        + urllib.parse.urlencode({"title": title, "oldid": revid})
    )


# ── table parsing ─────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    text = text.replace("​", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def cell_value(cell) -> tuple[str, str, tuple[str, ...]]:
    for junk in cell.select("sup.reference, style, .sortkey"):
        junk.decompose()
    links = tuple(a["title"] for a in cell.find_all("a") if a.get("title"))
    return cell.name, clean_text(cell.get_text(" ", strip=True)), links


def table_grid(table) -> list[list[tuple[str, str, tuple[str, ...]]]]:
    """Rows of (tag, text, link titles), with rowspan/colspan expanded."""
    grid = []
    pending: dict[tuple[int, int], tuple] = {}
    for r, tr in enumerate(table.find_all("tr")):
        row, c = [], 0
        cells = iter(tr.find_all(["th", "td"], recursive=False))
        while True:
            while (r, c) in pending:
                row.append(pending.pop((r, c)))
                c += 1
            cell = next(cells, None)
            if cell is None:
                break
            rowspan = int(re.sub(r"\D", "", cell.get("rowspan", "1")) or 1)
            colspan = int(re.sub(r"\D", "", cell.get("colspan", "1")) or 1)
            value = cell_value(cell)
            for _ in range(colspan):
                row.append(value)
                for extra in range(1, rowspan):
                    pending[(r + extra, c)] = value
                c += 1
        grid.append(row)
    return grid


def row_source_urls(table) -> list[str]:
    """The first external link each row's footnotes cite, by <tr> index.

    That link is the poll's own release or the article reporting it, which is
    what a reader needs to check a number. Runs before table_grid, which
    strips the footnote markers. Rows citing nothing get "".
    """
    root = list(table.parents)[-1]
    urls = []
    for tr in table.find_all("tr"):
        url = ""
        for ref in tr.select("sup.reference a[href^='#']"):
            note = root.find(id=ref["href"][1:])
            link = note.select_one("a.external[href]") if note else None
            if link:
                url = link["href"]
                url = f"https:{url}" if url.startswith("//") else url
                break
        urls.append(url)
    return urls


def heading_path(table) -> str:
    path = {}
    for heading in table.find_all_previous(["h2", "h3", "h4"]):
        path.setdefault(heading.name, clean_text(heading.get_text(" ", strip=True)))
        if heading.name == "h2":
            break
    return " > ".join(path[k] for k in ("h2", "h3", "h4") if k in path)


def page_tables(payload: dict):
    soup = BeautifulSoup(payload["parse"]["text"], "lxml")
    return soup.select("table.wikitable")


def header_columns(grid) -> list[tuple[str, tuple[str, ...]]]:
    """One (label, links) per column, merged across the header rows.

    Headers stack a candidate name over a party-colour strip or a logo, so the
    label comes from whichever header row has text and the links are pooled.
    """
    # Party-colour strips between header rows are empty <td> cells, so a row
    # counts as header when every cell that has text is a <th>.
    # An event row ("17 November 2023 | García becomes the sole candidate")
    # can be marked up in <th> too; it starts with a date, a header never does.
    header_rows = []
    for row in grid:
        if row and all(tag == "th" for tag, text, _ in row if text):
            if parse_point(row[0][1]).keys() >= {"d", "m"}:
                break
            header_rows.append(row)
        else:
            break
    width = max(len(row) for row in grid)
    columns = []
    for c in range(width):
        label, links = "", []
        for row in header_rows:
            if c >= len(row):
                continue
            _, text, cell_links = row[c]
            if text and not label:
                label = text
            elif text and text != label and not re.search(r"\(\d{4}\)$", label):
                label = text  # deepest non-empty label wins (e.g. "Calderón PAN")
            links.extend(l for l in cell_links if l not in links)
        columns.append((label, tuple(links)))
    return columns, len(header_rows)


# ── value parsing ─────────────────────────────────────────────────────────────

def is_blank(text: str) -> bool:
    # "Did not exist": a party registered after the poll was taken (2027's PAZ, SOMOS)
    return clean_text(text).lower() in {"", "—", "-", "–", "sin datos", "n/d",
                                        "no data", "not shown", "n/a",
                                        "did not exist", "no existía"}


def parse_pct(text: str) -> float | None:
    text = clean_text(text).replace(",", ".")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%?", text)
    return float(match.group(1)) if match else None


def parse_int(text: str) -> int | None:
    digits = re.sub(r"[\s.,]", "", clean_text(text))
    match = re.match(r"^(\d+)", digits)
    return int(match.group(1)) if match else None


def parse_margin(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)", clean_text(text))
    return float(match.group(1).replace(",", ".")) if match else None


def parse_point(text: str) -> dict:
    out: dict = {}
    for token in re.findall(r"[a-záéíóú]+|\d+", text.lower()):
        if token.isdigit():
            if len(token) == 4:
                out["y"] = int(token)
            elif "d" not in out and int(token) <= 31:
                out["d"] = int(token)
        elif token in MONTHS and "m" not in out:
            out["m"] = MONTHS[token]
    return out


def parse_dates(
    text: str, election_day: date, year_hint: int | None,
) -> tuple[date | None, date | None, str | None]:
    """Start, end and precision ('dia'/'mes') of a Spanish or English date or range."""
    text = clean_text(text).lower()
    text = re.sub(r"^(hasta|desde|through|until)\s+", "", text)
    if is_blank(text):
        return None, None, None
    numeric = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if numeric:
        day = date(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
        return day, day, "dia"

    pieces = [p for p in re.split(r"\s*(?:-|–|—|\ba\b|\bal\b)\s*", text) if p]
    points = [parse_point(p) for p in pieces]
    points = [p for p in points if p]
    if not points or not any("m" in p for p in points):
        return None, None, None
    start, end = dict(points[0]), dict(points[-1])
    # Spanish puts the month last ("5-7 de septiembre"), English often first
    # ("Jan 7-9"); either side lends the other what it lacks
    end.setdefault("m", start.get("m"))
    start.setdefault("m", end["m"])
    if "y" in start:
        end.setdefault("y", start["y"])
    precision = "dia" if "d" in end else "mes"

    if "y" not in end:
        end["y"] = year_hint or election_day.year
        if not year_hint and date(end["y"], end["m"], end.get("d", 1)) > election_day:
            end["y"] -= 1
    if "y" not in start:
        start["y"] = end["y"]
        if (start["m"], start.get("d", 1)) > (end["m"], end.get("d", 31)):
            start["y"] -= 1

    def first(p):
        return date(p["y"], p["m"], p.get("d", 1))

    def last(p):
        return date(p["y"], p["m"], p.get("d", calendar.monthrange(p["y"], p["m"])[1]))

    return first(start), last(end), precision


BALLOT_KINDS = {"candidato", "coalicion", "partido"}
RESIDUAL_KINDS = {"indecisos", "ninguno", "nulo_blanco", "otros_indecisos"}


def share_basis(declared: str | None, options: list) -> str:
    """How a poll's shares were computed, judged from what they add up to.

    Houses differ, and so do rows within one table: some print gross shares
    that reach 100 only with undecided included, others print effective shares
    (undecided removed) and then list the undecided rate on the full sample,
    which pushes the row total well past 100.

        efectiva      ballot options + "otros" make ~100 by themselves
        bruta         ~100 only once undecided / none / blank are added
        incompleta    under 97 even with everything — categories not published
        inconsistente over 103 and not explained by either reading
    """
    if declared:
        return declared
    totals = {"ballot": 0.0, "residual": 0.0}
    for _, _, (kind, *_), text in options:
        pct = parse_pct(text) or 0.0
        if kind in RESIDUAL_KINDS:
            totals["residual"] += pct
        else:
            totals["ballot"] += pct
    ballot, gross = totals["ballot"], totals["ballot"] + totals["residual"]
    if totals["residual"] > 0 and 97 <= gross <= 103:
        return "bruta"
    if 97 <= ballot <= 103:
        return "efectiva"
    if gross < 97:
        return "incompleta"
    return "inconsistente"


def year_from(text: str) -> int | None:
    match = re.search(r"\b(19\d\d|20\d\d)\b", text)
    return int(match.group(1)) if match else None


# ── main scrape ───────────────────────────────────────────────────────────────

def phase_for(election_id: str, spec_phase: str, reference: date | None) -> str:
    if spec_phase != "por_fecha":
        return spec_phase
    precampaign, campaign = PHASE_START[election_id]
    if reference is None:
        return ""
    if reference >= campaign:
        return "campaña"
    return "precampaña" if reference >= precampaign else "pre_electoral"


def classify_column(election_id: str, label: str, links: tuple[str, ...]):
    for pattern, role in META_COLUMNS:
        if re.search(pattern, label):
            return role, None
    probe = f"{label} || {' | '.join(links)}"
    for pattern, kind, candidato, partido, keys in OPTIONS[election_id]:
        if re.search(pattern, probe):
            return "option", (kind, candidato, partido, keys)
    for pattern, kind in RESIDUAL_OPTIONS:
        if re.search(pattern, label):
            return "option", (kind, "", "", "")
    return None, None


def scrape_table(lang, election_id, title, revid, payload, spec, problems):
    tables = page_tables(payload)
    table = tables[spec["table"]]
    section = heading_path(table)
    if spec["section"] not in section:
        raise ValueError(
            f"{lang} {election_id} table {spec['table']} sits under '{section}', "
            f"expected '{spec['section']}' — the page layout changed"
        )
    section_year = year_from(section.rsplit(">", 1)[-1])
    election_day = ELECTION_DATE[election_id]
    poll_urls = row_source_urls(table)
    grid = table_grid(table)
    columns, n_header = header_columns(grid)

    roles = []
    for c, (label, links) in enumerate(columns):
        role, option = classify_column(election_id, label, links)
        if role is None:
            problems.append(
                f"{lang} {election_id} t{spec['table']} col {c}: unmapped header "
                f"{label!r} {links}"
            )
        roles.append((role, option, label, year_from(label)))

    out = []
    for r, row in enumerate(grid[n_header:], start=n_header):
        if not row:
            continue
        texts = [text for _, text, _ in row]
        if len(set(texts)) == 1:
            continue  # event marker spanning the table ("Asesinato de Colosio")
        fields: dict = {}
        options = []
        for c, (role, option, label, label_year) in enumerate(roles):
            text = texts[c] if c < len(texts) else ""
            if role in (None, "skip"):
                continue
            if role == "option":
                options.append((c, label, option, text))
            else:
                fields[role] = text
                if label_year:
                    fields[f"{role}_year"] = label_year
        pollster_raw = fields.get("pollster", "")
        header_label = next(l for ro, _, l, _ in roles if ro == "pollster")
        if not pollster_raw or pollster_raw == header_label:
            continue  # repeated header row
        if NON_POLL_ROW.search(pollster_raw) or NON_POLL_NOTE.search(fields.get("notas", "")):
            continue
        if not any(parse_pct(text) is not None for *_, text in options):
            continue

        # Spanish ids keep their original form; English ones carry "_en"
        poll_id = f"{election_id}{'_en' if lang == 'en' else ''}_t{spec['table']:02d}_r{r:03d}"
        notas = fields.get("notas", "")
        dates = {}
        for role in ("levantamiento", "publicacion", "fecha"):
            if role in fields:
                hint = section_year or fields.get(f"{role}_year")
                text = fields[role]
                if poll_id in DATE_CORRECTIONS and DATE_CORRECTIONS[poll_id][0] == role:
                    text = DATE_CORRECTIONS[poll_id][1]
                    notas = "; ".join(filter(None, [
                        notas, f"fecha corregida: {fields[role]!r} -> {text!r}",
                    ]))
                dates[role] = parse_dates(text, election_day, hint)
                if dates[role][0] is None and not is_blank(text):
                    problems.append(
                        f"{poll_id}: unparsed date "
                        f"{fields[role]!r}"
                    )
        fieldwork = dates.get("levantamiento")
        published = dates.get("publicacion")
        bare = dates.get("fecha")
        if bare and spec["fecha"] == "levantamiento":
            fieldwork = fieldwork or bare
        elif bare and spec["fecha"] == "publicacion":
            published = published or bare

        if fieldwork and fieldwork[1]:
            reference, ref_type, precision = fieldwork[1], "fin_levantamiento", fieldwork[2]
        elif published and published[1]:
            reference, ref_type, precision = published[1], "publicacion", published[2]
        elif bare and bare[1]:
            reference, ref_type, precision = bare[1], "sin_especificar", bare[2]
        else:
            reference = ref_type = precision = None
            problems.append(f"{poll_id}: no usable date")

        base = {
            "source": f"{lang}.wikipedia",
            "source_url": source_url(lang, title, revid),
            "source_revid": revid,
            "source_section": section,
            "source_table": spec["table"],
            "source_row": r,
            "election_id": election_id,
            "election_date": election_day.isoformat(),
            "poll_id": poll_id,
            "poll_kind": spec["kind"],
            "intention_level": spec["level"],
            "phase": phase_for(election_id, spec["phase"], reference),
            "share_basis": share_basis(spec["basis"], options),
            "pollster_raw": pollster_raw,
            "pollster": "",
            "cliente": "",
            "fecha_levantamiento_raw": fields.get("levantamiento", ""),
            "fecha_publicacion_raw": fields.get("publicacion", ""),
            "fecha_raw": fields.get("fecha", ""),
            "fieldwork_start": fieldwork[0].isoformat() if fieldwork and fieldwork[0] else "",
            "fieldwork_end": fieldwork[1].isoformat() if fieldwork and fieldwork[1] else "",
            "published_date": published[1].isoformat() if published and published[1] else "",
            "reference_date": reference.isoformat() if reference else "",
            "reference_date_type": ref_type or "",
            "date_precision": precision or "",
            "sample_raw": fields.get("muestra", ""),
            "sample_size": parse_int(fields.get("muestra", "")) or "",
            "margin_raw": fields.get("margen", ""),
            "margin_of_error": parse_margin(fields.get("margen", "")) or "",
            "notas": notas,
            "poll_source_url": poll_urls[r] if r < len(poll_urls) else "",
        }
        if reference and reference > election_day:
            problems.append(
                f"{poll_id}: reference date {reference} is after election day"
            )
        for order, (c, label, (kind, candidato, partido, keys), text) in enumerate(options, 1):
            pct = parse_pct(text)
            if pct is None and not is_blank(text):
                problems.append(f"{poll_id} {label!r}: unparsed share {text!r}")
            out.append({
                **base,
                "option_order": order,
                "opcion": label or partido,
                "option_kind": kind,
                "candidato": candidato,
                "partido_coalicion": partido,
                "party_keys": keys,
                "pct_raw": text,
                "pct": "" if pct is None else pct,
            })
    return out


def table_specs(lang, election_id, payload, problems) -> list[dict]:
    """TABLES' specs for one page, each `match` resolved to the tables it covers.

    On a live page, a table with a pollster column that no spec covers is a
    problem: a new kind of table (polls by coalition, say) needs a decision on
    how to read it, not a silent skip.
    """
    tables = page_tables(payload)
    specs, covered = [], set()
    for spec in TABLES[lang][election_id]:
        if "match" not in spec:
            specs.append(spec)
            covered.add(spec["table"])
            continue
        found = [i for i, t in enumerate(tables) if re.search(spec["match"], heading_path(t))]
        if not found:
            problems.append(f"{lang} {election_id}: no table under a heading matching "
                            f"{spec['match']!r}")
        for i in found:
            specs.append({**spec, "table": i, "section": heading_path(tables[i])})
            covered.add(i)
    if election_id in LIVE:
        for i, table in enumerate(tables):
            columns, _ = header_columns(table_grid(table))
            if i not in covered and any(
                classify_column(election_id, label, links)[0] == "pollster"
                for label, links in columns
            ):
                problems.append(f"{lang} {election_id} table {i} under "
                                f"'{heading_path(table)}' lists polls but no TABLES spec covers it")
    return specs


def stable_poll_ids(rows: list[dict]) -> None:
    """Re-key live-page polls by house and date, in place.

    Live tables grow at the top, so a row-numbered id would move to a
    different poll on every refresh. Same house and date twice on one page
    (two question levels, say) takes a numeric suffix in page order.
    """
    new_ids: dict[str, str] = {}
    taken: set[str] = set()
    for row in rows:
        if row["election_id"] not in LIVE or row["poll_id"] in new_ids:
            continue
        house = unicodedata.normalize("NFKD", row["pollster"]).encode("ascii", "ignore").decode()
        house = re.sub(r"[^a-z0-9]+", "", house.lower())
        lang = "_en" if row["source"] == "en.wikipedia" else ""
        base = f"{row['election_id']}{lang}_{house}_{row['reference_date'].replace('-', '')}"
        poll_id, n = base, 2
        while poll_id in taken:
            poll_id, n = f"{base}_{n}", n + 1
        taken.add(poll_id)
        new_ids[row["poll_id"]] = poll_id
    for row in rows:
        row["poll_id"] = new_ids.get(row["poll_id"], row["poll_id"])


def list_tables(langs: list[str]) -> None:
    for lang in langs:
        for election_id, (title, revid) in PAGES[lang].items():
            list_page(lang, election_id, title, revid)


def list_page(lang, election_id, title, revid) -> None:
    payload = fetch(lang, title, revid)
    print(f"\n## {lang} {election_id}  {title}  (oldid {revid})")
    for i, table in enumerate(page_tables(payload)):
        grid = table_grid(table)
        columns, _ = header_columns(grid)
        labels = [label or "|".join(links)[:25] for label, links in columns]
        print(f"  {i:>2} [{heading_path(table)}] rows={len(grid)}  {labels[:9]}")


# ── matching the two editions ─────────────────────────────────────────────────

MATCH_COLUMNS = [
    "election_id", "status", "pollster", "intention_level", "en_poll_id", "es_poll_id",
    "en_reference_date", "es_reference_date", "max_diff_raw", "max_diff_efectiva",
    "en_shares", "es_shares",
]


def poll_index(rows: list[dict]) -> dict[str, dict]:
    """Per poll: its metadata row and {party_keys: pct} for ballot options."""
    polls: dict[str, dict] = {}
    for row in rows:
        poll = polls.setdefault(row["poll_id"], {"meta": row, "shares": {}, "ballot_total": 0.0})
        if row["pct"] == "" or row["option_kind"] in RESIDUAL_KINDS:
            continue
        poll["ballot_total"] += float(row["pct"])      # ballot options + "otros"
        if row["party_keys"]:
            poll["shares"][row["party_keys"]] = float(row["pct"])
    return polls


def dates_close(a: dict, b: dict, days: int) -> bool:
    if not a["reference_date"] or not b["reference_date"]:
        return False
    da, db = date.fromisoformat(a["reference_date"]), date.fromisoformat(b["reference_date"])
    if "mes" in (a["date_precision"], b["date_precision"]):
        return (da.year, da.month) == (db.year, db.month)
    return abs((da - db).days) <= days


def share_gap(en: dict, es: dict) -> tuple[float | None, float | None]:
    """Largest disagreement on shared options, as printed and after rescaling.

    One edition can print gross shares and the other effective ones for the
    same poll, so a pair also counts as agreeing once each side is rescaled
    to the total of the options both list.
    """
    shared = en["shares"].keys() & es["shares"].keys()
    if not shared:
        return None, None
    raw = max(abs(en["shares"][k] - es["shares"][k]) for k in shared)
    en_total = sum(en["shares"][k] for k in shared)
    es_total = sum(es["shares"][k] for k in shared)
    eff = None
    if en_total and es_total:
        eff = max(
            abs(100 * en["shares"][k] / en_total - 100 * es["shares"][k] / es_total)
            for k in shared
        )
    return raw, eff


def is_primary(meta: dict) -> bool:
    return meta["source"] == f"{PRIMARY.get(meta['election_id'], 'es')}.wikipedia"


def match_sources(rows: list[dict]) -> list[dict]:
    """Keep every poll from the primary edition, plus the other edition's
    polls the primary doesn't have.

    The primary edition is Spanish unless PRIMARY says otherwise. For each
    poll from the other edition, a primary poll is a candidate when it is the
    same election, question level and house (by familia, so "Buendía & Laredo"
    and "El Universal/Buendía & Laredo" meet) with fieldwork ending close by.
        duplicado   shares agree within MATCH_TOLERANCE -> secondary row dropped
        conflicto   same house and dates, shares disagree -> secondary row
                    dropped, primary kept; review in en_es_matches.csv
        solo_en     no candidate -> secondary row kept (solo_es when Spanish
                    is the secondary edition)
    """
    polls = poll_index(rows)
    by_key: dict[tuple, list[dict]] = {}
    for poll in polls.values():
        m = poll["meta"]
        if is_primary(m):
            by_key.setdefault((m["election_id"], m["intention_level"], m["familia"]), []).append(poll)

    drop: set[str] = set()
    # A primary poll that cites nothing borrows the source its match cites
    borrowed_url: dict[str, str] = {}
    report: list[dict] = []
    for poll_id, other in polls.items():
        m = other["meta"]
        if is_primary(m):
            continue
        best = None
        for primary in by_key.get((m["election_id"], m["intention_level"], m["familia"]), []):
            if not dates_close(m, primary["meta"], MATCH_DAYS_IF_EQUAL):
                continue
            raw, eff = share_gap(other, primary)
            if raw is None:
                continue
            gap = min(raw, eff if eff is not None else raw)
            near = dates_close(m, primary["meta"], MATCH_DAYS)
            if gap > MATCH_TOLERANCE and not near:
                continue                      # a different wave, not a disagreement
            if best is None or gap < best[0]:
                best = (gap, primary, raw, eff)
        lang = m["source"].split(".")[0]
        if best is None:
            status, primary, raw, eff = f"solo_{lang}", None, None, None
        else:
            gap, primary, raw, eff = best
            status = "duplicado" if gap <= MATCH_TOLERANCE else "conflicto"
            drop.add(poll_id)
            if not primary["meta"]["poll_source_url"] and m["poll_source_url"]:
                borrowed_url.setdefault(primary["meta"]["poll_id"], m["poll_source_url"])
        en, es = (other, primary) if lang == "en" else (primary, other)
        fmt_shares = lambda p: "; ".join(f"{k}={v:g}" for k, v in sorted(p["shares"].items()))
        report.append({
            "election_id": m["election_id"], "status": status, "pollster": m["pollster"],
            "intention_level": m["intention_level"],
            "en_poll_id": en["meta"]["poll_id"] if en else "",
            "es_poll_id": es["meta"]["poll_id"] if es else "",
            "en_reference_date": en["meta"]["reference_date"] if en else "",
            "es_reference_date": es["meta"]["reference_date"] if es else "",
            "max_diff_raw": "" if raw is None else round(raw, 2),
            "max_diff_efectiva": "" if eff is None else round(eff, 2),
            "en_shares": fmt_shares(en) if en else "",
            "es_shares": fmt_shares(es) if es else "",
        })

    with MATCHES_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(report, key=lambda r: (r["election_id"], r["status"], r["en_reference_date"])))

    counts: dict[tuple, int] = {}
    for r in report:
        counts[(r["election_id"], r["status"])] = counts.get((r["election_id"], r["status"]), 0) + 1
    print("Secondary-edition polls vs. primary (English vs. Spanish unless noted):")
    for election_id in PAGES_ES:
        solo = "solo_es" if PRIMARY.get(election_id) == "en" else "solo_en"
        line = ", ".join(f"{st}={counts.get((election_id, st), 0)}"
                         for st in ("duplicado", "conflicto", solo))
        note = "  (primary: English)" if PRIMARY.get(election_id) == "en" else ""
        print(f"  {election_id}: {line}{note}")
    for row in rows:
        row["poll_source_url"] = row["poll_source_url"] or borrowed_url.get(row["poll_id"], "")
    return [row for row in rows if row["poll_id"] not in drop]


def latest_revid(lang: str, title: str) -> int:
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(dict(
        action="query", prop="revisions", titles=title, rvprop="ids",
        format="json", formatversion=2,
    ))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    page = json.load(urllib.request.urlopen(request, timeout=60))["query"]["pages"][0]
    if "missing" in page:
        raise RuntimeError(f"{lang} page {title!r} not found — renamed? update live_pages.json")
    return page["revisions"][0]["revid"]


def refresh_live_pages() -> dict:
    """Point every live page at its latest revision; returns the new pins."""
    pins = json.loads(json.dumps(LIVE_PAGES))
    print("Live pages:")
    for election_id, by_lang in pins.items():
        for lang, page in by_lang.items():
            revid = latest_revid(lang, page["title"])
            moved = "unchanged" if revid == page["revid"] else f"{page['revid']} -> {revid}"
            print(f"  {election_id} {lang}: {moved}")
            page["revid"] = revid
            PAGES[lang][election_id] = (page["title"], revid)
    return pins


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-tables", nargs="*", choices=["es", "en"], default=None,
                        help="Print every table on the pinned pages and exit")
    parser.add_argument("--refresh", action="store_true",
                        help="Move live pages (live_pages.json) to their latest revision; "
                             "the new pins are saved only if the scrape comes out clean")
    args = parser.parse_args()
    pins = refresh_live_pages() if args.refresh else None
    if args.list_tables is not None:
        list_tables(args.list_tables or ["es", "en"])
        return

    rows: list[dict] = []
    problems: list[str] = []
    for lang in ("es", "en"):
        for election_id, (title, revid) in PAGES[lang].items():
            payload = fetch(lang, title, revid)
            if payload["parse"]["revid"] != revid:
                raise ValueError(f"{title}: asked for {revid}, got {payload['parse']['revid']}")
            for spec in table_specs(lang, election_id, payload, problems):
                rows.extend(scrape_table(lang, election_id, title, revid, payload, spec, problems))

    unknown = set()
    for row in rows:
        pollster, cliente, metodo = canonical_pollster(row["pollster_raw"])
        if pollster is None:
            unknown.add(row["pollster_raw"])
        row["pollster"], row["cliente"] = pollster or "", cliente or ""
        row["metodo"] = metodo or ""
        row["familia"] = familia(pollster) if pollster else ""
    for name in sorted(unknown):
        problems.append(f"pollster not in pollsters.py: {name!r}")

    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        print(f"{len(problems)} problem(s); CSV not written"
              f"{', live_pages.json left as it was' if pins else ''}.")
        sys.exit(1)

    stable_poll_ids(rows)
    rows = match_sources(rows)

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    polls = {row["poll_id"] for row in rows}
    print(f"Wrote {len(rows):,} option rows from {len(polls):,} polls to {OUT_CSV}")
    by_election: dict[str, set] = {}
    for row in rows:
        by_election.setdefault(row["election_id"], set()).add(row["poll_id"])
    for election_id, ids in by_election.items():
        print(f"  {election_id}: {len(ids)} polls")
    if pins:
        LIVE_PAGES_JSON.write_text(json.dumps(pins, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"Pinned the new revisions in {LIVE_PAGES_JSON.name}")


if __name__ == "__main__":
    main()
