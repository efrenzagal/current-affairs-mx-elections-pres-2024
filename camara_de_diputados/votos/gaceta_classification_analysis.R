# Exploratory analysis of LLM classifications for Gaceta Parlamentaria votes,
# joined against actual vote outcomes (favor/contra/abstención/ausente).
#
# Run this file from the repository root in RStudio. It is read-only with
# respect to election_data.db: the SQL views below are TEMPORARY and exist
# only for the current R connection.
#
# Required packages:
# install.packages(c("DBI", "RSQLite", "dplyr", "tidyr", "ggplot2", "scales"))

library(DBI)
library(RSQLite)
library(dplyr)
library(tidyr)
library(ggplot2)
library(scales)

setwd('Documents/GitHub/current-affairs-mx-elections-pres-2024/')
db_path <- "election_data.db"
stopifnot(file.exists(db_path))

con <- dbConnect(SQLite(), db_path)
# Keep `con` open after Source so the temporary views remain available for
# interactive follow-up queries. Run `dbDisconnect(con)` when you are finished.

# -----------------------------------------------------------------------------
# Ingestion: every Gaceta vote, its outcome totals, and its classification
# (LEFT JOIN, so unclassified votes still appear with NA classification
# fields rather than being silently dropped).
# -----------------------------------------------------------------------------

dbExecute(con, "DROP VIEW IF EXISTS temp.v_gaceta_resultados")
dbExecute(con, "
  CREATE TEMP VIEW v_gaceta_resultados AS
  SELECT
    gaceta_vote_id,
    SUM(CASE WHEN vote_choice = 'Favor' THEN count ELSE 0 END) AS favor,
    SUM(CASE WHEN vote_choice = 'Contra' THEN count ELSE 0 END) AS contra,
    SUM(CASE WHEN vote_choice = 'Abstención' THEN count ELSE 0 END) AS abstencion,
    SUM(CASE WHEN vote_choice = 'Ausente' THEN count ELSE 0 END) AS ausente,
    SUM(CASE WHEN vote_choice = 'Quórum *' THEN count ELSE 0 END) AS quorum_sin_voto,
    SUM(CASE WHEN vote_choice = 'Total' THEN count ELSE 0 END) AS total
  FROM fact_gaceta_vote_summary
  WHERE party_key = 'Total'
  GROUP BY gaceta_vote_id
")

dbExecute(con, "DROP VIEW IF EXISTS temp.v_gaceta_votos_clasificados")
dbExecute(con, "
  CREATE TEMP VIEW v_gaceta_votos_clasificados AS
  SELECT
    v.gaceta_vote_id,
    v.legislature,
    v.vote_date,
    v.gaceta_number,
    v.title,
    v.vote_context,
    r.favor, r.contra, r.abstencion, r.ausente, r.quorum_sin_voto, r.total,
    c.origen,
    c.etapa_votacion,
    c.tipo_instrumento,
    c.tema_politica,
    c.confianza,
    c.requiere_revision,
    c.evidencia,
    c.model,
    c.prompt_version,
    c.classified_at
  FROM dim_gaceta_vote AS v
  LEFT JOIN v_gaceta_resultados AS r ON r.gaceta_vote_id = v.gaceta_vote_id
  LEFT JOIN fact_gaceta_vote_classification AS c ON v.gaceta_vote_id = c.gaceta_vote_id
")

votos_clasificados <- dbGetQuery(con, "
  SELECT *
  FROM v_gaceta_votos_clasificados
  ORDER BY legislature, vote_date, gaceta_vote_id
") %>%
  mutate(
    vote_date = as.Date(vote_date),
    year = as.integer(format(vote_date, '%Y')),
    clasificado = !is.na(tema_politica),
    # Consensus/contentiousness: 1.0 = unanimous among favor+contra,
    # 0.5 = an even split (the most contentious a vote can be).
    votos_efectivos = favor + contra,
    margen = if_else(votos_efectivos > 0, pmax(favor, contra) / votos_efectivos, NA_real_),
    contencioso = 1 - margen
  )

cat(sprintf(
  "%d votos totales, %d clasificados (%.1f%%)\n",
  nrow(votos_clasificados), sum(votos_clasificados$clasificado),
  100 * mean(votos_clasificados$clasificado)
))

# -----------------------------------------------------------------------------
# Core tables for inspection
# -----------------------------------------------------------------------------

resumen_general <- votos_clasificados %>%
  filter(clasificado) %>%
  summarise(
    votos = n(),
    legislaturas = n_distinct(legislature),
    confianza_media = mean(confianza),
    confianza_mediana = median(confianza),
    para_revision = sum(requiere_revision),
    porcentaje_revision = 100 * mean(requiere_revision)
  )

resumen_origen <- votos_clasificados %>%
  filter(clasificado) %>%
  count(origen, sort = TRUE) %>%
  mutate(porcentaje = 100 * n / sum(n))

resumen_etapa <- votos_clasificados %>%
  filter(clasificado) %>%
  count(etapa_votacion, sort = TRUE) %>%
  mutate(porcentaje = 100 * n / sum(n))

resumen_instrumento <- votos_clasificados %>%
  filter(clasificado) %>%
  count(tipo_instrumento, sort = TRUE) %>%
  mutate(porcentaje = 100 * n / sum(n))

resumen_tema <- votos_clasificados %>%
  filter(clasificado) %>%
  count(tema_politica, sort = TRUE) %>%
  mutate(porcentaje = 100 * n / sum(n))

# Topic composition per legislature (the original cross-section).
temas_por_legislatura <- votos_clasificados %>%
  filter(clasificado) %>%
  count(legislature, tema_politica, name = "votos") %>%
  group_by(legislature) %>%
  mutate(porcentaje = 100 * votos / sum(votos)) %>%
  ungroup() %>%
  arrange(legislature, desc(votos))

# Topic composition over calendar time (year), independent of legislature
# boundaries — useful for spotting trends that don't align with a term.
temas_por_anio <- votos_clasificados %>%
  filter(clasificado, !is.na(year)) %>%
  count(year, tema_politica, name = "votos") %>%
  group_by(year) %>%
  mutate(porcentaje = 100 * votos / sum(votos)) %>%
  ungroup() %>%
  arrange(year, desc(votos))

etapa_por_instrumento <- votos_clasificados %>%
  filter(clasificado) %>%
  count(etapa_votacion, tipo_instrumento, name = "votos") %>%
  arrange(desc(votos))

# These rows are the first manual-review queue. The model's confidence is a
# ranking signal, not a measured probability of correctness.
cola_revision <- votos_clasificados %>%
  filter(clasificado, requiere_revision == 1) %>%
  arrange(confianza, legislature, vote_date) %>%
  select(
    gaceta_vote_id, legislature, vote_date, confianza,
    origen, etapa_votacion, tipo_instrumento, tema_politica,
    title, vote_context, evidencia
  )

confianza_baja <- votos_clasificados %>%
  filter(clasificado, confianza < 0.80) %>%
  arrange(confianza, legislature, vote_date) %>%
  select(
    gaceta_vote_id, legislature, vote_date, confianza, requiere_revision,
    origen, etapa_votacion, tipo_instrumento, tema_politica,
    title, vote_context, evidencia
  )

# A focused view of cases whose topic cannot be confidently inferred from the
# source record. These should not be treated as substantive topic assignments.
tema_no_claro <- votos_clasificados %>%
  filter(clasificado, tema_politica %in% c("no_claro", "otro", "no_aplica")) %>%
  arrange(tema_politica, confianza) %>%
  select(
    gaceta_vote_id, legislature, vote_date, confianza, requiere_revision,
    tipo_instrumento, tema_politica, title, vote_context, evidencia
  )

# -----------------------------------------------------------------------------
# Consensus vs. contentiousness by topic. `contencioso` is close to 0 when a
# vote is nearly unanimous and approaches 0.5 as favor/contra split evenly.
# -----------------------------------------------------------------------------

consenso_por_tema <- votos_clasificados %>%
  filter(clasificado, requiere_revision == 0, !is.na(contencioso)) %>%
  group_by(tema_politica) %>%
  summarise(
    votos = n(),
    contencioso_media = mean(contencioso),
    contencioso_mediana = median(contencioso),
    margen_media = mean(margen),
    .groups = "drop"
  ) %>%
  arrange(desc(contencioso_media))

consenso_por_tema_legislatura <- votos_clasificados %>%
  filter(clasificado, requiere_revision == 0, !is.na(contencioso)) %>%
  group_by(legislature, tema_politica) %>%
  summarise(
    votos = n(),
    contencioso_media = mean(contencioso),
    .groups = "drop"
  ) %>%
  filter(votos >= 5) %>%
  arrange(legislature, desc(contencioso_media))

print(resumen_general)
print(resumen_etapa)
print(resumen_instrumento)
print(head(resumen_tema, 12))
print(head(cola_revision, 12))
print(consenso_por_tema)

# -----------------------------------------------------------------------------
# Analysis-ready SQL examples
# -----------------------------------------------------------------------------

# Topic totals by legislature, excluding rows explicitly flagged for review.
temas_confiables_por_legislatura <- dbGetQuery(con, "
  SELECT
    legislature,
    tema_politica,
    COUNT(*) AS votos
  FROM v_gaceta_votos_clasificados
  WHERE requiere_revision = 0
  GROUP BY legislature, tema_politica
  ORDER BY legislature, votos DESC, tema_politica
")

# Classification composition by party-vote outcome. This joins the total row
# only, avoiding duplicate vote counts from party-level summary records.
clasificacion_y_resultado <- dbGetQuery(con, "
  SELECT
    c.legislature,
    c.tema_politica,
    c.etapa_votacion,
    SUM(CASE WHEN s.vote_choice = 'Favor' THEN s.count ELSE 0 END) AS favor,
    SUM(CASE WHEN s.vote_choice = 'Contra' THEN s.count ELSE 0 END) AS contra,
    SUM(CASE WHEN s.vote_choice IN ('Abstención', 'Abstencion') THEN s.count ELSE 0 END) AS abstencion
  FROM v_gaceta_votos_clasificados AS c
  LEFT JOIN fact_gaceta_vote_summary AS s
    ON c.gaceta_vote_id = s.gaceta_vote_id
   AND s.party_key = 'Total'
  WHERE c.requiere_revision = 0
  GROUP BY c.legislature, c.tema_politica, c.etapa_votacion
  ORDER BY c.legislature, c.tema_politica, c.etapa_votacion
")

# -----------------------------------------------------------------------------
# Plot objects. In RStudio, running this file displays them in the Plots pane.
# -----------------------------------------------------------------------------

grafica_temas_legislatura <- temas_por_legislatura %>%
  filter(!tema_politica %in% c("no_claro", "otro", "no_aplica")) %>%
  ggplot(aes(x = factor(legislature), y = porcentaje, fill = tema_politica)) +
  geom_col(width = 0.8) +
  scale_y_continuous(labels = label_percent(scale = 1)) +
  labs(
    title = "Composición temática de las votaciones por legislatura",
    x = "Legislatura", y = "Porcentaje de votaciones", fill = "Tema de política"
  ) +
  theme_minimal(base_size = 12) +
  theme(legend.position = "right")

grafica_temas_tiempo <- temas_por_anio %>%
  filter(!tema_politica %in% c("no_claro", "otro", "no_aplica")) %>%
  ggplot(aes(x = year, y = porcentaje, fill = tema_politica)) +
  geom_area(position = "stack") +
  scale_x_continuous(breaks = pretty_breaks()) +
  scale_y_continuous(labels = label_percent(scale = 1)) +
  labs(
    title = "Composición temática de las votaciones por año",
    x = "Año", y = "Porcentaje de votaciones", fill = "Tema de política"
  ) +
  theme_minimal(base_size = 12) +
  theme(legend.position = "right")

grafica_etapa <- resumen_etapa %>%
  mutate(etapa_votacion = reorder(etapa_votacion, n)) %>%
  ggplot(aes(x = etapa_votacion, y = n)) +
  geom_col(fill = "#2C7FB8") +
  coord_flip() +
  scale_y_continuous(labels = comma) +
  labs(
    title = "Etapa descrita de la votación",
    x = NULL, y = "Votaciones"
  ) +
  theme_minimal(base_size = 12)

grafica_confianza <- ggplot(
  votos_clasificados %>% filter(clasificado),
  aes(x = confianza, fill = factor(requiere_revision))
) +
  geom_histogram(binwidth = 0.05, colour = "white") +
  scale_y_log10(labels = comma) +
  scale_fill_manual(
    values = c("0" = "#2C7FB8", "1" = "#D95F0E"),
    labels = c("0" = "No", "1" = "Sí")
  ) +
  labs(
    title = "Distribución de confianza reportada por el modelo (escala log)",
    x = "Confianza reportada", y = "Votaciones", fill = "Requiere revisión"
  ) +
  theme_minimal(base_size = 12)

# Which topics see the most consensus vs. the most contentious splits.
grafica_consenso_tema <- consenso_por_tema %>%
  filter(!tema_politica %in% c("no_claro", "otro", "no_aplica"), votos >= 10) %>%
  mutate(tema_politica = reorder(tema_politica, contencioso_media)) %>%
  ggplot(aes(x = tema_politica, y = contencioso_media)) +
  geom_col(fill = "#D95F0E") +
  coord_flip() +
  scale_y_continuous(labels = label_percent(scale = 100)) +
  labs(
    title = "Contenciosidad promedio por tema de política",
    subtitle = "0% = unánime entre favor/contra · 50% = dividido a la mitad",
    x = NULL, y = "Contenciosidad media"
  ) +
  theme_minimal(base_size = 12)

print(grafica_temas_legislatura)
print(grafica_temas_tiempo)
print(grafica_etapa)
print(grafica_confianza)
print(grafica_consenso_tema)

# Optional exports:
# write.csv(cola_revision, "data/gaceta_vote_classification/cola_revision.csv", row.names = FALSE)
# ggsave("data/gaceta_vote_classification/temas_por_legislatura.png", grafica_temas_legislatura,
#        width = 11, height = 7, dpi = 180)

# When you finish the RStudio session:
# dbDisconnect(con)


library(plotly)
p <- votos_clasificados %>%
  mutate(
    favorable = favor > contra,
    text = paste0(
      gaceta_vote_id, "<br>",
      format(vote_date, "%Y-%m-%d"), " · L", legislature, "<br>",
      strtrim(gsub("<[^>]+>", "", title), 80), "<br>",
      "Favor: ", favor, " · Contra: ", contra, " · Efectivos: ", votos_efectivos, "<br>",
      "Tema: ", tema_politica, " · Etapa: ", etapa_votacion, "<br>",
      if_else(requiere_revision == 1, " ⚠ requiere revisión", "")
    )
  ) %>%
  filter(legislature %in% c(64, 65, 66)) %>%
  ggplot(aes(x = margen, y = votos_efectivos, col = tema_politica, text = text)) +
  facet_wrap(legislature ~ favorable, ncol = 2) +
  geom_point()

ggplotly(p, tooltip = "text")
