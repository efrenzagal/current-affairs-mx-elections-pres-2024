# Libraries
{
  library(tidyverse)
  library(ggplot2)
  library(plotly)
  library(shiny)
  library(DBI)
  library(RSQLite)
}

# Read data
{
  setwd('~/Documents/GitHub/current-affairs-mx-elections-pres-2024/')
  con <- dbConnect(SQLite(), "election_data.db")
  
  pibe_data <- dbGetQuery(con, "SELECT
    geography,
    year,

    CASE
        WHEN activity_group = 'Actividades primarias' THEN 'Primaria'
        WHEN activity_group = 'Actividades secundarias' THEN 'Secundaria'
        WHEN activity_group = 'Actividades terciarias' THEN 'Terciaria'
        ELSE 'Total'
    END AS broad_activity,

    activity_id,
    activity_level,
    activity_name,
    concept_name,
    value

FROM fact_pibe_state_annual

WHERE unit = 'Millones de pesos a precios de 2018';")

  dbDisconnect(con)
}

# What is the activity that usually provides more money to the country?
{
  top_sectors_by_year <- pibe_data %>%
    filter(
      #geography == "Estados Unidos Mexicanos",
      concept_name == "Valor agregado bruto",
      activity_level == "sector"
    ) %>%
    group_by(geography, year) %>%
    slice_max(
      order_by = value,
      n = 3,
      with_ties = FALSE
    ) %>%
    ungroup()
}

# Shiny logic
{
  # The user should be able to select this:
  concept_name_input <- "Valor agregado bruto"
  geography_input <- "Estados Unidos Mexicanos"
  activity_name_input <- "Comercio al por mayor"
  #to help the user filter, there should be another one for broad_activity and select a range of years, 
  #ideally it would select all the ones in the same broad_activity
  
  # output: time series
  ggplotly(pibe_data %>%
    filter(
      geography == geography_input,
      concept_name == concept_name_input,
      activity_name == activity_name_input
    ) %>%
    ggplot(aes(x = year, value)) +
    geom_line())
}


# Actual shinyapp:
{
  # Prepare variables used by the app
  pibe_data <- pibe_data %>%
    mutate(
      year = as.integer(year),
      value = as.numeric(value)
    )
  
  year_limits <- range(pibe_data$year, na.rm = TRUE)
  
  # UI ----------------------------------------------------------------------
  
  ui <- fluidPage(
    titlePanel("PIBE por entidad federativa"),
    
    sidebarLayout(
      sidebarPanel(
        selectInput(
          inputId = "geography",
          label = "Geografía",
          choices = sort(unique(pibe_data$geography)),
          selected = "Estados Unidos Mexicanos"
        ),
        
        selectInput(
          inputId = "concept_name",
          label = "Concepto",
          choices = sort(unique(pibe_data$concept_name)),
          selected = "Valor agregado bruto"
        ),
        
        selectInput(
          inputId = "broad_activity",
          label = "Grupo de actividad",
          choices = NULL
        ),
        
        selectizeInput(
          inputId = "activity_name",
          label = "Actividades",
          choices = NULL,
          multiple = TRUE,
          options = list(
            plugins = list("remove_button"),
            placeholder = "Seleccione una o más actividades"
          )
        ),
        
        sliderInput(
          inputId = "year_range",
          label = "Rango de años",
          min = year_limits[1],
          max = year_limits[2],
          value = year_limits,
          step = 1,
          sep = ""
        )
      ),
      
      mainPanel(
        plotlyOutput(
          outputId = "pibe_time_series",
          height = "650px"
        )
      )
    )
  )
  
  # Server ------------------------------------------------------------------
  
  server <- function(input, output, session) {
    
    # Broad activities available for the selected geography and concept
    available_broad_activities <- reactive({
      req(input$geography, input$concept_name)
      
      pibe_data %>%
        filter(
          geography == input$geography,
          concept_name == input$concept_name
        ) %>%
        distinct(broad_activity) %>%
        arrange(broad_activity) %>%
        pull(broad_activity)
    })
    
    observeEvent(
      available_broad_activities(),
      {
        choices <- available_broad_activities()
        
        selected <- if ("Terciaria" %in% choices) {
          "Terciaria"
        } else {
          choices[1]
        }
        
        updateSelectInput(
          session = session,
          inputId = "broad_activity",
          choices = choices,
          selected = selected
        )
      },
      ignoreInit = FALSE
    )
    
    # Activities available inside the selected broad activity
    available_activities <- reactive({
      req(
        input$geography,
        input$concept_name,
        input$broad_activity
      )
      
      pibe_data %>%
        filter(
          geography == input$geography,
          concept_name == input$concept_name,
          broad_activity == input$broad_activity
        ) %>%
        distinct(activity_name) %>%
        arrange(activity_name) %>%
        pull(activity_name)
    })
    
    # Select all activities belonging to the broad activity
    observeEvent(
      available_activities(),
      {
        choices <- available_activities()
        
        updateSelectizeInput(
          session = session,
          inputId = "activity_name",
          choices = choices,
          selected = choices,
          server = TRUE
        )
      },
      ignoreInit = FALSE
    )
    
    # Data displayed in the graph
    plot_data <- reactive({
      req(length(input$activity_name) > 0)
      
      pibe_data %>%
        filter(
          geography == input$geography,
          concept_name == input$concept_name,
          broad_activity == input$broad_activity,
          activity_name %in% input$activity_name,
          between(year, input$year_range[1], input$year_range[2])
        ) %>%
        arrange(activity_name, year)
    })
    
    output$pibe_time_series <- renderPlotly({
      data <- plot_data()
      
      validate(
        need(nrow(data) > 0, "No hay datos para esta selección.")
      )
      
      graph <- ggplot(
        data,
        aes(
          x = year,
          y = value,
          color = activity_name,
          group = activity_name,
          text = paste0(
            "Actividad: ", activity_name,
            "<br>Año: ", year,
            "<br>Valor: ", scales::comma(value),
            " millones de pesos"
          )
        )
      ) +
        geom_line(linewidth = 0.9) +
        geom_point(size = 1.5) +
        scale_x_continuous(
          breaks = scales::pretty_breaks()
        ) +
        scale_y_continuous(
          labels = scales::label_number(
            big.mark = ",",
            accuracy = 1
          )
        ) +
        labs(
          title = paste(input$broad_activity, "—", input$geography),
          subtitle = input$concept_name,
          x = NULL,
          y = "Millones de pesos a precios de 2018",
          color = "Actividad"
        ) +
        theme_minimal(base_size = 13) +
        theme(
          legend.position = "bottom",
          panel.grid.minor = element_blank()
        )
      
      ggplotly(graph, tooltip = "text") %>%
        layout(
          hovermode = "x unified",
          legend = list(
            orientation = "h",
            x = 0,
            y = -0.2
          )
        )
    })
  }
  
  shinyApp(ui = ui, server = server)
}