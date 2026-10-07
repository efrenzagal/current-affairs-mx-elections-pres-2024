# Municipal regression explorer ---------------------------------------------
#
# Pick an EIC 2025 variable (x) and a 2024 INE result (y); the app fits
# lm(y ~ x) over municipalities weighted by population (EIC POBTOT) and shows
# the full summary() plus a bubble chart. Same queries and join as
# municipal_regression.R; see its Joins section for how the key works.
#
# Run with:
# shiny::runApp("state_scorecards/analysis/municipal_regression_app.R")

library(tidyverse)
library(DBI)
library(RSQLite)
library(duckdb)
library(plotly)
library(shiny)


# Data ---------------------------------------------------------------------

repo_root <- "~/Documents/GitHub/current-affairs-mx-elections-pres-2024"
query_dir <- file.path(repo_root, "state_scorecards/analysis/queries")

con <- dbConnect(SQLite(), file.path(repo_root, "election_data.db"))
votes_long <- dbGetQuery(con, read_file(file.path(query_dir, "municipal_vote_share.sql")))
dbDisconnect(con)

con <- dbConnect(duckdb(), file.path(repo_root, "state_scorecards/data/eic2025.duckdb"),
                 read_only = TRUE)
eic <- dbGetQuery(con, read_file(file.path(query_dir, "municipal_eic_predictors.sql")))
dbDisconnect(con, shutdown = TRUE)

votes <- votes_long %>%
  select(cvegeo, election_type, pct_coalicion_morena, pct_bloque_pan, pct_mc,
         turnout) %>%
  pivot_wider(
    names_from  = election_type,
    names_glue  = "{.value}_{tolower(election_type)}_2024",
    values_from = c(pct_coalicion_morena, pct_bloque_pan, pct_mc, turnout)
  )

# NAs are dropped per regression, only for the chosen x and y
municipios <- eic %>%
  inner_join(votes, by = "cvegeo") %>%
  mutate(log_ingreso = log(ingreso_trabajo_hogar)) %>%
  filter(!is.na(poblacion))

x_choices <- c(
  "% hogares con ingresos de programas de gobierno" = "pct_hog_gob",
  "log ingreso por trabajo del hogar"               = "log_ingreso",
  "Ingreso por trabajo del hogar (pesos al mes)"    = "ingreso_trabajo_hogar",
  "% hogares sin acceso a alimentos"                = "pct_hog_sin_alim",
  "% hogares con jubilación o pensión"              = "pct_hog_jub",
  "% hogares con remesas"                           = "pct_hog_remesas",
  "Grado promedio de escolaridad"                   = "escolaridad",
  "% población indígena"                            = "pct_indigena",
  "% población sin afiliación a salud"              = "pct_sin_salud",
  "% viviendas con automóvil"                       = "pct_viv_auto",
  "% viviendas con internet"                        = "pct_viv_internet",
  "Edad mediana"                                    = "edad_mediana"
)

y_choices <- c(
  "% coalición MORENA, diputados"   = "pct_coalicion_morena_dip_2024",
  "% coalición MORENA, senadores"   = "pct_coalicion_morena_sen_2024",
  "% coalición MORENA, presidencia" = "pct_coalicion_morena_pre_2024",
  "% PAN-PRI-PRD, diputados"        = "pct_bloque_pan_dip_2024",
  "% PAN-PRI-PRD, senadores"        = "pct_bloque_pan_sen_2024",
  "% PAN-PRI-PRD, presidencia"      = "pct_bloque_pan_pre_2024",
  "% MC, diputados"                 = "pct_mc_dip_2024",
  "% MC, senadores"                 = "pct_mc_sen_2024",
  "% MC, presidencia"               = "pct_mc_pre_2024",
  "Participación, diputados"        = "turnout_dip_2024",
  "Participación, senadores"        = "turnout_sen_2024",
  "Participación, presidencia"      = "turnout_pre_2024"
)

label_of <- function(choices, value) names(choices)[choices == value]


# UI -----------------------------------------------------------------------

ui <- fluidPage(
  titlePanel("Regresión municipal: EIC 2025 vs. resultados INE 2024"),

  sidebarLayout(
    sidebarPanel(
      width = 3,
      selectInput("x", "Variable X (EIC 2025)", choices = x_choices,
                  selected = "log_ingreso"),
      selectInput("y", "Variable Y (INE 2024)", choices = y_choices,
                  selected = "pct_coalicion_morena_dip_2024"),
      selectizeInput("estados", "Estados", choices = sort(unique(municipios$state_name)),
                     multiple = TRUE,
                     options = list(placeholder = "Todos los estados")),
      numericInput("min_pob", "Población mínima del municipio",
                   value = 0, min = 0, step = 1000),
      helpText("Ponderado por población (EIC 2025, POBTOT)."),
      textOutput("n_municipios")
    ),

    mainPanel(
      width = 9,
      plotlyOutput("bubbles", height = "520px"),
      h4("summary(lm)"),
      verbatimTextOutput("summary")
    )
  )
)


# Server -------------------------------------------------------------------

server <- function(input, output, session) {

  # No state selected = all states
  model_data <- reactive({
    municipios %>%
      filter(length(input$estados) == 0 | state_name %in% input$estados) %>%
      filter(poblacion >= input$min_pob) %>%
      drop_na(all_of(c(input$x, input$y)))
  })

  # bquote puts the chosen formula in the model call, so summary() prints
  # "pct_coalicion_morena_dip_2024 ~ log_ingreso" instead of a variable name
  model <- reactive({
    d <- model_data()
    validate(need(nrow(d) >= 3, "Muy pocos municipios con ese filtro."))
    f <- reformulate(input$x, response = input$y)
    eval(bquote(lm(.(f), data = d, weights = poblacion)))
  })

  output$n_municipios <- renderText({
    paste0(scales::comma(nrow(model_data())), " municipios en el modelo")
  })

  output$summary <- renderPrint({
    summary(model())
  })

  output$bubbles <- renderPlotly({
    d <- model_data() %>%
      mutate(
        x = .data[[input$x]],
        y = .data[[input$y]],
        hover = paste0(
          "<b>", municipio, ", ", state_name, "</b>",
          "<br>", label_of(x_choices, input$x), ": ", round(x, 2),
          "<br>", label_of(y_choices, input$y), ": ", round(y, 1),
          "<br>Población: ", scales::comma(poblacion)
        )
      )
    validate(need(nrow(d) >= 3, "Muy pocos municipios con ese filtro."))

    # The line is the same weighted fit as the summary
    p <- ggplot(d, aes(x, y)) +
      geom_point(aes(size = poblacion, text = hover),
                 color = "steelblue", alpha = 0.4) +
      geom_smooth(aes(weight = poblacion), method = "lm", formula = y ~ x,
                  se = FALSE, color = "firebrick") +
      scale_size_area(max_size = 15, guide = "none") +
      labs(x = label_of(x_choices, input$x), y = label_of(y_choices, input$y))

    ggplotly(p, tooltip = "text")
  })
}

shinyApp(ui = ui, server = server)
