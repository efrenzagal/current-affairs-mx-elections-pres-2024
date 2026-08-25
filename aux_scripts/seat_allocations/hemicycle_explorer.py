"""
Hemicycle Explorer — diputados MR + RP seat visualization (500 seats)
======================================================================
Standalone script that:
  1. Queries MR district winners from the warehouse (300 seats).
  2. Can approximate RP seat allocation (200 seats, 40 per circunscripcion)
     using natural quotient/largest remainder + Mexico's overrepresentation cap
     (COFIPE/LEGIPE: no party may hold more than 300 total seats or
     exceed its national vote share by more than 8 percentage points).
  3. Renders an interactive hemicycle with MR seats (squares) and
     RP seats (circles) distinguished visually.

Controls (all in-browser):
  - Year tabs           : one tab per DIP_MR_* election
  - Vista               : Coaliciones | Partidos
  - Orden               : Por partido | Por estado
  - Estado filter       : Nacional | any state (MR seats only when filtered)

Usage:
    python3 aux_scripts/hemicycle_explorer.py

The warehouse (election_data.db) must already be built:
    python3 electoral/ingest.py
"""

from __future__ import annotations
import os
import sqlite3
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from aux_scripts.seat_allocations.common import (
    COALITION_TO_PARTY as _C2P,
    largest_remainder,
    connect as _sa_connect,
)
from camara_de_diputados.escanos import diputados as dip_mod
from camara_de_senadores.escanos import senadores as sen_mod
from camara_de_diputados.escanos.ingest import diputado_id_for_row
from camara_de_senadores.escanos.ingest import senador_id_for_row
from lib.person_names import display_person_name

DB_PATH  = "election_data.db"
OUT_PATH = "aux_scripts/seat_allocations/hemicycle_explorer.html"

# ── Diputados constants (authoritative values live in diputados.py) ────────────
RP_SEATS_PER_CIRC   = dip_mod.RP_SEATS_PER_CIRC
N_CIRCUNSCRIPCIONES = dip_mod.N_CIRCUNSCRIPCIONES
TOTAL_SEATS         = dip_mod.TOTAL_SEATS
MR_SEATS            = dip_mod.TOTAL_SEATS - dip_mod.RP_SEATS_PER_CIRC * dip_mod.N_CIRCUNSCRIPCIONES
RP_SEATS_TOTAL      = dip_mod.RP_SEATS_PER_CIRC * dip_mod.N_CIRCUNSCRIPCIONES
THRESHOLD_PCT       = dip_mod.THRESHOLD_PCT
MAX_SEATS_ABSOLUTE  = dip_mod.MAX_SEATS_ABSOLUTE
SOBREREPR_CAP_PTS   = dip_mod.SOBREREPR_CAP_PTS

# ── Senate constants (authoritative values live in senadores.py) ───────────────
SEN_TOTAL_SEATS = 128   # 64 MR winner + 32 first-minority + 32 RP
SEN_MR_SEATS    = 64    # 2 per state × 32 states
SEN_FM_SEATS    = 32    # 1 first-minority per state
SEN_RP_SEATS    = sen_mod.SEN_RP_SEATS

# ── Colors ─────────────────────────────────────────────────────────────────────

PARTY_COLORS: dict[str, str] = {
    "MORENA":           "#8B0000",
    "PAN":              "#003893",
    "PRI":              "#006847",
    "PRD":              "#FFD700",
    "PT":               "#CC0000",
    "PVEM":             "#4CAF50",
    "MC":               "#FF8C00",
    "PANAL":            "#00BCD4",
    "PES":              "#E91E8C",
    "RSP":              "#9C27B0",
    "FXM":              "#795548",
    "CI":               "#607D8B",
    # Coalition bloc names (senate and diputados)
    "Sigamos Haciendo Historia":   "#8B0000",
    "Juntos Hacemos Historia":     "#8B0000",
    "Juntos Haremos Historia":     "#8B0000",
    "Fuerza y Corazon por Mexico": "#003893",
    "Fuerza y Corazón por México": "#003893",
    "Va por Mexico":               "#003893",
    "Va por México":               "#003893",
    "Por Mexico al Frente":        "#003893",
    "Por México al Frente":        "#003893",
    "Todos por Mexico":            "#006847",
    "Todos por México":            "#006847",
    "Compromiso por Mexico":       "#006847",
    "Compromiso por México":       "#006847",
    "Movimiento Progresista":      "#FFD700",
    "Movimiento Ciudadano":        "#FF8C00",
    "Alianza por el Cambio":       "#003893",
    "Alianza por Mexico":          "#FFD700",
    "Alianza por México":          "#FFD700",
    "Por el Bien de Todos":        "#FFD700",
    "APM":                         "#006847",
    "Nueva Alianza":               "#00BCD4",
    "PVEM_PT_MORENA":   "#8B0000",
    "PT_MORENA":        "#A02020",
    "PVEM_MORENA":      "#7A3030",
    "PVEM_PT":          "#6B8C50",
    "PT_MORENA_PES":    "#8B0000",
    "MORENA_PES":       "#922020",
    "PT_PES":           "#B84040",
    "PAN_PRI_PRD":      "#003893",
    "PAN_PRI":          "#2060A0",
    "PAN_PRD":          "#1878B8",
    "PRI_PRD":          "#2E7A60",
    "PAN_PRD_MC":       "#003893",
    "PAN_MC":           "#4488CC",
    "PRD_MC":           "#C89000",
    "PRI_PVEM_NA":      "#006847",
    "PRI_PVEM":         "#1E7048",
    "PRI_NA":           "#226050",
    "PVEM_NA":          "#5EA050",
    "C_PRI_PVEM":       "#006847",
    "C_PRD_PT":         "#FFD700",
    "C_PRD_PT_MC":      "#FFD700",
    "C_PRD_MC":         "#C89000",
    "C_PT_MC":          "#B87000",
    "A. CAM.":          "#003893",
    "A. MEX.":          "#FFD700",
    "APM":              "#006847",
    "PBT":              "#FFD700",
    # Historical parties
    "CONV":             "#FF8C00",   # Convergencia → became MC
    "PSN":              "#9C27B0",
    "PAS":              "#795548",
    "PASC":             "#607D8B",
    "IND":              "#888888",
    "CAND_INDEPENDIENTE": "#888888",
    "SG":                "#777777",
    "VACANTE":           "#454545",
    "LICENCIA":          "#8A6D3B",
}

COALITION_TO_PARTY: dict[str, str] = _C2P


def _fallback_color(key: str) -> str:
    h = hash(key) % 360
    return f"hsl({h},55%,42%)"


def party_color(key: str) -> str:
    return PARTY_COLORS.get(key, _fallback_color(key))


# Official INTEGRACION_CARGOS files are the dashboard's ground truth.
INTEGRACION_PATHS: dict[str, str] = {
    "DIP_MR_2024": "data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/INTEGRACION_CARGOS_PEF_2024.csv",
    "SEN_MR_2024": "data/electoral_data_raw/raw_2024/PRESIDENCIA_2024/CSV/INTEGRACION_CARGOS_PEF_2024.csv",
}

COMPOSICION_DIP = "data/composicion/diputados.csv"
COMPOSICION_SEN = "data/composicion/senadores.csv"


def _year(election_id: str) -> int:
    return int(election_id.rsplit("_", 1)[-1])


def _legacy_load_composicion_dip(election_id: str, conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Deprecated reference loader retained only to reproduce earlier research.

    This is deliberately private and is never used by Streamlit because its
    quota-fill party reassignment does not identify the owner of a particular
    constitutional seat.

    Build 500 seat rows from the local composicion CSV.
    MR seats reuse warehouse district winners for geography/tooltips.
    RP seats are generated from reference counts.
    """
    year = _year(election_id)
    ref = pd.read_csv(COMPOSICION_DIP)
    ref = ref[ref["year"] == year].copy()
    if ref.empty:
        return pd.DataFrame()

    # ── MR: use warehouse winners for geography, map party from reference ──────
    raw = dip_mod.district_votes(conn, election_id)
    if raw.empty:
        # No warehouse data — build MR rows without geography from reference
        mr_rows = []
        for _, r in ref.iterrows():
            for i in range(int(r["mr"])):
                mr_rows.append({
                    "party_key": r["party"], "canonical_party": r["party"],
                    "id_estado": 0, "nombre_estado": "MR",
                    "id_distrito_federal": i + 1,
                    "cabecera_distrital_federal": f"Distrito {i+1}",
                    "votes": 0, "votes_total": 0, "pct_winner": 0.0,
                    "seat_type": "MR",
                })
        df_mr = pd.DataFrame(mr_rows) if mr_rows else pd.DataFrame()
        rp_rows = []
        for _, r in ref.iterrows():
            for i in range(int(r["rp"])):
                rp_rows.append({
                    "party_key": r["party"], "canonical_party": r["party"],
                    "id_estado": 0, "nombre_estado": "RP",
                    "id_distrito_federal": i + 1,
                    "cabecera_distrital_federal": f"Lista RP {i+1}",
                    "votes": 0, "votes_total": 0, "pct_winner": 0.0,
                    "seat_type": "RP",
                })
        df_rp = pd.DataFrame(rp_rows) if rp_rows else pd.DataFrame()
        return pd.concat([df_mr, df_rp], ignore_index=True)
    idx = raw.groupby(["id_estado", "id_distrito_federal"])["votes"].idxmax()
    mr_geo = raw.loc[idx].copy()
    totals = raw.groupby(["id_estado", "id_distrito_federal"])["votes"].sum().rename("votes_total").reset_index()
    mr_geo = mr_geo.merge(totals, on=["id_estado", "id_distrito_federal"])
    mr_geo["pct_winner"] = (mr_geo["votes"] / mr_geo["votes_total"] * 100).round(1)
    mr_geo["canonical_party"] = mr_geo["party_key"].map(lambda k: COALITION_TO_PARTY.get(k, k))
    mr_geo["seat_type"] = "MR"

    # Re-label canonical_party to match reference totals.
    # Sort by votes desc so dominant party wins get assigned first.
    ref_mr = ref.set_index("party")["mr"].to_dict()
    mr_geo = mr_geo.sort_values("votes", ascending=False).reset_index(drop=True)
    assigned: dict[str, int] = {p: 0 for p in ref_mr}
    new_parties = []
    for _, row in mr_geo.iterrows():
        cp = row["canonical_party"]
        # Assign to this party if quota not yet exhausted, else try others in ref
        if cp in assigned and assigned[cp] < ref_mr.get(cp, 0):
            new_parties.append(cp)
            assigned[cp] = assigned.get(cp, 0) + 1
        else:
            # Find a party that still has quota
            fallback = next(
                (p for p in ref_mr if assigned.get(p, 0) < ref_mr[p]), cp
            )
            new_parties.append(fallback)
            assigned[fallback] = assigned.get(fallback, 0) + 1
    mr_geo["canonical_party"] = new_parties
    mr_geo["party_key"] = mr_geo["canonical_party"]

    # ── RP: expand reference counts into individual seat rows ─────────────────
    rp_rows = []
    for _, r in ref.iterrows():
        for i in range(int(r["rp"])):
            rp_rows.append({
                "party_key": r["party"], "canonical_party": r["party"],
                "id_estado": 0, "nombre_estado": "RP",
                "id_distrito_federal": i + 1,
                "cabecera_distrital_federal": f"Lista RP {i+1}",
                "votes": 0, "votes_total": 0, "pct_winner": 0.0,
                "seat_type": "RP",
            })
    df_rp = pd.DataFrame(rp_rows) if rp_rows else pd.DataFrame()
    return pd.concat([mr_geo.reset_index(drop=True), df_rp], ignore_index=True)


def _legacy_load_composicion_sen(election_id: str, conn: sqlite3.Connection) -> pd.DataFrame:
    """Deprecated reference loader retained only to reproduce earlier research.

    MR/FM seats are derived from actual state-level vote order (warehouse),
    ensuring each state's FM seat always goes to its second-place party.
    RP seat counts come from the local composicion CSV.
    """
    year = _year(election_id)
    ref = pd.read_csv(COMPOSICION_SEN)
    ref = ref[ref["year"] == year].copy()
    if ref.empty:
        return pd.DataFrame()   # fall back to computed

    # ── Build per-state vote data ──────────────��──────────────────────────────
    state_votes = sen_mod.state_votes(conn, election_id)
    sv = state_votes.copy()
    sv["canonical_party"] = sv["party_key"].map(lambda k: COALITION_TO_PARTY.get(k, k))
    sv_canon = sv.groupby(["id_estado", "nombre_estado", "canonical_party"])["votes"].sum().reset_index()
    sv_totals = sv.groupby("id_estado")["votes"].sum().rename("votes_total").reset_index()
    sv_canon = sv_canon.merge(sv_totals, on="id_estado")

    # ── Determine MR and FM winner per state from vote order ──────────────────
    rows = []
    for state_id, grp in sv_canon.groupby("id_estado"):
        sorted_grp = grp.sort_values("votes", ascending=False).reset_index(drop=True)
        nombre_e = sorted_grp.iloc[0]["nombre_estado"]
        vt_total = int(sorted_grp.iloc[0]["votes_total"])

        # MR winner: 2 seats to 1st-place party
        if len(sorted_grp) >= 1:
            mr_party = sorted_grp.iloc[0]["canonical_party"]
            mr_votes = int(sorted_grp.iloc[0]["votes"])
            mr_pct = round(mr_votes / vt_total * 100, 1) if vt_total > 0 else 0.0
            for _ in range(2):
                rows.append({
                    "party_key": mr_party, "canonical_party": mr_party,
                    "id_estado": int(state_id), "nombre_estado": nombre_e,
                    "id_distrito_federal": 1, "cabecera_distrital_federal": nombre_e,
                    "votes": mr_votes, "votes_total": vt_total, "pct_winner": mr_pct,
                    "seat_type": "MR",
                })

        # FM winner: 1 seat to 2nd-place party (must be different from MR winner)
        if len(sorted_grp) >= 2:
            fm_party = sorted_grp.iloc[1]["canonical_party"]
            fm_votes = int(sorted_grp.iloc[1]["votes"])
            fm_pct = round(fm_votes / vt_total * 100, 1) if vt_total > 0 else 0.0
            rows.append({
                "party_key": fm_party, "canonical_party": fm_party,
                "id_estado": int(state_id), "nombre_estado": nombre_e,
                "id_distrito_federal": 2, "cabecera_distrital_federal": nombre_e,
                "votes": fm_votes, "votes_total": vt_total, "pct_winner": fm_pct,
                "seat_type": "FM",
            })

    # ── Re-attribute MR/FM parties to match Wikipedia caucus totals ───────────
    # The warehouse gives coalition-level winners; Wikipedia gives individual party totals.
    # Remap canonical_party on MR rows using the same quota-fill approach as diputados.
    ref_mr = ref.set_index("party")["mr"].to_dict()
    ref_fm = ref.set_index("party")["fm"].to_dict()
    df_mrfm = pd.DataFrame(rows)

    for stype, ref_col in [("MR", ref_mr), ("FM", ref_fm)]:
        mask = df_mrfm["seat_type"] == stype
        sub = df_mrfm[mask].copy()
        assigned: dict[str, int] = {p: 0 for p in ref_col}
        new_parties = []
        for _, row in sub.iterrows():
            cp = row["canonical_party"]
            if cp in assigned and assigned[cp] < ref_col.get(cp, 0):
                new_parties.append(cp)
                assigned[cp] += 1
            else:
                fallback = next(
                    (p for p in ref_col if assigned.get(p, 0) < ref_col[p]), cp
                )
                new_parties.append(fallback)
                assigned[fallback] = assigned.get(fallback, 0) + 1
        df_mrfm.loc[mask, "canonical_party"] = new_parties
        df_mrfm.loc[mask, "party_key"] = new_parties

    # ── RP: expand from CSV reference counts ────────────���─────────────────────
    rp_rows = []
    for _, r in ref[ref["rp"] > 0].iterrows():
        for i in range(int(r["rp"])):
            rp_rows.append({
                "party_key": r["party"], "canonical_party": r["party"],
                "id_estado": 0, "nombre_estado": "RP",
                "id_distrito_federal": i + 1, "cabecera_distrital_federal": "Lista RP",
                "votes": 0, "votes_total": 0, "pct_winner": 0.0,
                "seat_type": "RP",
            })
    df_rp = pd.DataFrame(rp_rows) if rp_rows else pd.DataFrame()
    return pd.concat([df_mrfm, df_rp], ignore_index=True)

# ── Coalition blocs per election ──────────────────────────────────────────────
# Each entry maps a bloc display name to the list of canonical party keys it
# contains. Parties not listed fall into "Otros".
ELECTION_BLOCS: dict[str, list[tuple[str, list[str]]]] = {
    "DIP_MR_2024": [
        ("Sigamos Haciendo Historia", ["MORENA", "PVEM", "PT"]),
        ("Fuerza y Corazón por México", ["PAN", "PRI", "PRD"]),
        ("Movimiento Ciudadano", ["MC"]),
    ],
    "DIP_MR_2021": [
        ("Juntos Hacemos Historia", ["MORENA", "PVEM", "PT"]),
        ("Va por México", ["PAN", "PRI", "PRD"]),
        ("Movimiento Ciudadano", ["MC"]),
    ],
    "DIP_MR_2018": [
        ("Juntos Haremos Historia", ["MORENA", "PT", "PES"]),
        ("Por México al Frente", ["PAN", "PRD", "MC"]),
        ("Todos por México", ["PRI", "PVEM", "PANAL"]),
        ("Movimiento Ciudadano", ["MC"]),
    ],
    "DIP_MR_2015": [
        ("PRI + aliados", ["PRI", "PVEM", "PANAL"]),
        ("PAN", ["PAN"]),
        ("PRD + aliados", ["PRD", "PT", "MC"]),
        ("MORENA", ["MORENA"]),
        ("PES", ["PES"]),
    ],
    "DIP_MR_2012": [
        ("Compromiso por México (PRI)", ["PRI", "PVEM"]),
        ("PAN", ["PAN"]),
        ("Movimiento Progresista (PRD)", ["PRD", "PT", "MC"]),
        ("PANAL", ["PANAL"]),
    ],
    "DIP_MR_2006": [
        ("APM (PRI)", ["PRI", "APM"]),
        ("PAN", ["PAN"]),
        ("Por el Bien de Todos (PRD)", ["PRD", "PBT"]),
        ("Nueva Alianza", ["NVA_A"]),
    ],
    "DIP_MR_2003": [
        ("PRI", ["PRI"]),
        ("PAN", ["PAN"]),
        ("PRD", ["PRD"]),
        ("PVEM", ["PVEM"]),
    ],
    "DIP_MR_2000": [
        ("Alianza por el Cambio (PAN)", ["PAN", "A. CAM."]),
        ("Alianza por México (PRD)", ["PRD", "A. MEX."]),
        ("PRI", ["PRI"]),
    ],
    # Senate blocs — actor names returned by sen_mod.mr_actor_seats
    "SEN_MR_2024": [
        ("Sigamos Haciendo Historia", ["MORENA", "PVEM", "PT"]),
        ("Fuerza y Corazón por México", ["PAN", "PRI", "PRD"]),
        ("Movimiento Ciudadano", ["MC"]),
    ],
    "SEN_MR_2021": [
        ("Juntos Hacemos Historia", ["Juntos Hacemos Historia"]),
        ("Va por Mexico", ["Va por Mexico", "Va por México"]),
        ("Movimiento Ciudadano", ["MC", "Movimiento Ciudadano"]),
    ],
    "SEN_MR_2018": [
        ("Juntos Haremos Historia", ["Juntos Haremos Historia"]),
        ("Por Mexico al Frente", ["Por Mexico al Frente", "Por México al Frente"]),
        ("Todos por Mexico", ["Todos por Mexico", "Todos por México"]),
    ],
    "SEN_MR_2012": [
        ("Compromiso por Mexico", ["Compromiso por Mexico", "Compromiso por México"]),
        ("Movimiento Progresista", ["Movimiento Progresista"]),
        ("PAN", ["PAN"]),
        ("PANAL", ["PANAL"]),
    ],
    "SEN_MR_2006": [
        ("APM (PRI)", ["APM", "PRI"]),
        ("PAN", ["PAN"]),
        ("Por el Bien de Todos", ["Por el Bien de Todos"]),
        ("Nueva Alianza", ["Nueva Alianza", "NVA_A", "PANAL"]),
    ],
    "SEN_MR_2000": [
        ("Alianza por el Cambio (PAN)", ["Alianza por el Cambio", "PAN"]),
        ("Alianza por México (PRD)", ["Alianza por Mexico", "Alianza por México", "PRD"]),
        ("PRI", ["PRI"]),
    ],
}


def build_summary_html(winners: pd.DataFrame, election_id: str) -> str:
    """Return an HTML table showing MR / RP / Total seats per party, grouped by bloc."""
    total_seats = len(winners)

    # Aggregate by canonical_party
    agg = (
        winners.groupby("canonical_party")["seat_type"]
        .value_counts()
        .unstack(fill_value=0)
        .reset_index()
    )
    for col in ("MR", "FM", "RP"):
        if col not in agg.columns:
            agg[col] = 0
    agg["Total"] = agg["MR"] + agg["FM"] + agg["RP"]
    agg = agg.rename(columns={"canonical_party": "party"})
    party_seats: dict[str, dict] = {
        r["party"]: {"MR": int(r["MR"]), "FM": int(r["FM"]), "RP": int(r["RP"]), "Total": int(r["Total"])}
        for _, r in agg.iterrows()
    }

    blocs = ELECTION_BLOCS.get(election_id, [])
    # Parties already assigned to a bloc
    assigned: set[str] = {p for _, parties in blocs for p in parties}
    # Remaining parties → Otros
    otros = [p for p in party_seats if p not in assigned and party_seats[p]["Total"] > 0]
    if otros:
        blocs = list(blocs) + [("Otros", sorted(otros))]

    # Bloc header background — derive from first party color
    def bloc_bg(parties: list[str]) -> str:
        for p in parties:
            if p in PARTY_COLORS:
                return PARTY_COLORS[p]
        return "#444"

    rows_html = []
    has_fm = (winners["seat_type"] == "FM").any()
    has_rp = (winners["seat_type"] == "RP").any()

    def th(label: str) -> str:
        return f'<th style="color:#888;font-size:0.72rem;text-align:right;padding:6px 8px;font-weight:500">{label}</th>'

    def td_num(val: int, bold: bool = False) -> str:
        style = "color:#eee;font-size:0.78rem;font-weight:700;text-align:right;padding:4px 8px" if bold else "color:#aaa;font-size:0.78rem;text-align:right;padding:4px 8px"
        return f'<td style="{style}">{val if val > 0 else ""}</td>'

    for bloc_name, parties in blocs:
        bloc_mr  = sum(party_seats.get(p, {}).get("MR", 0) for p in parties)
        bloc_fm  = sum(party_seats.get(p, {}).get("FM", 0) for p in parties)
        bloc_rp  = sum(party_seats.get(p, {}).get("RP", 0) for p in parties)
        bloc_total = bloc_mr + bloc_fm + bloc_rp
        if bloc_total == 0:
            continue
        bloc_pct = bloc_total / total_seats * 100
        bg = bloc_bg(parties)
        rows_html.append(f"""
        <tr class="bloc-header" style="background:{bg}22; border-left:3px solid {bg}">
          <td colspan="2" style="color:{bg}; font-weight:700; font-size:0.78rem; padding:6px 8px; letter-spacing:0.02em">{bloc_name}</td>
          <td style="color:#bbb; font-size:0.75rem; text-align:right; padding:6px 8px">{bloc_mr}</td>
          {"<td style='color:#bbb; font-size:0.75rem; text-align:right; padding:6px 8px'>" + str(bloc_fm) + "</td>" if has_fm else ""}
          {"<td style='color:#bbb; font-size:0.75rem; text-align:right; padding:6px 8px'>" + str(bloc_rp) + "</td>" if has_rp else ""}
          <td style="color:#eee; font-weight:700; text-align:right; padding:6px 8px">{bloc_total}</td>
          <td style="color:#aaa; font-size:0.75rem; text-align:right; padding:6px 8px">{bloc_pct:.1f}%</td>
        </tr>""")
        for p in parties:
            s = party_seats.get(p)
            if not s or s["Total"] == 0:
                continue
            # Skip party row when it's the same entity as the bloc header (senate actors)
            if p == bloc_name:
                continue
            pct = s["Total"] / total_seats * 100
            color = party_color(p)
            rows_html.append(f"""
        <tr class="party-row">
          <td style="padding:4px 8px 4px 18px">
            <span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:{color};margin-right:6px;vertical-align:middle"></span>
            <span style="color:#ddd; font-size:0.78rem">{p}</span>
          </td>
          <td></td>
          {td_num(s['MR'])}
          {"" + td_num(s['FM']) if has_fm else ""}
          {"" + td_num(s['RP']) if has_rp else ""}
          {td_num(s['Total'], bold=True)}
          <td style="color:#888; font-size:0.75rem; text-align:right; padding:4px 8px">{pct:.1f}%</td>
        </tr>""")

    header = f"""
    <table style="width:100%; border-collapse:collapse; font-family:'IBM Plex Mono',monospace">
      <thead>
        <tr style="border-bottom:1px solid #333">
          <th colspan="2" style="color:#888;font-size:0.72rem;text-align:left;padding:6px 8px;font-weight:500">Partido / Coalición</th>
          {th("MR")}
          {th("1ª Min") if has_fm else ""}
          {th("RP") if has_rp else ""}
          {th("Total")}
          {th("%")}
        </tr>
      </thead>
      <tbody>
        {"".join(rows_html)}
        <tr style="border-top:1px solid #444; margin-top:4px">
          <td colspan="2" style="color:#eee;font-size:0.78rem;font-weight:700;padding:8px 8px">Total</td>
          <td style="color:#eee;font-size:0.78rem;font-weight:700;text-align:right;padding:8px 8px">{(winners["seat_type"]=="MR").sum()}</td>
          {'<td style="color:#eee;font-size:0.78rem;font-weight:700;text-align:right;padding:8px 8px">' + str((winners["seat_type"]=="FM").sum()) + '</td>' if has_fm else ''}
          {'<td style="color:#eee;font-size:0.78rem;font-weight:700;text-align:right;padding:8px 8px">' + str((winners["seat_type"]=="RP").sum()) + '</td>' if has_rp else ''}
          <td style="color:#eee;font-size:0.78rem;font-weight:700;text-align:right;padding:8px 8px">{total_seats}</td>
          <td style="color:#eee;font-size:0.78rem;font-weight:700;text-align:right;padding:8px 8px">100.0%</td>
        </tr>
      </tbody>
    </table>"""

    return f"""
    <div style="padding:12px 0 0 0">
      <div style="color:#888; font-size:0.7rem; font-family:'IBM Plex Mono',monospace; margin-bottom:10px; letter-spacing:0.05em; text-transform:uppercase">Composición · {election_id.replace('_',' ')}</div>
      {header}
    </div>"""


# ── DB queries ─────────────────────────────────────────────────────────────────

def get_elections(conn: sqlite3.Connection, prefix: str) -> list[str]:
    return pd.read_sql_query(
        "SELECT election_id FROM dim_election WHERE election_id LIKE ? ORDER BY election_id",
        conn, params=(f"{prefix}_%",),
    )["election_id"].tolist()


# ── Diputados seat rows ────────────────────────────────────────────────────────

def dip_winners_from_votes(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """Return a vote-based 300 MR + 200 RP QA approximation."""
    raw = dip_mod.district_votes(conn, election_id)
    if raw.empty:
        return pd.DataFrame()

    # MR: one row per district winner with geography
    idx = raw.groupby(["id_estado", "id_distrito_federal"])["votes"].idxmax()
    mr = raw.loc[idx].copy()
    totals = raw.groupby(["id_estado", "id_distrito_federal"])["votes"].sum().rename("votes_total").reset_index()
    mr = mr.merge(totals, on=["id_estado", "id_distrito_federal"])
    mr["pct_winner"]     = (mr["votes"] / mr["votes_total"] * 100).round(1)
    mr["canonical_party"] = mr["party_key"].map(lambda k: COALITION_TO_PARTY.get(k, k))
    mr["seat_type"]      = "MR"

    # RP: natural quotient/largest remainder via the QA allocation module
    mr_for_rp = mr.rename(columns={"party_key": "party_key_raw"}).assign(party=mr["canonical_party"], seats=1)
    rp_counts = dip_mod.rp_allocation(conn, election_id, mr_for_rp)
    # Fallback: use one national largest-remainder allocation for 200 seats
    if rp_counts.empty:
        nat_votes = (
            mr.groupby("canonical_party")["votes"].sum()
            .rename_axis("party").reset_index()
        )
        total_v = nat_votes["votes"].sum()
        qualified = nat_votes[nat_votes["votes"] / total_v >= THRESHOLD_PCT]["party"].tolist()
        vote_map = {r["party"]: float(r["votes"]) for _, r in nat_votes[nat_votes["party"].isin(qualified)].iterrows()}
        seats_map = largest_remainder(vote_map, RP_SEATS_TOTAL)
        rp_counts = pd.DataFrame(
            [{"party": p, "seat_type": "RP", "seats": s} for p, s in seats_map.items() if s > 0]
        )
    rp_rows = []
    for _, r in rp_counts.iterrows():
        for i in range(int(r["seats"])):
            rp_rows.append({
                "party_key": r["party"], "canonical_party": r["party"],
                "id_estado": 0, "nombre_estado": "RP",
                "id_distrito_federal": i + 1,
                "cabecera_distrital_federal": f"Lista RP {i+1}",
                "votes": 0, "votes_total": 0, "pct_winner": 0.0,
                "seat_type": "RP",
            })
    df_rp = pd.DataFrame(rp_rows) if rp_rows else pd.DataFrame()
    return pd.concat([mr.reset_index(drop=True), df_rp], ignore_index=True)


# ── Senate seat rows ───────────────────────────────────────────────────────────

def _party_to_sen_bloc(party: str, election_id: str) -> str:
    """Map a canonical party name to its senate actor/bloc for coalition grouping."""
    from aux_scripts.seat_allocations.common import ELECTION_GROUPS, year_from_election_id
    year = year_from_election_id(election_id)
    groups = ELECTION_GROUPS.get(year, {})
    for bloc, members in groups.items():
        if party in members:
            return bloc
    return party  # independent or unregistered coalition


def sen_winners_from_votes(conn: sqlite3.Connection, election_id: str) -> pd.DataFrame:
    """128 Senate seat rows: 64 MR winner + 32 first-minority + 32 RP."""
    actor_df = sen_mod.mr_actor_seats(conn, election_id)
    if actor_df.empty:
        return pd.DataFrame()

    rows = []
    for _, r in actor_df.iterrows():
        n = int(r["seats"])
        stype = "MR" if r["seat_type"] == "MR" else "FM"
        for i in range(n):
            rows.append({
                "party_key":               r["party"],
                "canonical_party":         r["party"],
                "id_estado":               int(r["id_estado"]),
                "nombre_estado":           r["nombre_estado"],
                "id_distrito_federal":     i + 1,
                "cabecera_distrital_federal": r["nombre_estado"],
                "votes":     int(r["votes"]),
                "votes_total": 0, "pct_winner": 0.0,
                "seat_type": stype,
            })

    rp_counts = sen_mod.rp_allocation(conn, election_id)
    for _, r in rp_counts.iterrows():
        # Map canonical party → bloc so RP seats group with MR/FM under the same actor
        bloc = _party_to_sen_bloc(r["party"], election_id)
        for i in range(int(r["seats"])):
            rows.append({
                "party_key": bloc, "canonical_party": bloc,
                "id_estado": 0, "nombre_estado": "RP",
                "id_distrito_federal": i + 1,
                "cabecera_distrital_federal": "Lista RP Nacional",
                "votes": 0, "votes_total": 0, "pct_winner": 0.0,
                "seat_type": "RP",
            })
    return pd.DataFrame(rows)


def load_from_integracion(path: str, chamber: str = "DIP") -> pd.DataFrame:
    """Load final INE seat assignments for one chamber.

    ``PARTIDO_POLITICO`` is the party that owns the seat; the coalition banner
    is retained separately.  For the Senate, list positions 1–2 are majority
    seats and position 3 is the first-minority seat.
    """
    chamber = chamber.upper()
    if chamber not in {"DIP", "SEN"}:
        raise ValueError(f"Unsupported chamber: {chamber}")

    df = pd.read_csv(path, encoding="utf-8-sig")
    df = df[df["TIPO_DE_CANDIDATURA"].isin({f"{chamber}_MR", f"{chamber}_RP"})].copy()
    rows = []

    for _, r in df.iterrows():
        is_rp = r["TIPO_DE_CANDIDATURA"] == f"{chamber}_RP"
        list_number = int(r["NUMERO_LISTA"]) if pd.notna(r["NUMERO_LISTA"]) else 0
        if is_rp:
            seat_type = "RP"
        elif chamber == "SEN" and list_number == 3:
            seat_type = "FM"
        else:
            seat_type = "MR"

        party = str(r["PARTIDO_POLITICO"]).strip()
        state_name = "RP" if is_rp else str(r["NOMBRE_ESTADO"]).strip()
        if is_rp and chamber == "DIP":
            circ = int(r["CIRCUNSCRIPCION"]) if pd.notna(r["CIRCUNSCRIPCION"]) else 0
            location = f"Circ. {circ} · Lista {list_number}"
        elif is_rp:
            location = f"Lista nacional · {list_number}"
        elif chamber == "DIP":
            location = str(r["CABECERA_DISTRITAL_FEDERAL"]).strip()
        else:
            location = state_name

        rows.append({
            "diputado_id": diputado_id_for_row(r) if chamber == "DIP" else None,
            "senador_seat_id": senador_id_for_row(r) if chamber == "SEN" else None,
            "party_key": party,
            "canonical_party": COALITION_TO_PARTY.get(party, party),
            "id_estado": int(r["ID_ESTADO"]) if pd.notna(r["ID_ESTADO"]) else 0,
            "nombre_estado": state_name,
            "id_distrito_federal": (
                int(r["ID_DISTRITO_FEDERAL"])
                if chamber == "DIP" and pd.notna(r["ID_DISTRITO_FEDERAL"])
                else list_number
            ),
            "cabecera_distrital_federal": location,
            "candidate_name": (
                str(r["PERSONA_CANDIDATA"]).strip()
                if pd.notna(r["PERSONA_CANDIDATA"])
                else (
                    str(r["PERSONA_CANDIDATA_SUPLENTE"]).strip()
                    if pd.notna(r["PERSONA_CANDIDATA_SUPLENTE"])
                    else ""
                )
            ),
            "coalition_banner": str(r["NOMBRE_ACTOR_POLITICO"]).strip(),
            "votes": float(r["VOTACION_GANADOR"]) if not is_rp and pd.notna(r["VOTACION_GANADOR"]) else 0,
            "votes_total": 0,
            "pct_winner": (
                float(str(r["PORCENTAJE_VOTACION_GANADOR"]).replace("%", "").strip())
                if not is_rp and pd.notna(r["PORCENTAJE_VOTACION_GANADOR"])
                else 0.0
            ),
            "seat_type": seat_type,
        })

    return pd.DataFrame(rows)


# ── Hemicycle layout ───────────────────────────────────────────────────────────

def _layout_params(n_seats: int) -> tuple[int, float, float]:
    """Return (n_rows, r_min, r_max) tuned so seats look packed at any chamber size."""
    if n_seats <= 130:
        # Fewer, wider rows so each row holds ~20-30 seats → tighter angular spacing
        return 5, 1.5, 2.4
    if n_seats <= 200:
        return 7, 1.2, 2.1
    return 10, 1.0, 2.2


def hemicycle_positions(n_seats: int, n_rows: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    n_rows_default, r_min, r_max = _layout_params(n_seats)
    if n_rows is None:
        n_rows = n_rows_default
    radii = np.linspace(r_min, r_max, n_rows)
    weights = radii / radii.sum()
    seats_per_row = np.round(weights * n_seats).astype(int)
    # Fix rounding: ensure total equals n_seats, adjusting largest row
    diff = n_seats - seats_per_row.sum()
    seats_per_row[seats_per_row.argmax()] += diff
    # Guard: no row can be negative
    seats_per_row = np.maximum(seats_per_row, 0)

    xs, ys = [], []
    for r, n in zip(radii, seats_per_row):
        if n <= 0:
            continue
        angles = np.linspace(np.pi, 0, n, endpoint=True)
        xs.extend(r * np.cos(angles))
        ys.extend(r * np.sin(angles))
    return np.array(xs), np.array(ys)


# ── Build per-config trace data ────────────────────────────────────────────────

def sorted_winners(
    winners: pd.DataFrame,
    estado_filter: str,   # "Nacional" or a state name
    sort_by: str,         # "partido" | "estado"
    view_mode: str,       # "coalition" | "party"
) -> pd.DataFrame:
    df = winners.copy()
    if estado_filter != "Nacional":
        df = df[df["nombre_estado"] == estado_filter]

    color_col = "party_key" if view_mode == "coalition" else "canonical_party"

    if sort_by == "partido":
        # ``layout_party`` is the immutable election-time anchor used by the
        # current-composition cache.  Colors may change with parliamentary
        # affiliation, but a constitutional seat must never jump coordinates
        # when the user switches views.
        layout_col = "layout_party" if "layout_party" in df.columns else color_col
        seat_counts = df.groupby(layout_col)["id_distrito_federal"].count().sort_values(ascending=False)
        order = {k: i for i, k in enumerate(seat_counts.index)}
        df["_sort_key"] = df[layout_col].map(order)
        # MR seats before RP seats within each party
        df["_type_order"] = (df["seat_type"] == "RP").astype(int)
        df = df.sort_values(["_sort_key", "_type_order", "id_estado", "id_distrito_federal"])
    else:
        # By state: RP rows go last (nombre_estado="RP" sorts after real states alphabetically)
        df = df.sort_values(["nombre_estado", "id_estado", "id_distrito_federal"])

    return df.reset_index(drop=True)


def build_trace_data(
    df: pd.DataFrame,
    view_mode: str,
    n_rows: int | None = None,
) -> dict[str, dict]:
    """
    Returns a dict mapping each unique (color_key, seat_type) →
    {x, y, text, symbol, color, seats}.
    MR seats → square markers; RP seats → circle markers.
    Positions span all seats together so MR and RP interleave spatially.
    """
    n = len(df)
    if n == 0:
        return {}

    xs, ys = hemicycle_positions(n, n_rows=n_rows)
    color_col = "party_key" if view_mode == "coalition" else "canonical_party"

    out: dict[str, dict] = {}
    for i, row in df.iterrows():
        key       = row[color_col]
        seat_type = row.get("seat_type", "MR")
        trace_key = f"{key}__{seat_type}"
        if trace_key not in out:
            symbol = "square" if seat_type == "MR" else ("diamond" if seat_type == "FM" else "circle")
            out[trace_key] = {
                "x": [], "y": [], "text": [], "customdata": [],
                "color": party_color(key),
                "symbol": symbol,
                "party_key": key,
                "seat_type": seat_type,
            }
        out[trace_key]["x"].append(float(xs[i]))
        out[trace_key]["y"].append(float(ys[i]))
        candidate = str(row.get("candidate_name", "") or "").strip()
        location = str(row.get("cabecera_distrital_federal", "") or "").strip()
        out[trace_key]["customdata"].append([
            candidate,
            str(row.get("canonical_party", key)),
            seat_type,
            str(row.get("nombre_estado", "")),
            location,
            str(row.get("diputado_id", "") or ""),
            str(row.get("senador_seat_id", "") or ""),
            str(row.get("vote_person_id", "") or ""),
            str(row.get("roster_status", "electoral") or "electoral"),
            str(row.get("election_candidate_name", candidate) or ""),
            str(row.get("election_party", row.get("canonical_party", key)) or ""),
            str(row.get("reported_current_party", "") or ""),
        ])

        status = str(row.get("roster_status", "electoral") or "electoral")
        status_line = "" if status == "electoral" else f"<br>Estatus: {status.replace('_', ' ').title()}"
        election_party = str(row.get("election_party", "") or "")
        reported_current_party = str(row.get("reported_current_party", "") or "")
        origin_line = (
            f"<br>Partido electoral: {election_party}"
            if status != "electoral" and election_party and election_party != str(row[color_col])
            else ""
        )
        directory_party_line = (
            f"<br>Grupo parlamentario registrado: {reported_current_party}"
            if status == "licencia" and reported_current_party
            else ""
        )

        if seat_type == "MR":
            loc = location or f"D{int(row['id_distrito_federal'])}"
            candidate_line = (
                f"Candidatura: {display_person_name(candidate)}<br>" if candidate else ""
            )
            out[trace_key]["text"].append(
                f"<b>{row['nombre_estado']} · {loc}</b><br>"
                f"{candidate_line}"
                f"Partido: {row[color_col]}<br>"
                f"Votos: {int(row['votes']):,}<br>"
                f"% del total: {row['pct_winner']:.1f}%"
                f"{status_line}{origin_line}{directory_party_line}"
            )
        elif seat_type == "FM":
            candidate_line = (
                f"Candidatura: {display_person_name(candidate)}<br>" if candidate else ""
            )
            out[trace_key]["text"].append(
                f"<b>{row['nombre_estado']} · Primera Minoría</b><br>"
                f"{candidate_line}"
                f"Partido: {key}<br>"
                f"Votos: {int(row['votes']):,}<br>"
                f"% del total: {row['pct_winner']:.1f}%"
                f"{status_line}{origin_line}{directory_party_line}"
            )
        else:
            candidate_line = (
                f"Candidatura: {display_person_name(candidate)}<br>" if candidate else ""
            )
            out[trace_key]["text"].append(
                f"<b>Escaño RP · {location}</b><br>"
                f"{candidate_line}"
                f"Partido: {key}<br>"
                f"Asignado por representación proporcional"
                f"{status_line}{origin_line}{directory_party_line}"
            )

    for k in out:
        out[k]["seats"] = len(out[k]["x"])

    return out


# ── Build interactive figure ───────────────────────────────────────────────────

def build_figure(winners: pd.DataFrame, election_id: str) -> go.Figure:
    is_senate = election_id.startswith("SEN")
    n_total   = len(winners)
    # Marker size: larger for smaller chambers so dots look packed
    marker_sz = 14 if n_total <= 130 else (12 if n_total <= 200 else 10)

    estados = ["Nacional"] + sorted(
        winners.loc[winners["nombre_estado"] != "RP", "nombre_estado"].dropna().unique()
    )

    # Only coalition view, partido sort — estado filter is the sole control
    all_configs: dict[str, dict] = {}
    for estado in estados:
        df_sorted = sorted_winners(winners, estado, "partido", "coalition")
        all_configs[estado] = build_trace_data(df_sorted, "coalition")

    # Union of all trace keys across estados
    all_keys: list[str] = []
    seen: set[str] = set()
    for cfg in all_configs.values():
        for k in cfg:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    default_cfg = all_configs["Nacional"]

    seat_counts = winners["seat_type"].value_counts()
    n_mr = seat_counts.get("MR", 0)
    n_fm = seat_counts.get("FM", 0)
    n_rp = seat_counts.get("RP", 0)
    chamber = "Senado" if is_senate else "Cámara de Diputados"

    def make_title(estado: str, n: int) -> str:
        if estado == "Nacional":
            if is_senate:
                loc = f"Nacional ({n_mr} MR + {n_fm} 1ª Min + {n_rp} RP = {n_mr+n_fm+n_rp} escaños)"
            else:
                loc = f"Nacional ({n_mr} MR + {n_rp} RP = {n_mr+n_rp} escaños)"
        else:
            loc = f"{estado} ({n} escaños)"
        return f"<b>{chamber} · {election_id.replace('_',' ')} · {loc}</b>"

    # Build traces from default (Nacional) config
    traces: list[go.Scatter] = []
    for trace_key in all_keys:
        d     = default_cfg.get(trace_key, {"x": [], "y": [], "text": [], "color": "#666",
                                             "customdata": [],
                                             "symbol": "square", "party_key": trace_key,
                                             "seat_type": "MR", "seats": 0})
        seats = d["seats"]
        pk    = d.get("party_key", trace_key.split("__")[0])
        stype = d.get("seat_type", "MR")
        stype_lbl = "MR" if stype == "MR" else ("1ª Min" if stype == "FM" else "RP")
        label = f"{pk} {stype_lbl} ({seats})" if seats > 0 else trace_key
        traces.append(go.Scatter(
            x=d["x"], y=d["y"],
            mode="markers",
            marker=dict(
                symbol=d.get("symbol", "square"),
                size=marker_sz if stype == "MR" else int(marker_sz * 0.9),
                color=d["color"],
                opacity=1.0 if stype == "MR" else (0.85 if stype == "FM" else 0.65),
                line=dict(width=0.5, color="rgba(0,0,0,0.25)"),
            ),
            name=label,
            hovertemplate="%{text}<extra></extra>",
            text=d["text"],
            customdata=d["customdata"],
            visible=seats > 0,
        ))

    fig = go.Figure(data=traces)

    # ── Helper: restyle payload for one estado ────────────────────────────────
    def restyle_args(cfg: dict, title_text: str) -> tuple[dict, dict]:
        xs_list, ys_list, texts_list = [], [], []
        customdata_list, names_list, visibles_list = [], [], []
        for trace_key in all_keys:
            d     = cfg.get(trace_key, {"x": [], "y": [], "text": [], "seats": 0,
                                         "customdata": [],
                                         "party_key": trace_key.split("__")[0], "seat_type": "MR"})
            seats = d["seats"]
            pk    = d.get("party_key", trace_key.split("__")[0])
            stype = d.get("seat_type", "MR")
            stype_lbl = "MR" if stype == "MR" else ("1ª Min" if stype == "FM" else "RP")
            label = f"{pk} {stype_lbl} ({seats})" if seats > 0 else trace_key
            xs_list.append(d["x"])
            ys_list.append(d["y"])
            texts_list.append(d["text"])
            customdata_list.append(d["customdata"])
            names_list.append(label)
            visibles_list.append(seats > 0)
        return (
            {"x": xs_list, "y": ys_list, "text": texts_list, "customdata": customdata_list,
             "name": names_list, "visible": visibles_list},
            {"title.text": title_text},
        )

    # ── Estado dropdown (only control) ───────────────────────────────────────
    estado_buttons = []
    for estado in estados:
        cfg = all_configs[estado]
        n   = sum(d["seats"] for d in cfg.values())
        rs, rl = restyle_args(cfg, make_title(estado, n))
        estado_buttons.append(dict(label=estado, method="update", args=[rs, rl]))

    _, _, r_max = _layout_params(n_total)
    pad = 0.35
    x_range = [-(r_max + pad), r_max + pad]
    y_range = [-0.15, r_max + pad]

    n_default = sum(d["seats"] for d in default_cfg.values())
    fig.update_layout(
        title=dict(
            text=make_title("Nacional", n_default),
            font=dict(family="IBM Plex Mono", size=13),
            x=0.5, xanchor="center",
        ),
        xaxis=dict(visible=False, range=x_range),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1, range=y_range),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="#111111",
        font=dict(family="IBM Plex Sans", color="#CCCCCC"),
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.02,
            xanchor="center", x=0.5,
            font=dict(family="IBM Plex Mono", size=10),
            itemsizing="constant",
        ),
        height=580,
        margin=dict(l=20, r=20, t=80, b=120),
        updatemenus=[
            dict(
                type="dropdown", direction="down",
                x=0.5, xanchor="center", y=1.1, yanchor="top",
                bgcolor="#222", bordercolor="#555", font=dict(color="#EEE", size=11),
                buttons=estado_buttons,
                showactive=True, active=0,
            ),
        ],
        annotations=[
            dict(text="Estado:", x=0.38, xref="paper", y=1.115, yref="paper",
                 showarrow=False, font=dict(size=11, color="#AAA"), xanchor="right"),
        ],
    )
    return fig


# ── Main ───────────────────────────────────────────────────────────────────────

def build_election_html(
    conn: sqlite3.Connection, election_id: str, *, allow_vote_approximation: bool = False
) -> str:
    """Build the flex-row div (hemicycle + table) for one election."""
    print(f"\n  Building {election_id}...")

    integracion_path = INTEGRACION_PATHS.get(election_id)
    if integracion_path and os.path.exists(integracion_path):
        chamber = "DIP" if election_id.startswith("DIP") else "SEN"
        print("    Using final INE integration")
        winners = load_from_integracion(integracion_path, chamber=chamber)
    elif not allow_vote_approximation:
        print("    Skipping: no final INE integration (vote approximation not enabled)")
        return ""
    elif election_id.startswith("DIP"):
        print("    Computing an unofficial vote-based approximation")
        winners = dip_winners_from_votes(conn, election_id)
    else:
        print("    Computing an unofficial vote-based approximation")
        winners = sen_winners_from_votes(conn, election_id)

    if winners is None or winners.empty:
        return ""

    counts = winners["seat_type"].value_counts().to_dict()
    print(f"    Seats: { {k: counts.get(k,0) for k in ['MR','FM','RP']} } = {len(winners)} total")

    fig        = build_figure(winners, election_id)
    table_html = build_summary_html(winners, election_id)
    fig_html   = fig.to_html(full_html=False, include_plotlyjs=False)

    return (
        f'<div style="display:flex; gap:24px; align-items:flex-start">'
        f'<div style="flex:1 1 0; min-width:0">{fig_html}</div>'
        f'<div style="width:320px; flex-shrink:0; background:#1a1a1a; border-radius:8px;'
        f' padding:16px; align-self:flex-start; margin-top:24px">{table_html}</div>'
        f'</div>'
    )


def build_chamber_section(conn: sqlite3.Connection, prefix: str, label: str) -> str:
    """Build the year-tab section for one chamber (DIP or SEN)."""
    elections = get_elections(conn, prefix)
    if not elections:
        return ""

    print(f"\n{'='*55}\n{label}\n{'='*55}")

    tab_id_prefix = prefix.replace("_", "")
    panels = ""
    for eid in elections:
        content = build_election_html(conn, eid)
        if content:
            panels += f'<div id="{eid}" class="tab-content" style="display:none">{content}</div>'

    year_buttons = "\n".join(
        f'<button class="tab-btn" onclick="showTab(\'{eid}\')">'
        f'{eid.replace(prefix + "_", "")}'
        f'</button>'
        for eid in elections
    )
    first = elections[0] if elections else ""

    return f"""
    <section class="chamber-section">
      <h2 class="chamber-title">{label}</h2>
      <div class="tab-bar">{year_buttons}</div>
      {panels}
      <script>showTab('{first}');</script>
    </section>"""


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)

    dip_section = build_chamber_section(conn, "DIP_MR", "Cámara de Diputados · 500 escaños")
    sen_section = build_chamber_section(conn, "SEN_MR", "Senado de la República · 128 escaños")

    conn.close()

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Hemicycle · México</title>
  <script src="https://cdn.plot.ly/plotly-2.26.0.min.js"></script>
  <style>
    body {{ background:#111; color:#ccc; font-family:'IBM Plex Sans',sans-serif; margin:0; padding:16px; }}
    .chamber-title {{
      font-family:'IBM Plex Mono',monospace; font-size:1rem; color:#eee;
      margin:24px 0 10px; border-bottom:1px solid #333; padding-bottom:8px;
    }}
    .tab-bar {{ display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }}
    .tab-btn {{
      background:#222; color:#ccc; border:1px solid #444; border-radius:3px;
      padding:6px 14px; cursor:pointer; font-family:'IBM Plex Mono',monospace; font-size:0.85rem;
    }}
    .tab-btn:hover  {{ background:#333; color:#fff; }}
    .tab-btn.active {{ background:#8B0000; color:#fff; border-color:#8B0000; }}
    .tab-content    {{ display:none; }}
    .chamber-section {{ margin-bottom:48px; }}
  </style>
</head>
<body>
  {dip_section}
  {sen_section}
  <script>
    function showTab(id) {{
      // Only hide/show tabs within the same section
      var el = document.getElementById(id);
      if (!el) return;
      var section = el.closest('.chamber-section');
      section.querySelectorAll('.tab-content').forEach(function(e) {{ e.style.display='none'; }});
      section.querySelectorAll('.tab-btn').forEach(function(e) {{ e.classList.remove('active'); }});
      el.style.display = 'block';
      var btn = section.querySelector('[onclick="showTab(\\'' + id + '\\')"]');
      if (btn) btn.classList.add('active');
    }}
  </script>
</body>
</html>"""

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✓ Written → {OUT_PATH}")
    print("  Two chambers, each with year tabs. Controls: Vista / Orden / Estado.")
