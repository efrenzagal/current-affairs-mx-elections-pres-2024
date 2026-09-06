# Charts/tables for MIEL 

#Import libraries
{
  library(DBI)
  library(RSQLite)
  library(dplyr)
  library(tidyr)
  library(jsonlite)
  library(purrr)
}

# Read data
{
  #setwd('Documents/GitHub/current-affairs-mx-elections-pres-2024/')
  con <- dbConnect(SQLite(), "election_data.db")
}

# Queries
{
  # Information on voting events
  dim_gaceta_vote <- dbGetQuery(con, "SELECT * FROM dim_gaceta_vote")
  dim_senado_vote <- dbGetQuery(con, "SELECT * FROM dim_senado_vote")
  
  # Information on legislators
  dim_gaceta_deputy <- dbGetQuery(con, "SELECT * FROM dim_gaceta_deputy")
  dim_senador <- dbGetQuery(con, "SELECT * FROM dim_senador")
  
  # Information on official 2024 legislative seats
  dim_diputados_ine <- dbGetQuery(con, "SELECT * FROM dim_diputados")
  dim_senadores_ine <- dbGetQuery(con, "SELECT * FROM dim_senadores")
  
  # Information on individual votes
  fact_gaceta_deputy_vote <- dbGetQuery(con, "SELECT * FROM fact_gaceta_deputy_vote")
  fact_senador_vote <- dbGetQuery(con, "SELECT * FROM fact_senador_vote")
  
  # Information on reported vote totals
  fact_gaceta_vote_summary <- dbGetQuery(con, "SELECT * FROM fact_gaceta_vote_summary")
  
  # Information on vote classifications
  fact_gaceta_vote_classification <- dbGetQuery(con, "SELECT * FROM fact_gaceta_vote_classification")
  fact_senado_vote_classification <- dbGetQuery(con, "SELECT * FROM fact_senado_vote_classification")
  
  # Information on initiatives and their proposers
  dim_gaceta_iniciativa <- dbGetQuery(con, "SELECT * FROM dim_gaceta_iniciativa")
  dim_senado_iniciativa <- dbGetQuery(con, "SELECT * FROM dim_senado_iniciativa")
  
  # Legislature 66 deputy seat-member relationships
  fact_legislature_66_deputy_seat_member <- dbGetQuery(con, "SELECT * FROM fact_legislature_66_deputy_seat_member")
  
  # Information on official Congress roster snapshots
  dim_congress_roster_snapshot <- dbGetQuery(con, "SELECT * FROM dim_congress_roster_snapshot")
  fact_congress_roster_seat <- dbGetQuery(con, "SELECT * FROM fact_congress_roster_seat")
  
  # Information on derived occupancy and party histories
  fact_congress_seat_occupancy <- dbGetQuery(con, "SELECT * FROM fact_congress_seat_occupancy")
  fact_congress_party_membership <- dbGetQuery(con, "SELECT * FROM fact_congress_party_membership")
  
  dbDisconnect(con)
}


#Senadores
{
  #Joining datasets
  {
    data_senado <- select(fact_senador_vote, 
                          votacion_id, senador_id, voto, grupo_parlamentario) %>%
      # General vote information
      left_join(select(dim_senado_vote, 
                       votacion_id, vote_date, legislature, description), 
                by = "votacion_id") %>%
      # Vote classification
      left_join(select(fact_senado_vote_classification, 
                       votacion_id, tema_politica), 
                by = "votacion_id") %>%
      # Senator names: note that dim_senador has more than 128 rows given subsitutes
      left_join(select(dim_senador, 
                       senador_id, senador_name), 
                by = "senador_id") %>%
      # Election seat and substitute information
      left_join(select(dim_senadores, 
                       senador_id, senador_seat_id, source_name_role, seat_type, election_party = party_key, nombre_estado), 
                by = "senador_id") %>%
      # Final column order
      select(vote_date, legislature, votacion_id, senador_id, senador_name, voto, grupo_parlamentario, senador_seat_id, source_name_role, seat_type, election_party, nombre_estado, description, tema_politica) %>%
      filter(legislature == 66)
    #nrow(data_senado) == nrow(fact_senador_vote)
  }
  
  #Sanity checks
  {
    #Votes per senador
    data_senado %>%
      count(senador_id, senador_name, source_name_role) %>%
      arrange(desc(n)) %>% View()
    
    #votes per seat,
    data_senado %>%
      count(senador_seat_id) %>%
      arrange(desc(n)) %>% View()
  }
}

#Sanity checks
