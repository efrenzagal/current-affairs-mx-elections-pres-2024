# PIBE explorer ------------------------------------------------------------
#
# Run from the repository root with:
# shiny::runApp("state_scorecards/analysis/pibe_dashboard.R")

library(tidyverse)
library(plotly)
library(shiny)
library(DBI)
library(RSQLite)


# Data ---------------------------------------------------------------------

find_file_upwards <- function(filename, start = getwd()) {
  current_dir <- normalizePath(start, winslash = "/", mustWork = TRUE)

  repeat {
    candidate <- file.path(current_dir, filename)

    if (file.exists(candidate)) {
      return(candidate)
    }

    parent_dir <- dirname(current_dir)

    if (identical(parent_dir, current_dir)) {
      stop("No se encontro ", filename, " desde ", start, call. = FALSE)
    }

    current_dir <- parent_dir
  }
}

db_path <- find_file_upwards("election_data.db")
geojson_path <- find_file_upwards(
  file.path("data", "materialized", "estados_processed.geojson")
)

state_geojson <- jsonlite::read_json(geojson_path, simplifyVector = FALSE)

con <- dbConnect(SQLite(), db_path)

pibe_data <- dbGetQuery(
  con,
  "SELECT
      geography,
      year,
      activity_id,
      activity_group,
      activity_level,
      activity_name,
      value
   FROM fact_pibe_state_annual
   WHERE unit = 'Millones de pesos a precios de 2018'
     AND concept_name = 'Valor agregado bruto'"
)

dbDisconnect(con)

pibe_data <- pibe_data %>%
  mutate(
    year = as.integer(year),
    value = as.numeric(value),
    broad_activity = recode(
      activity_group,
      "Actividades primarias" = "Primaria",
      "Actividades secundarias" = "Secundaria",
      "Actividades terciarias" = "Terciaria",
      .default = "Total"
    )
  )

national_name <- "Estados Unidos Mexicanos"

state_names <- pibe_data %>%
  filter(geography != national_name) %>%
  distinct(geography) %>%
  arrange(geography) %>%
  pull(geography)

default_state <- if ("Ciudad de México" %in% state_names) {
  "Ciudad de México"
} else {
  state_names[[1]]
}

activity_catalog <- pibe_data %>%
  distinct(activity_level, activity_id, activity_group, activity_name) %>%
  arrange(
    factor(
      activity_level,
      levels = c("total", "activity_group", "sector", "subsector")
    ),
    activity_group,
    activity_name
  )

detail_activity_ids <- activity_catalog %>%
  filter(activity_level %in% c("sector", "subsector")) %>%
  distinct(activity_id) %>%
  arrange(activity_id) %>%
  pull(activity_id)

# Golden-angle spacing keeps neighboring assignments far apart on the hue
# wheel. Colors remain stable when the user adds or removes activities.
detail_activity_colors <- setNames(
  grDevices::hcl(
    h = ((seq_along(detail_activity_ids) - 1) * 137.508) %% 360,
    c = 78,
    l = 52,
    fixup = TRUE
  ),
  detail_activity_ids
)

ranking_activity_choices <- list(
  "Economía y grandes actividades" = activity_catalog %>%
    filter(activity_level %in% c("total", "activity_group")) %>%
    {setNames(.$activity_id, .$activity_name)},
  "Sectores" = activity_catalog %>%
    filter(activity_level == "sector") %>%
    {setNames(.$activity_id, .$activity_name)},
  "Subsectores" = activity_catalog %>%
    filter(activity_level == "subsector") %>%
    {setNames(.$activity_id, .$activity_name)}
)

group_colors <- c(
  "Actividades primarias" = "#26865E",
  "Actividades secundarias" = "#3575B8",
  "Actividades terciarias" = "#E1782D"
)

group_name_from_label <- function(label) {
  unname(c(
    "Primaria" = "Actividades primarias",
    "Secundaria" = "Actividades secundarias",
    "Terciaria" = "Actividades terciarias"
  )[label])
}

map_state_name <- function(state_name) {
  recode(
    state_name,
    "Coahuila de Zaragoza" = "Coahuila",
    "Michoacán de Ocampo" = "Michoacán",
    "Veracruz de Ignacio de la Llave" = "Veracruz",
    .default = state_name
  )
}

geo_state_names <- purrr::map_chr(
  state_geojson$features,
  ~ .x$properties$name
)

unmatched_geo_states <- setdiff(map_state_name(state_names), geo_state_names)

if (length(unmatched_geo_states) > 0) {
  stop(
    "Entidades PIBE sin geometria: ",
    paste(unmatched_geo_states, collapse = ", "),
    call. = FALSE
  )
}

geo_to_pibe_name <- setNames(state_names, map_state_name(state_names))


# Reusable UI helpers ------------------------------------------------------

filter_card <- function(...) {
  div(class = "filter-card", ...)
}

metric_card <- function(label, value, note = NULL) {
  div(
    class = "metric-card",
    div(class = "metric-label", label),
    div(class = "metric-value", value),
    if (!is.null(note)) div(class = "metric-note", note)
  )
}


# UI -----------------------------------------------------------------------

ui <- fluidPage(
  tags$head(
    tags$style(HTML("
      :root {
        --ink: #1f2933;
        --muted: #66727f;
        --line: #dfe5ea;
        --panel: #f7f9fa;
        --accent: #176b87;
      }

      body {
        color: var(--ink);
        background: #ffffff;
      }

      .container-fluid {
        max-width: 1500px;
        padding: 20px 32px 40px 32px;
      }

      .app-header {
        margin-bottom: 14px;
      }

      .app-title {
        margin: 0;
        font-size: 30px;
        font-weight: 650;
        letter-spacing: -0.5px;
      }

      .app-subtitle {
        margin: 5px 0 0 0;
        color: var(--muted);
      }

      .nav-pills {
        margin-bottom: 16px;
      }

      .nav-pills > li > a {
        color: var(--accent);
      }

      .nav-pills > li.active > a,
      .nav-pills > li.active > a:hover,
      .nav-pills > li.active > a:focus {
        background: var(--accent);
      }

      .filter-card {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 13px 16px 4px 16px;
        margin-bottom: 16px;
      }

      .filter-card .form-group {
        margin-bottom: 10px;
      }

      .state-button {
        width: 100%;
        margin-top: 25px;
      }

      .activity-actions {
        display: flex;
        gap: 16px;
        margin: -5px 0 8px 0;
        font-size: 12px;
      }

      .control-label {
        color: #3c4650;
        font-size: 13px;
        font-weight: 600;
      }

      .metric-row {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 8px;
      }

      .metric-card {
        min-height: 92px;
        padding: 14px 16px;
        border: 1px solid var(--line);
        border-radius: 10px;
        background: #fff;
      }

      .metric-label {
        color: var(--muted);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: .5px;
      }

      .metric-value {
        margin-top: 3px;
        font-size: 25px;
        font-weight: 650;
      }

      .metric-note {
        color: var(--muted);
        font-size: 12px;
      }

      .chart-section {
        margin-top: 20px;
        padding-top: 4px;
        border-top: 1px solid var(--line);
      }

      .chart-heading {
        margin: 15px 0 2px 0;
        font-size: 21px;
        font-weight: 600;
      }

      .chart-description {
        margin-bottom: 7px;
        color: var(--muted);
        font-size: 13px;
      }

      .data-note {
        margin-top: 14px;
        color: var(--muted);
        font-size: 12px;
      }

      @media (max-width: 800px) {
        .container-fluid {
          padding: 15px;
        }

        .metric-row {
          grid-template-columns: 1fr;
        }
      }
    "))
  ),

  div(
    class = "app-header",
    h1("Explorador del PIBE", class = "app-title"),
    p(
      "Ranking estatal y estructura económica a precios constantes de 2018",
      class = "app-subtitle"
    )
  ),

  tabsetPanel(
    id = "main_tabs",
    type = "pills",

    tabPanel(
      title = "Vista nacional",
      value = "national_view",

      filter_card(
        fluidRow(
          column(
            width = 5,
            selectizeInput(
              inputId = "rank_activity",
              label = "Actividad para el ranking",
              choices = ranking_activity_choices,
              selected = "TOTAL",
              width = "100%",
              options = list(placeholder = "Busque una actividad")
            )
          ),
          column(
            width = 3,
            selectInput(
              inputId = "rank_state",
              label = "Entidad destacada",
              choices = state_names,
              selected = default_state,
              width = "100%"
            )
          ),
          column(
            width = 2,
            selectInput(
              inputId = "rank_year",
              label = "Año",
              choices = max(pibe_data$year, na.rm = TRUE),
              selected = max(pibe_data$year, na.rm = TRUE),
              width = "100%"
            )
          ),
          column(
            width = 2,
            actionButton(
              inputId = "open_state_view",
              label = "Ver serie estatal",
              icon = icon("arrow-right"),
              class = "btn-primary state-button"
            )
          )
        )
      ),

      uiOutput("national_metrics"),
      fluidRow(
        column(
          width = 6,
          plotlyOutput("state_ranking", height = "760px")
        ),
        column(
          width = 6,
          plotlyOutput("state_map", height = "760px")
        )
      ),
      p(
        "También puede hacer clic en una barra o en el mapa para cambiar la entidad destacada sin salir del ranking.",
        class = "data-note"
      )
    ),

    tabPanel(
      title = "Vista estatal",
      value = "state_view",

      filter_card(
        fluidRow(
          column(
            width = 5,
            selectInput(
              inputId = "state",
              label = "Entidad federativa",
              choices = state_names,
              selected = default_state,
              width = "100%"
            )
          ),
          column(
            width = 7,
            sliderInput(
              inputId = "state_years",
              label = "Periodo",
              min = min(pibe_data$year, na.rm = TRUE),
              max = max(pibe_data$year, na.rm = TRUE),
              value = range(pibe_data$year, na.rm = TRUE),
              step = 1,
              sep = "",
              width = "100%"
            )
          )
        )
      ),

      div(
        class = "chart-section",
        h2("Estructura económica", class = "chart-heading"),
        p(
          "Primaria, secundaria y terciaria se muestran juntas, sin incluir sus componentes.",
          class = "chart-description"
        ),
        radioButtons(
          inputId = "broad_metric",
          label = NULL,
          choices = c(
            "Participación del VAB" = "share",
            "Valor real" = "value"
          ),
          selected = "share",
          inline = TRUE
        ),
        plotlyOutput("broad_timeseries", height = "470px")
      ),

      div(
        class = "chart-section",
        h2("Detalle por actividad", class = "chart-heading"),
        p(
          "Solo se comparan componentes del mismo nivel; el agregado queda fuera de esta gráfica.",
          class = "chart-description"
        ),

        filter_card(
          fluidRow(
            column(
              width = 3,
              selectInput(
                inputId = "detail_group",
                label = "Grupo",
                choices = c("Primaria", "Secundaria", "Terciaria"),
                selected = "Terciaria",
                width = "100%"
              )
            ),
            column(
              width = 2,
              selectInput(
                inputId = "detail_level",
                label = "Nivel",
                choices = c("Sector" = "sector"),
                selected = "sector",
                width = "100%"
              )
            ),
            column(
              width = 5,
              selectizeInput(
                inputId = "detail_activities",
                label = "Actividades",
                choices = NULL,
                multiple = TRUE,
                width = "100%",
                options = list(
                  plugins = list("remove_button"),
                  placeholder = "Seleccione actividades"
                )
              ),
              div(
                class = "activity-actions",
                actionLink("select_all_activities", "Seleccionar todas"),
                actionLink("clear_activities", "Limpiar")
              )
            ),
            column(
              width = 2,
              selectInput(
                inputId = "detail_metric",
                label = "Escala",
                choices = c(
                  "Índice" = "index",
                  "Valor real" = "value"
                ),
                selected = "index",
                width = "100%"
              )
            )
          )
        ),

        plotlyOutput("detail_timeseries", height = "560px")
      ),

      p(
        "Fuente: INEGI, PIBE. Valores monetarios en millones de pesos a precios de 2018.",
        class = "data-note"
      )
    )
  )
)


# Server -------------------------------------------------------------------

server <- function(input, output, session) {

  # National ranking -------------------------------------------------------

  observeEvent(input$rank_activity, {
    available_years <- pibe_data %>%
      filter(
        geography != national_name,
        activity_id == input$rank_activity,
        !is.na(value)
      ) %>%
      distinct(year) %>%
      arrange(desc(year)) %>%
      pull(year)

    req(length(available_years) > 0)

    current_year <- isolate(as.integer(input$rank_year))
    selected_year <- if (current_year %in% available_years) {
      current_year
    } else {
      max(available_years)
    }

    updateSelectInput(
      session,
      "rank_year",
      choices = available_years,
      selected = selected_year
    )
  }, ignoreInit = FALSE)

  ranking_data <- reactive({
    req(input$rank_activity, input$rank_year, input$rank_state)

    selected_year <- as.integer(input$rank_year)

    data <- pibe_data %>%
      filter(
        geography != national_name,
        activity_id == input$rank_activity,
        year == selected_year,
        !is.na(value)
      ) %>%
      group_by(geography) %>%
      summarise(value = sum(value), .groups = "drop") %>%
      arrange(desc(value)) %>%
      mutate(
        rank = row_number(),
        national_share = value / sum(value),
        is_selected = geography == input$rank_state,
        geo_name = map_state_name(geography)
      )

    data
  })

  output$national_metrics <- renderUI({
    data <- ranking_data()
    req(nrow(data) > 0)

    selected <- data %>% filter(geography == input$rank_state)
    activity_name <- activity_catalog %>%
      filter(activity_id == input$rank_activity) %>%
      slice(1) %>%
      pull(activity_name)

    if (nrow(selected) == 0) {
      return(NULL)
    }

    div(
      class = "metric-row",
      metric_card(
        paste("Posición de", input$rank_state),
        paste0("#", selected$rank),
        paste("de", nrow(data), "entidades")
      ),
      metric_card(
        "Valor",
        scales::label_number(
          scale_cut = scales::cut_short_scale(),
          accuracy = 0.1
        )(selected$value),
        "millones de pesos de 2018"
      ),
      metric_card(
        "Participación nacional",
        scales::percent(selected$national_share, accuracy = 0.1),
        activity_name
      )
    )
  })

  output$state_ranking <- renderPlotly({
    data <- ranking_data()

    validate(need(nrow(data) > 0, "No hay datos para esta selección."))

    activity_name <- activity_catalog %>%
      filter(activity_id == input$rank_activity) %>%
      slice(1) %>%
      pull(activity_name)

    graph <- data %>%
      mutate(
        geography_plot = forcats::fct_reorder(geography, value),
        highlight = if_else(is_selected, "Entidad seleccionada", "Otras entidades"),
        hover = paste0(
          "<b>", geography, "</b>",
          "<br>Posición: #", rank,
          "<br>Valor: ", scales::comma(value, accuracy = 1),
          " millones",
          "<br>Participación: ", scales::percent(national_share, accuracy = 0.1)
        )
      ) %>%
      ggplot(
        aes(
          x = geography_plot,
          y = value,
          fill = highlight,
          text = hover,
          key = geography
        )
      ) +
      geom_col(width = 0.72) +
      coord_flip() +
      scale_fill_manual(
        values = c(
          "Entidad seleccionada" = "#E1782D",
          "Otras entidades" = "#4F91A8"
        )
      ) +
      scale_y_continuous(
        labels = scales::label_number(scale_cut = scales::cut_short_scale()),
        expand = expansion(mult = c(0, 0.05))
      ) +
      labs(
        title = activity_name,
        subtitle = paste("Ranking estatal en", input$rank_year),
        x = NULL,
        y = "Millones de pesos de 2018",
        fill = NULL
      ) +
      theme_minimal(base_size = 13) +
      theme(
        plot.title = element_text(face = "bold", size = 19),
        panel.grid.major.y = element_blank(),
        panel.grid.minor = element_blank(),
        legend.position = "top"
      )

    ggplotly(graph, tooltip = "text", source = "national_rank") %>%
      layout(
        dragmode = FALSE,
        margin = list(l = 190, r = 30, t = 85, b = 65)
      ) %>%
      config(displayModeBar = FALSE)
  })

  output$state_map <- renderPlotly({
    data <- ranking_data() %>%
      mutate(
        hover = paste0(
          "<b>", geography, "</b>",
          "<br>Posición: #", rank,
          "<br>Valor: ", scales::comma(value, accuracy = 1), " millones",
          "<br>Participación: ", scales::percent(national_share, accuracy = 0.1)
        )
      )

    validate(
      need(nrow(data) == 32, "No se pudieron vincular las 32 entidades al mapa.")
    )

    selected <- data %>%
      filter(is_selected) %>%
      mutate(selection_value = 1)

    map <- plot_ly(source = "national_map") %>%
      add_trace(
        data = data,
        type = "choropleth",
        geojson = state_geojson,
        featureidkey = "properties.name",
        locations = ~geo_name,
        z = ~value,
        zmin = min(data$value),
        zmax = max(data$value),
        colorscale = list(
          list(0, "#EEF6F8"),
          list(0.5, "#65A7B9"),
          list(1, "#0B4F6C")
        ),
        colorbar = list(
          title = "Millones<br>de pesos",
          thickness = 12,
          len = 0.55,
          tickformat = ".3s"
        ),
        marker = list(
          line = list(color = "#FFFFFF", width = 0.8)
        ),
        text = ~hover,
        hoverinfo = "text",
        showlegend = FALSE,
        inherit = FALSE
      )

    if (nrow(selected) == 1) {
      map <- map %>%
        add_trace(
          data = selected,
          type = "choropleth",
          geojson = state_geojson,
          featureidkey = "properties.name",
          locations = ~geo_name,
          z = ~selection_value,
          colorscale = list(
            list(0, "rgba(225, 120, 45, 0.34)"),
            list(1, "rgba(225, 120, 45, 0.34)")
          ),
          marker = list(
            line = list(color = "#E1782D", width = 3.5)
          ),
          text = ~hover,
          hoverinfo = "text",
          showscale = FALSE,
          showlegend = FALSE,
          inherit = FALSE
        )
    }

    map %>%
      layout(
        title = list(
          text = paste0(
            "Distribución territorial<br><sup>",
            input$rank_year,
            " · colores más oscuros indican mayor valor</sup>"
          ),
          x = 0.02,
          xanchor = "left"
        ),
        geo = list(
          fitbounds = "locations",
          visible = FALSE,
          bgcolor = "rgba(0,0,0,0)"
        ),
        margin = list(l = 5, r = 15, t = 90, b = 15),
        paper_bgcolor = "rgba(0,0,0,0)"
      ) %>%
      config(displayModeBar = FALSE)
  })

  observeEvent(event_data("plotly_click", source = "national_rank"), {
    click <- event_data("plotly_click", source = "national_rank")
    req(click$key, click$key %in% state_names)

    updateSelectInput(session, "rank_state", selected = click$key)
  })

  observeEvent(event_data("plotly_click", source = "national_map"), {
    click <- event_data("plotly_click", source = "national_map")
    req(click$location)

    selected_state <- unname(geo_to_pibe_name[click$location])
    req(length(selected_state) == 1, !is.na(selected_state))

    updateSelectInput(session, "rank_state", selected = selected_state)
  })

  observeEvent(input$rank_state, {
    req(input$rank_state, input$rank_state %in% state_names)

    if (!identical(input$state, input$rank_state)) {
      updateSelectInput(session, "state", selected = input$rank_state)
    }
  }, ignoreInit = TRUE)

  observeEvent(input$state, {
    req(input$state, input$state %in% state_names)

    if (!identical(input$rank_state, input$state)) {
      updateSelectInput(session, "rank_state", selected = input$state)
    }
  }, ignoreInit = TRUE)

  observeEvent(input$open_state_view, {
    req(input$rank_state, input$rank_state %in% state_names)

    updateSelectInput(session, "state", selected = input$rank_state)
    updateTabsetPanel(session, "main_tabs", selected = "state_view")
  })


  # State: broad economic structure ---------------------------------------

  broad_data <- reactive({
    req(input$state, input$state_years)

    data <- pibe_data %>%
      filter(
        geography == input$state,
        activity_level == "activity_group",
        between(year, input$state_years[1], input$state_years[2]),
        !is.na(value)
      ) %>%
      mutate(
        activity_group = factor(
          activity_group,
          levels = c(
            "Actividades primarias",
            "Actividades secundarias",
            "Actividades terciarias"
          )
        )
      )

    if (identical(input$broad_metric, "share")) {
      data <- data %>%
        group_by(year) %>%
        mutate(plot_value = 100 * value / sum(value)) %>%
        ungroup()
    } else {
      data <- data %>% mutate(plot_value = value)
    }

    data
  })

  output$broad_timeseries <- renderPlotly({
    data <- broad_data() %>%
      mutate(
        hover_share = paste0(
          "<b>", activity_name, "</b>",
          "<br>Año: ", year,
          "<br>Participación: ", scales::number(plot_value, accuracy = 0.1), "%",
          "<br>Valor: ", scales::comma(value, accuracy = 1), " millones"
        ),
        hover_value = paste0(
          "<b>", activity_name, "</b>",
          "<br>Año: ", year,
          "<br>Valor: ", scales::comma(value, accuracy = 1), " millones"
        )
      )

    validate(need(nrow(data) > 0, "No hay datos para este periodo."))

    if (identical(input$broad_metric, "share")) {
      graph <- ggplot(
        data,
        aes(
          x = year,
          y = plot_value,
          fill = activity_group,
          group = activity_group,
          text = hover_share
        )
      ) +
        geom_area(position = "stack", alpha = 0.92, color = "white", linewidth = 0.25) +
        scale_y_continuous(
          labels = function(x) paste0(x, "%"),
          limits = c(0, 100),
          expand = c(0, 0)
        ) +
        labs(
          x = NULL,
          y = "Participación",
          fill = NULL
        )
    } else {
      graph <- ggplot(
        data,
        aes(
          x = year,
          y = plot_value,
          color = activity_group,
          group = activity_group,
          text = hover_value
        )
      ) +
        geom_line(linewidth = 1.05) +
        scale_y_continuous(
          labels = scales::label_number(scale_cut = scales::cut_short_scale()),
          expand = expansion(mult = c(0.02, 0.08))
        ) +
        labs(
          x = NULL,
          y = "Millones de pesos de 2018",
          color = NULL
        )
    }

    if (identical(input$broad_metric, "share")) {
      graph <- graph + scale_fill_manual(values = group_colors, drop = FALSE)
    } else {
      graph <- graph + scale_color_manual(values = group_colors, drop = FALSE)
    }

    graph <- graph +
      theme_minimal(base_size = 13) +
      theme(
        legend.position = "top",
        panel.grid.minor = element_blank()
      )

    ggplotly(graph, tooltip = "text") %>%
      layout(
        hovermode = "x unified",
        legend = list(orientation = "h", x = 0, y = 1.06),
        margin = list(t = 65, r = 30, b = 60, l = 85)
      ) %>%
      config(displayModeBar = FALSE)
  })


  # State: sectors and subsectors ------------------------------------------

  observeEvent(input$detail_group, {
    group_name <- group_name_from_label(input$detail_group)

    levels_available <- pibe_data %>%
      filter(
        activity_group == group_name,
        activity_level %in% c("sector", "subsector")
      ) %>%
      distinct(activity_level) %>%
      pull(activity_level)

    labels <- c("sector" = "Sector", "subsector" = "Subsector")
    choices <- setNames(levels_available, labels[levels_available])
    selected <- if ("sector" %in% levels_available) "sector" else levels_available[[1]]

    updateSelectInput(
      session,
      "detail_level",
      choices = choices,
      selected = selected
    )
  }, ignoreInit = FALSE)

  detail_catalog <- reactive({
    req(input$state, input$detail_group, input$detail_level)

    group_name <- group_name_from_label(input$detail_group)

    pibe_data %>%
      filter(
        geography == input$state,
        activity_group == group_name,
        activity_level == input$detail_level
      ) %>%
      distinct(activity_id, activity_name) %>%
      arrange(activity_name)
  })

  observeEvent(detail_catalog(), {
    catalog <- detail_catalog()
    req(nrow(catalog) > 0)

    group_name <- group_name_from_label(input$detail_group)
    latest_year <- pibe_data %>%
      filter(
        geography == input$state,
        activity_group == group_name,
        activity_level == input$detail_level,
        !is.na(value)
      ) %>%
      summarise(year = max(year)) %>%
      pull(year)

    default_ids <- pibe_data %>%
      filter(
        geography == input$state,
        activity_group == group_name,
        activity_level == input$detail_level,
        year == latest_year,
        !is.na(value)
      ) %>%
      arrange(desc(value)) %>%
      slice_head(n = 5) %>%
      pull(activity_id)

    choices <- setNames(catalog$activity_id, catalog$activity_name)

    updateSelectizeInput(
      session,
      "detail_activities",
      choices = choices,
      selected = default_ids,
      server = TRUE
    )
  }, ignoreInit = FALSE)

  observeEvent(input$select_all_activities, {
    catalog <- detail_catalog()
    req(nrow(catalog) > 0)

    updateSelectizeInput(
      session,
      "detail_activities",
      choices = setNames(catalog$activity_id, catalog$activity_name),
      selected = catalog$activity_id,
      server = TRUE
    )
  })

  observeEvent(input$clear_activities, {
    catalog <- detail_catalog()

    updateSelectizeInput(
      session,
      "detail_activities",
      choices = setNames(catalog$activity_id, catalog$activity_name),
      selected = character(0),
      server = TRUE
    )
  })

  detail_data <- reactive({
    req(length(input$detail_activities) > 0, input$state_years)

    data <- pibe_data %>%
      filter(
        geography == input$state,
        activity_id %in% input$detail_activities,
        between(year, input$state_years[1], input$state_years[2]),
        !is.na(value)
      ) %>%
      arrange(activity_id, year)

    if (identical(input$detail_metric, "index")) {
      data <- data %>%
        group_by(activity_id) %>%
        mutate(plot_value = 100 * value / first(value)) %>%
        ungroup()
    } else {
      data <- data %>% mutate(plot_value = value)
    }

    data
  })

  output$detail_timeseries <- renderPlotly({
    data <- detail_data()

    validate(
      need(nrow(data) > 0, "Seleccione al menos una actividad con datos en el periodo.")
    )

    is_index <- identical(input$detail_metric, "index")

    data <- data %>%
      mutate(
        legend_name = stringr::str_replace_all(
          stringr::str_wrap(activity_name, width = 34),
          "\\n",
          "<br>"
        ),
        hover = paste0(
          "<b>", activity_name, "</b>",
          "<br>Año: ", year,
          "<br>Valor: ", scales::comma(value, accuracy = 1), " millones",
          if (is_index) {
            paste0("<br>Índice: ", scales::number(plot_value, accuracy = 0.1))
          } else {
            ""
          }
        )
      )

    legend_entries <- data %>%
      distinct(activity_id, legend_name)

    selected_activity_colors <- setNames(
      detail_activity_colors[legend_entries$activity_id],
      legend_entries$legend_name
    )

    graph <- ggplot(
        data,
        aes(
          x = year,
          y = plot_value,
          color = legend_name,
          group = activity_id,
          text = hover
        )
      ) +
      geom_line(linewidth = 1.05) +
      scale_color_manual(
        values = selected_activity_colors,
        breaks = legend_entries$legend_name,
        labels = legend_entries$legend_name,
        drop = TRUE
      ) +
      scale_y_continuous(
        labels = if (is_index) {
          scales::label_number(accuracy = 1)
        } else {
          scales::label_number(scale_cut = scales::cut_short_scale())
        },
        expand = expansion(mult = c(0.03, 0.08))
      ) +
      labs(
        title = paste(input$detail_group, "-", input$state),
        subtitle = if (is_index) {
          paste0("Evolución relativa (", min(data$year), " = 100 para cada actividad)")
        } else {
          "Valor agregado bruto por actividad"
        },
        x = NULL,
        y = if (is_index) "Índice" else "Millones de pesos de 2018",
        color = NULL
      ) +
      theme_minimal(base_size = 13) +
      theme(
        plot.title = element_text(face = "bold", size = 19),
        legend.position = "right",
        panel.grid.minor = element_blank()
      )

    ggplotly(graph, tooltip = "text") %>%
      layout(
        hovermode = "x unified",
        legend = list(orientation = "v", x = 1.02, y = 1),
        margin = list(t = 75, r = 330, b = 60, l = 85)
      ) %>%
      config(displayModeBar = FALSE)
  })
}


shinyApp(ui = ui, server = server)
