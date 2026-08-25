"""
Shared constants for the ingestion layer.
Imported by both electoral/ingest.py and its materialize.py so
state names
and normalization stay in sync across the full pipeline.
"""

import unicodedata

# Canonical state names keyed by id_estado (1-32).
# Source files across cycles spell/accent state names inconsistently
# (e.g. "COAHUILA" vs "COAHUILA DE ZARAGOZA", "MEXICO" vs "MÉXICO").
# Both ingest and materialize key off this single mapping so state identity
# never fragments across the pipeline.
CANONICAL_ESTADO_NOMBRES: dict[int, str] = {
     1: "AGUASCALIENTES",                  2: "BAJA CALIFORNIA",
     3: "BAJA CALIFORNIA SUR",             4: "CAMPECHE",
     5: "COAHUILA DE ZARAGOZA",            6: "COLIMA",
     7: "CHIAPAS",                         8: "CHIHUAHUA",
     9: "CIUDAD DE MÉXICO",               10: "DURANGO",
    11: "GUANAJUATO",                     12: "GUERRERO",
    13: "HIDALGO",                        14: "JALISCO",
    15: "MÉXICO",                         16: "MICHOACÁN DE OCAMPO",
    17: "MORELOS",                        18: "NAYARIT",
    19: "NUEVO LEÓN",                     20: "OAXACA",
    21: "PUEBLA",                         22: "QUERÉTARO",
    23: "QUINTANA ROO",                   24: "SAN LUIS POTOSÍ",
    25: "SINALOA",                        26: "SONORA",
    27: "TABASCO",                        28: "TAMAULIPAS",
    29: "TLAXCALA",                       30: "VERACRUZ DE IGNACIO DE LA LLAVE",
    31: "YUCATÁN",                        32: "ZACATECAS",
}

DB_PATH = "election_data.db"


def canonical_estado(id_estado: int, fallback: str) -> str:
    return CANONICAL_ESTADO_NOMBRES.get(int(id_estado), fallback)
