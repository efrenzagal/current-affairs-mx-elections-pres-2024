import sqlite3

# (id_estado, nombre_estado) matches dim_geography exactly.
# gub_term_years: legal term length for governor (years)
# last_gub_election / next_gub_election: years
# next_local_congress_election, next_municipal_election: years (comma-separated string if multiple known upcoming cycles)
# has_governor: 0 for CDMX (Jefe/a de Gobierno, not "gobernador", but functionally equivalent executive - kept as 1 with note)
# notes

rows = [
    (1,  "AGUASCALIENTES", 6, 2022, 2027, "2027", "2027", "Legal term is 6 years, positioned to land on the 2027 intermediate cycle"),
    (2,  "BAJA CALIFORNIA", 5, 2021, 2027, "2027", "2027", "Term extended from originally shorter length to synchronize with the 2027 cycle (post Bonilla-era dispute)"),
    (3,  "BAJA CALIFORNIA SUR", 6, 2021, 2027, "2027", "2027", None),
    (4,  "CAMPECHE", 6, 2021, 2027, "2027", "2027", None),
    (5,  "COAHUILA DE ZARAGOZA", 6, 2023, 2029, "2026,2029", "2026,2029", "Local congress/municipal already renewed June 2026; next full slate 2029"),
    (6,  "COLIMA", 6, 2021, 2027, "2027", "2027", None),
    (7,  "CHIAPAS", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election"),
    (8,  "CHIHUAHUA", 6, 2021, 2027, "2027", "2027", None),
    (9,  "CIUDAD DE MÉXICO", 6, 2024, 2030, "2027,2030", "2027,2030", "No 'gobernador'; executive is Jefe/a de Gobierno. No municipios; 16 alcaldías elected on the same cycle as municipal elections elsewhere"),
    (10, "DURANGO", 6, 2022, 2028, "2025,2028", "2025,2028", "Local congress/municipal elected June 2025; next full slate 2028"),
    (11, "GUANAJUATO", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election"),
    (12, "GUERRERO", 6, 2021, 2027, "2027", "2027", None),
    (13, "HIDALGO", 6, 2022, 2028, "2025,2028", "2025,2028", "Local congress/municipal elected June 2025; next full slate 2028"),
    (14, "JALISCO", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election"),
    (15, "MÉXICO", 6, 2023, 2029, "2026,2029", "2026,2029", "Local congress/municipal already renewed June 2026; next full slate 2029"),
    (16, "MICHOACÁN DE OCAMPO", 6, 2021, 2027, "2027", "2027", None),
    (17, "MORELOS", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election"),
    (18, "NAYARIT", 6, 2021, 2027, "2027", "2027", None),
    (19, "NUEVO LEÓN", 6, 2021, 2027, "2027", "2027", None),
    (20, "OAXACA", 6, 2022, 2028, "2025,2028", "2025,2028", "Many municipios are governed by usos y costumbres rather than party elections"),
    (21, "PUEBLA", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election"),
    (22, "QUERÉTARO", 6, 2021, 2027, "2027", "2027", None),
    (23, "QUINTANA ROO", 5, 2022, 2027, "2027", "2027", "Term shortened from 6 to 5 years (2022-2027) specifically to synchronize with the 2027 federal intermediate cycle"),
    (24, "SAN LUIS POTOSÍ", 6, 2021, 2027, "2027", "2027", None),
    (25, "SINALOA", 6, 2021, 2027, "2027", "2027", None),
    (26, "SONORA", 6, 2021, 2027, "2027", "2027", None),
    (27, "TABASCO", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election"),
    (28, "TAMAULIPAS", 6, 2022, 2028, "2025,2028", "2025,2028", "Local congress/municipal elected June 2025; next full slate 2028"),
    (29, "TLAXCALA", 6, 2021, 2027, "2027", "2027", None),
    (30, "VERACRUZ DE IGNACIO DE LA LLAVE", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election; some 2025-26 municipal contests had extraordinary re-runs"),
    (31, "YUCATÁN", 6, 2024, 2030, "2027,2030", "2027,2030", "Governor elected concurrently with 2024 federal presidential election"),
    (32, "ZACATECAS", 6, 2021, 2027, "2027", "2027", None),
]

GUB_2027 = {1, 2, 3, 4, 8, 6, 12, 16, 18, 19, 22, 23, 24, 25, 26, 29, 32}

con = sqlite3.connect("election_data.db")
cur = con.cursor()

cur.execute("DROP TABLE IF EXISTS dim_state_election_calendar")
cur.execute("""
CREATE TABLE dim_state_election_calendar (
    id_estado INTEGER PRIMARY KEY,
    nombre_estado TEXT NOT NULL,
    has_gubernatorial_office INTEGER NOT NULL DEFAULT 1,
    gub_term_years INTEGER NOT NULL,
    last_gub_election_year INTEGER NOT NULL,
    next_gub_election_year INTEGER NOT NULL,
    local_congress_term_years INTEGER NOT NULL DEFAULT 3,
    next_local_congress_election_years TEXT NOT NULL,
    municipal_term_years INTEGER NOT NULL DEFAULT 3,
    next_municipal_election_years TEXT NOT NULL,
    has_gub_election_2027 INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'web research (INE, OPLE sites, Wikipedia elecciones locales) as of 2026-07',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

for r in rows:
    id_estado = r[0]
    has_gub = 0 if id_estado == 9 else 1  # CDMX uses Jefe/a de Gobierno, not "gobernador"
    has_2027 = 1 if id_estado in GUB_2027 else 0
    cur.execute("""
        INSERT INTO dim_state_election_calendar
        (id_estado, nombre_estado, has_gubernatorial_office, gub_term_years,
         last_gub_election_year, next_gub_election_year, local_congress_term_years,
         next_local_congress_election_years, municipal_term_years,
         next_municipal_election_years, has_gub_election_2027, notes)
        VALUES (?, ?, ?, ?, ?, ?, 3, ?, 3, ?, ?, ?)
    """, (id_estado, r[1], has_gub, r[2], r[3], r[4], r[5], r[6], has_2027, r[7]))

con.commit()

cur.execute("SELECT count(*) FROM dim_state_election_calendar")
print("rows:", cur.fetchone())

cur.execute("""
SELECT nombre_estado FROM dim_state_election_calendar
WHERE has_gub_election_2027 = 1
   OR next_local_congress_election_years LIKE '%2027%'
   OR next_municipal_election_years LIKE '%2027%'
ORDER BY nombre_estado
""")
print("\nStates with ANY election in 2027:")
for r in cur.fetchall():
    print(" -", r[0])

con.close()
