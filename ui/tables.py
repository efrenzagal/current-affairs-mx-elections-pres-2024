"""
Scorecards, metric cards, header badges, and results tables.
"""

from typing import Optional

import pandas as pd
import streamlit as st

from ui.common import (
    CANDIDATES, CANDIDATE_PARTY_KEY, PARTY_GROUPS,
    fmt_pct, fmt_num, resolve_candidate_name,
)


def metric_card(label: str, value: str, sub: str = None):
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="metric-card">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def header_badge(tags: list):
    spans = "".join(f'<span class="tag">{t}</span>' for t in tags)
    st.markdown(
        f'<div style="background:#1A1A1A;color:#F7F7F5;padding:0.8rem 1.2rem;'
        f'border-radius:3px;margin-bottom:1rem;">{spans}</div>',
        unsafe_allow_html=True,
    )


def render_scorecards(total_v: int, lista_nom: int, part_pct: float, nulos_pct: float):
    c1, c2, c3 = st.columns(3)
    with c1:
        metric_card(
            "Votos emitidos", fmt_num(total_v),
            sub=f"de {fmt_num(lista_nom)} en lista nominal" if lista_nom else None,
        )
    with c2:
        metric_card("Participación", fmt_pct(part_pct))
    with c3:
        metric_card(
            "Votos nulos", fmt_pct(nulos_pct),
            sub=f"{fmt_num(round(total_v * nulos_pct / 100))} votos" if total_v else None,
        )


def render_results_table(
    df: pd.DataFrame,
    group_cols,
    group_name: str,
    election_id: str,
    candidates_df: pd.DataFrame,
    id_distrito: Optional[int] = None,
):
    """
    Winners-only results table — one row per geographic unit showing the
    winning candidate and their share of votos validos as a progress bar.

    Name resolution only fires for presidential races (single national winner)
    or when both id_estado + id_distrito_federal are in the group columns.
    District numbers repeat across states, so district alone is ambiguous.
    """
    grp = group_cols if isinstance(group_cols, list) else [group_cols]
    grp = [c for c in grp if c in df.columns]
    if not grp:
        return

    shh_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_SHH"]
    fcm_keys = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_FCM"]
    mc_keys  = [k for k in PARTY_GROUPS if PARTY_GROUPS[k]["cand"] == "CAND_MC"]

    agg = df.groupby(grp).apply(lambda g: pd.Series({
        "CAND_SHH":          g[g["party_key"].isin(shh_keys)]["votes"].sum(),
        "CAND_FCM":          g[g["party_key"].isin(fcm_keys)]["votes"].sum(),
        "CAND_MC":           g[g["party_key"].isin(mc_keys)]["votes"].sum(),
        "NUM_VOTOS_VALIDOS": g[g["party_key"] == g["party_key"].iloc[0]]["num_votos_validos"].sum(),
    })).reset_index()

    agg = agg[agg["NUM_VOTOS_VALIDOS"] > 0].sort_values("NUM_VOTOS_VALIDOS", ascending=False)
    if agg.empty:
        return

    election_type    = "_".join(election_id.split("_")[:-1])
    is_presidential  = election_type == "PRE"
    has_district_grp = "id_distrito_federal" in grp
    has_estado_grp   = "id_estado" in grp
    can_resolve_name = is_presidential or (has_district_grp and has_estado_grp)

    records = []
    for _, row in agg.iterrows():
        validos = row["NUM_VOTOS_VALIDOS"]
        votes   = {"SHH": row["CAND_SHH"], "FCM": row["CAND_FCM"], "MC": row["CAND_MC"]}
        winner  = max(votes, key=votes.get)
        pct_win = votes[winner] / validos * 100 if validos > 0 else 0
        label   = " · ".join(str(row[c]) for c in grp)

        ganador_display = winner
        if can_resolve_name:
            row_distrito = int(row["id_distrito_federal"]) if has_district_grp and pd.notna(row.get("id_distrito_federal")) else None
            row_estado   = int(row["id_estado"]) if has_estado_grp and pd.notna(row.get("id_estado")) else None
            resolved = resolve_candidate_name(
                candidates_df, CANDIDATE_PARTY_KEY[winner], election_id,
                id_distrito=row_distrito, id_estado=row_estado,
            )
            if resolved:
                ganador_display = resolved

        records.append({
            group_name:      label,
            "Votos validos": int(validos),
            "Ganador":       ganador_display,
            "% Ganador":     round(pct_win, 1),
        })

    out_df = pd.DataFrame(records)
    st.markdown(
        f'<div class="section-label">{group_name} — ganadores</div>',
        unsafe_allow_html=True,
    )
    st.caption("Haz clic en cualquier columna para ordenar · puedes filtrar con ⌘F")
    st.dataframe(
        out_df,
        use_container_width=True,
        height=min(600, max(200, len(out_df) * 35 + 40)),
        column_config={
            "Votos validos": st.column_config.NumberColumn(format="%d"),
            "% Ganador": st.column_config.ProgressColumn(
                "% Ganador", min_value=0, max_value=100, format="%.1f%%"
            ),
        },
    )
