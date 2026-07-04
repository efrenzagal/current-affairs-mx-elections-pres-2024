{
  
  library(tidyverse)
  library(readxl)
  library(lubridate)
  library(ggplot2)
  library(scales)
  
  approval_archive <- read_xlsx('table-aprobacion_archivo.xlsx') %>%
    mutate(source = "archive")
  
  approval_recent <- read_xlsx('table-aprobacion.xlsx') %>%
    mutate(
      Presidente = case_when(
        str_detect(Mes, "2024") & Mes >= "Oct 2024" ~ "Sheinbaum",
        TRUE ~ "AMLO"
      ),
      source = "recent"
    )
  
  approval_full <- bind_rows(approval_archive, approval_recent) %>%
    mutate(
      fecha = parse_date_time(Mes, orders = "b Y", locale = "es_ES"),
      metodo = case_when(
        str_detect(Encuestadora, regex("telef|/tel|telefon", ignore_case = TRUE)) ~ "Telefónica",
        str_detect(Encuestadora, regex("vivien|/viv",        ignore_case = TRUE)) ~ "Vivienda",
        str_detect(Encuestadora, regex("online|web|internet",ignore_case = TRUE)) ~ "Online",
        TRUE                                                                       ~ "No especificado"
      ),
      encuestadora_clean = str_remove(Encuestadora, regex("/tel|/viv|/online", ignore_case = TRUE)) %>% str_trim(),
      Presidente = factor(Presidente, levels = c("EZPL", "VFQ", "FCH", "EPN", "AMLO", "Sheinbaum"))
    )
  
  GRAY_LIGHT <- "#E8E8E8"
  GRAY_MID   <- "#AAAAAA"
  TEXT_DARK  <- "#1A1A1A"
  
  presidente_colors <- c(
    "EZPL"       = "#2E7D32",
    "VFQ"        = "#1565C0",
    "FCH"        = "#1E90FF",
    "EPN"        = "#C62828",
    "AMLO"       = "#8B0000",
    "Sheinbaum"  = "#C84B31"
  )
  
  presidente_labels <- c(
    "EZPL"      = "Zedillo",
    "VFQ"       = "Fox",
    "FCH"       = "Calderón",
    "EPN"       = "Peña Nieto",
    "AMLO"      = "AMLO",
    "Sheinbaum" = "Sheinbaum"
  )
  
  transiciones <- tibble(
    fecha = as.POSIXct(c(
      "1994-12-01",
      "2000-12-01",
      "2006-12-01",
      "2012-12-01",
      "2018-12-01",
      "2024-10-01"
    )),
    label = c("Zedillo", "Fox", "Calderón", "Peña", "AMLO", "Sheinbaum")
  )
  
  trend <- approval_full %>%
    filter(!is.na(fecha), !is.na(Aprueba)) %>%
    group_by(Presidente, fecha) %>%
    summarise(
      mediana = median(Aprueba, na.rm = TRUE),
      q25     = quantile(Aprueba, 0.25, na.rm = TRUE),
      q75     = quantile(Aprueba, 0.75, na.rm = TRUE),
      n       = n(),
      .groups = "drop"
    ) %>%
    filter(n >= 2)
  
  p <- approval_full %>%
    filter(!is.na(fecha), !is.na(Aprueba)) %>%
    ggplot(aes(x = fecha, y = Aprueba)) +
    geom_vline(
      data        = transiciones,
      aes(xintercept = as.numeric(fecha)),
      linetype    = "dashed",
      color       = GRAY_MID,
      linewidth   = 0.4
    ) +
    geom_text(
      data        = transiciones,
      aes(x = fecha, label = label),
      y           = 97,
      hjust       = -0.1,
      vjust       = 1,
      size        = 3,
      color       = GRAY_MID,
      inherit.aes = FALSE
    ) +
    geom_ribbon(
      data        = trend,
      aes(x = fecha, ymin = q25, ymax = q75, fill = Presidente),
      inherit.aes = FALSE,
      alpha       = 0.18
    ) +
    geom_line(
      data        = trend,
      aes(x = fecha, y = mediana, color = Presidente),
      inherit.aes = FALSE,
      linewidth   = 1.1
    ) +
    geom_point(
      aes(color = Presidente),
      size        = 1.6,
      alpha       = 0.35
    ) +
    scale_color_manual(values = presidente_colors, labels = presidente_labels) +
    scale_fill_manual(values  = presidente_colors, labels = presidente_labels) +
    scale_y_continuous(
      limits = c(20, 100),
      breaks = seq(20, 100, 10),
      labels = function(x) paste0(x, "%")
    ) +
    scale_x_datetime(
      date_breaks  = "2 years",
      date_labels  = "%Y",
      minor_breaks = NULL,
      expand       = expansion(mult = 0.01)
    ) +
    labs(
      title    = "Aprobación presidencial · México 1995–2025",
      subtitle = "Cada punto = una encuesta · Línea = mediana mensual · Banda = rango intercuartílico (mín. 2 encuestas)",
      x        = NULL,
      y        = "Aprobación (%)",
      color    = "Presidente",
      fill     = "Presidente",
      caption  = "Fuente: Oraculus · Elaboración propia"
    ) +
    theme_minimal(base_size = 13) +
    theme(
      plot.title       = element_text(face = "bold", size = 15, color = TEXT_DARK),
      plot.subtitle    = element_text(size = 9, color = GRAY_MID),
      plot.caption     = element_text(size = 8, color = GRAY_MID),
      panel.grid.major = element_line(color = GRAY_LIGHT),
      panel.grid.minor = element_blank(),
      axis.text.x      = element_text(hjust = 0.5, color = TEXT_DARK),
      axis.text.y      = element_text(color = TEXT_DARK),
      legend.position  = "bottom",
      legend.title     = element_text(face = "bold")
    )
  
  print(p)
  
}


{
  
  house_effects <- approval_full %>%
    filter(!is.na(fecha), !is.na(Aprueba)) %>%
    group_by(fecha) %>%
    mutate(mediana_mes = median(Aprueba, na.rm = TRUE)) %>%
    ungroup() %>%
    mutate(desviacion = Aprueba - mediana_mes) %>%
    group_by(encuestadora_clean) %>%
    summarise(
      efecto_casa = mean(desviacion, na.rm = TRUE),
      sd_efecto   = sd(desviacion, na.rm = TRUE),
      n           = n(),
      .groups     = "drop"
    ) %>%
    filter(n >= 5) %>%
    mutate(
      encuestadora_clean = fct_reorder(encuestadora_clean, efecto_casa),
      direccion          = ifelse(efecto_casa >= 0, "Alto", "Bajo")
    )
  
  p2 <- house_effects %>%
    ggplot(aes(x = efecto_casa, y = encuestadora_clean, fill = direccion)) +
    geom_col(width = 0.65, alpha = 0.85) +
    geom_errorbarh(
      aes(xmin = efecto_casa - sd_efecto, xmax = efecto_casa + sd_efecto),
      height = 0.3, color = GRAY_MID, linewidth = 0.5
    ) +
    geom_vline(xintercept = 0, color = TEXT_DARK, linewidth = 0.6) +
    geom_text(
      aes(
        label = paste0(ifelse(efecto_casa > 0, "+", ""), round(efecto_casa, 1), "pp  (n=", n, ")"),
        hjust = ifelse(efecto_casa >= 0, -0.1, 1.1)
      ),
      size = 3, color = TEXT_DARK
    ) +
    scale_fill_manual(values = c("Alto" = "#C84B31", "Bajo" = "#1565C0")) +
    scale_x_continuous(
      labels = function(x) paste0(ifelse(x > 0, "+", ""), x, "pp"),
      expand = expansion(mult = 0.25)
    ) +
    labs(
      title    = "Efecto de casa por encuestadora",
      subtitle = "Desviación promedio respecto a la mediana mensual · Barras de error = ±1 SD · mín. 5 encuestas",
      x        = "Puntos porcentuales vs mediana del mes",
      y        = NULL,
      fill     = "Sesgo",
      caption  = "Fuente: Oraculus · Elaboración propia"
    ) +
    theme_minimal(base_size = 13) +
    theme(
      plot.title         = element_text(face = "bold", size = 15, color = TEXT_DARK),
      plot.subtitle      = element_text(size = 9, color = GRAY_MID),
      plot.caption       = element_text(size = 8, color = GRAY_MID),
      panel.grid.major.y = element_blank(),
      panel.grid.major.x = element_line(color = GRAY_LIGHT),
      panel.grid.minor   = element_blank(),
      legend.position    = "bottom"
    )
  
  print(p2)
  
}

{
  
  house_effects_sexenio <- approval_full %>%
    filter(!is.na(fecha), !is.na(Aprueba)) %>%
    group_by(Presidente, fecha) %>%
    mutate(mediana_mes = median(Aprueba, na.rm = TRUE)) %>%
    ungroup() %>%
    mutate(desviacion = Aprueba - mediana_mes) %>%
    group_by(encuestadora_clean, Presidente) %>%
    summarise(
      efecto_casa = mean(desviacion, na.rm = TRUE),
      n           = n(),
      .groups     = "drop"
    ) %>%
    filter(n >= 3) %>%
    mutate(
      Presidente = factor(Presidente, levels = c("EZPL", "VFQ", "FCH", "EPN", "AMLO", "Sheinbaum")),
      encuestadora_clean = fct_reorder(encuestadora_clean, efecto_casa, .fun = mean)
    )
  
  presidente_labels <- c(
    "EZPL"      = "Zedillo",
    "VFQ"       = "Fox",
    "FCH"       = "Calderón",
    "EPN"       = "Peña Nieto",
    "AMLO"      = "AMLO",
    "Sheinbaum" = "Sheinbaum"
  )
  
  presidente_colors_tile <- c(
    "EZPL"      = "#2E7D32",
    "VFQ"       = "#1565C0",
    "FCH"       = "#1E90FF",
    "EPN"       = "#C62828",
    "AMLO"      = "#8B0000",
    "Sheinbaum" = "#C84B31"
  )
  
  p3 <- house_effects_sexenio %>%
    ggplot(aes(x = Presidente, y = encuestadora_clean, fill = efecto_casa)) +
    geom_tile(color = "white", linewidth = 0.5) +
    geom_text(
      aes(
        label = paste0(ifelse(efecto_casa > 0, "+", ""), round(efecto_casa, 1)),
        color = abs(efecto_casa) > 4
      ),
      size = 3
    ) +
    scale_fill_gradient2(
      low      = "#1565C0",
      mid      = "white",
      high     = "#C84B31",
      midpoint = 0,
      limits   = c(-10, 10),
      name     = "Efecto de casa (pp)"
    ) +
    scale_color_manual(values = c("FALSE" = TEXT_DARK, "TRUE" = "white"), guide = "none") +
    scale_x_discrete(labels = presidente_labels) +
    labs(
      title    = "Efecto de casa por encuestadora y sexenio",
      subtitle = "Desviación promedio respecto a la mediana mensual · mín. 3 encuestas por celda\nAzul = subestima aprobación · Rojo = sobreestima aprobación",
      x        = NULL,
      y        = NULL,
      caption  = "Fuente: Oraculus · Elaboración propia"
    ) +
    theme_minimal(base_size = 13) +
    theme(
      plot.title       = element_text(face = "bold", size = 15, color = TEXT_DARK),
      plot.subtitle    = element_text(size = 9, color = GRAY_MID),
      plot.caption     = element_text(size = 8, color = GRAY_MID),
      panel.grid       = element_blank(),
      axis.text.x      = element_text(color = TEXT_DARK, face = "bold"),
      axis.text.y      = element_text(color = TEXT_DARK),
      legend.position  = "bottom",
      legend.key.width = unit(2, "cm")
    )
  
  print(p3)
  
}

#Free polls to update
# https://www.demotecnia.com.mx/evaluacion-de-gobierno-federal-abril-2026/
# https://www.demotecnia.com.mx/encuesta-nacional-junio-2026/
# https://www.elfinanciero.com.mx/nacional/2026/05/04/aprobacion-a-claudia-sheinbaum-68-por-ciento-en-abril-encuesta-ef/
