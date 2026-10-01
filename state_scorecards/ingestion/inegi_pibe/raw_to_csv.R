#INEGI data explorer
# Libraries
{
  library(stringr)
  library(tidyverse)
}

setwd("Documents/GitHub/current-affairs-mx-elections-pres-2024/data/raw_inegi")

# Read data
{
 # Detailed PIBE: one file per economic activity
  pibe_detallada_dir <- file.path(
    "conjunto_de_datos_pibe_csv",
    "conjunto_de_datos"
  )
  
  pibe_detallada_files <- list.files(
    pibe_detallada_dir,
    pattern = ".csv",
    full.names = TRUE
  )
  
  pibe_detallada_list <- setNames(
    lapply(
      pibe_detallada_files,
      \(file) read_csv(file, show_col_types = FALSE)
    ),
    basename(pibe_detallada_files)
  )
  
  
  # Retropolated PIBE: one file per economic activity
  pibe_retro_dir <- file.path(
    "conjunto_de_datos_piber_csv",
    "conjunto_de_datos"
  )
  
  pibe_retro_files <- list.files(
    pibe_retro_dir,
    pattern = ".csv",
    full.names = TRUE
  )
  
  pibe_retro_list <- setNames(
    lapply(
      pibe_retro_files,
      \(file) read_csv(file, show_col_types = FALSE)
    ),
    basename(pibe_retro_files)
  )
  
}

# Inspect pibe detallada 
{
  indice <- pibe_detallada_list$indice.csv
  
  #Example actividad
  View(pibe_detallada_list$conjunto_de_datos_pibe_actividad_21np2024_r.csv)
  
  #Example per state
  View(pibe_detallada_list$conjunto_de_datos_pibe_entidad_ver2024_r.csv)
}

# Clean data
{
  # Tidy PIBE activity files
  tidy_pibe_activity_files <- function(
    data_list,
    data_dir,
    dataset_name
  ) {
    
    broad_activity_groups <- c(
      "Actividades primarias",
      "Actividades secundarias",
      "Actividades terciarias"
    )
    
    # indice.csv does not contain a header
    index <- read_csv(
      file.path(data_dir, "indice.csv"),
      col_names = c("source_file", "series_path"),
      show_col_types = FALSE
    ) %>%
      filter(
        str_detect(
          source_file,
          "_actividad_.*\\.csv$"
        )
      )
    
    # Select only activity files
    activity_file_names <- names(data_list)[
      str_detect(
        names(data_list),
        "_actividad_.*\\.csv$"
      )
    ]
    
    long_data <- data_list[activity_file_names] %>%
      
      # Combine files and retain their filenames
      imap_dfr(
        function(data, source_file) {
          data %>%
            mutate(
              source_file = source_file,
              .before = 1
            )
        }
      ) %>%
      
      # Attach the hierarchy from indice.csv
      left_join(
        index,
        by = "source_file",
        relationship = "many-to-one"
      ) %>%
      
      # Preserve series_path and split a temporary copy
      mutate(
        series_path_split = series_path
      ) %>%
      
      separate_wider_delim(
        series_path_split,
        delim = "|",
        names = c(
          "series_name",
          "path_level_1",
          "path_level_2",
          "path_level_3"
        ),
        too_few = "align_start",
        too_many = "error"
      ) %>%
      
      # Normalize the activity hierarchy
      mutate(
        across(
          c(
            series_name,
            path_level_1,
            path_level_2,
            path_level_3
          ),
          ~ str_squish(.x)
        ),
        
        # Accounting concepts such as VAB and taxes belong
        # to the total economy rather than an activity group
        activity_group = case_when(
          path_level_1 %in% broad_activity_groups ~ path_level_1,
          TRUE ~ "Total de la economía"
        ),
        
        # "Actividades secundarias | Total" is the aggregate
        # for secondary activities, not an economic sector
        sector_raw = case_when(
          path_level_1 %in% broad_activity_groups &
            !is.na(path_level_2) &
            path_level_2 != "Total" ~ path_level_2,
          TRUE ~ NA_character_
        ),
        
        subsector_raw = case_when(
          path_level_1 %in% broad_activity_groups &
            !is.na(path_level_3) ~ path_level_3,
          TRUE ~ NA_character_
        ),
        
        # Sector fields
        sector_code = if_else(
          str_detect(sector_raw, fixed("---")),
          str_remove(sector_raw, "---.*$"),
          NA_character_
        ),
        
        sector_name = if_else(
          !is.na(sector_raw),
          str_squish(
            str_remove(
              sector_raw,
              "^.*?---"
            )
          ),
          NA_character_
        ),
        
        # Subsector fields
        subsector_code = if_else(
          str_detect(subsector_raw, fixed("---")),
          str_remove(subsector_raw, "---.*$"),
          NA_character_
        ),
        
        subsector_name = if_else(
          !is.na(subsector_raw),
          str_squish(
            str_remove(
              subsector_raw,
              "^.*?---"
            )
          ),
          NA_character_
        ),
        
        # Position within the hierarchy
        activity_level = case_when(
          !is.na(subsector_raw) ~ "subsector",
          !is.na(sector_raw) ~ "sector",
          activity_group != "Total de la economía" ~ "activity_group",
          TRUE ~ "total"
        ),
        
        # Official activity code when available
        activity_code = coalesce(
          subsector_code,
          sector_code
        ),
        
        # Stable identifier, including aggregate activities
        activity_id = case_when(
          !is.na(activity_code) ~ activity_code,
          activity_group == "Actividades primarias" ~ "PRIMARY",
          activity_group == "Actividades secundarias" ~ "SECONDARY",
          activity_group == "Actividades terciarias" ~ "TERTIARY",
          TRUE ~ "TOTAL"
        ),
        
        # Most specific activity label
        activity_name = case_when(
          !is.na(subsector_name) ~ subsector_name,
          !is.na(sector_name) ~ sector_name,
          TRUE ~ activity_group
        ),
        
        # Complete activity hierarchy
        activity_full_name = case_when(
          activity_level == "subsector" ~ str_c(
            activity_group,
            sector_name,
            subsector_name,
            sep = " | "
          ),
          activity_level == "sector" ~ str_c(
            activity_group,
            sector_name,
            sep = " | "
          ),
          TRUE ~ activity_group
        )
      ) %>%
      
      # Split Descriptores into separate fields
      separate_wider_delim(
        Descriptores,
        delim = "|",
        names = c(
          "unit",
          "concept_raw",
          "geography_raw"
        ),
        too_few = "align_start",
        too_many = "merge"
      ) %>%
      
      # Convert annual columns to one observation per row
      pivot_longer(
        cols = matches("^\\d{4}"),
        names_to = "year_raw",
        values_to = "value"
      ) %>%
      
      mutate(
        dataset = dataset_name,
        
        # Convert 2023<R> to 2023
        year = as.integer(
          str_extract(
            year_raw,
            "^\\d{4}"
          )
        ),
        
        # Preserve the revision marker
        revision_status = case_when(
          str_detect(year_raw, fixed("<R>")) ~ "revised",
          TRUE ~ "not_marked"
        ),
        
        # Extract markers such as <C1>
        geography_note = str_remove_all(
          str_extract(
            geography_raw,
            "<[^>]+>$"
          ),
          "[<>]"
        ),
        
        # Remove markers from the geography name
        geography = str_squish(
          str_remove(
            geography_raw,
            "<[^>]+>$"
          )
        ),
        
        # Separate the accounting code from its name
        concept_code = if_else(
          str_detect(concept_raw, fixed("---")),
          str_remove(concept_raw, "---.*$"),
          NA_character_
        ),
        
        concept_name = str_squish(
          str_remove(
            concept_raw,
            "^.*?---"
          )
        )
      ) %>%
      
      select(
        dataset,
        source_file,
        
        # Original and separated INEGI hierarchy
        series_path,
        series_name,
        path_level_1,
        path_level_2,
        path_level_3,
        
        geography,
        geography_note,
        
        # Normalized activity hierarchy
        activity_id,
        activity_group,
        activity_level,
        activity_code,
        activity_name,
        activity_full_name,
        sector_code,
        sector_name,
        subsector_code,
        subsector_name,
        
        unit,
        concept_code,
        concept_name,
        year,
        revision_status,
        value
      )
    
    # Confirm every file matched the index and received an activity ID
    stopifnot(
      !any(is.na(long_data$series_path)),
      !any(is.na(long_data$activity_id))
    )
    
    return(long_data)
  }
  
  pibe_detallada_long <- tidy_pibe_activity_files(
    data_list = pibe_detallada_list,
    data_dir = pibe_detallada_dir,
    dataset_name = "pibe_detailed"
  )
  
  pibe_retro_long <- tidy_pibe_activity_files(
    data_list = pibe_retro_list,
    data_dir = pibe_retro_dir,
    dataset_name = "pibe_retro"
  )
  
  # The retro series have data from before 2003 although not classified, we can merge them 
  pibe_long <- bind_rows(
    pibe_retro_long %>%
      filter(year < 2003),
    
    pibe_detallada_long
  ) %>%
    arrange(
      geography,
      activity_id,
      concept_name,
      unit,
      year
    )
  
  # and insert a column detailing the origin
  pibe_long <- bind_rows(
    pibe_retro_long %>%
      filter(year < 2003),
    
    pibe_detallada_long
  ) %>%
    mutate(
      historical_coverage = case_when(
        year < 2003 ~ "retropolated_aggregate",
        TRUE ~ "detailed"
      )
    ) %>%
    arrange(
      geography,
      activity_id,
      concept_name,
      unit,
      year
    )
  
}

# Example, dashboard where i can select a geo and an activity and value

# We have 11 sectors
unique(pibe_detallada_long$sector_name)

# And 19 subsectors
unique(pibe_detallada_long$subsector_name)

state_input <- "Morelos"
subsector_input <- "Agricultura"

# filter dataset and columns
plot_df <- pibe_detallada_long %>%
  filter(geography == state_input,
         subsector_name == subsector_name)
