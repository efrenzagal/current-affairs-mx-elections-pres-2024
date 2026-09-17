# Charts/tables for MIEL 

#Import libraries
{
  library(DBI)
  library(RSQLite)
  library(dplyr)
  library(tidyr)
  library(jsonlite)
  library(purrr)
  library(ggplot2)
  library(plotly)
}

# Read data
{
  #setwd('Documents/GitHub/current-affairs-mx-elections-pres-2024/')
  con <- dbConnect(SQLite(), "election_data.db")
}

# Practice queries
{
  example_vote_agg_query <- "
  WITH vote_roll_call AS (
  SELECT fcdv.gaceta_vote_id, fcdv.deputy_id, fcdv.party_key AS party, fcdv.vote_choice 
  FROM fact_gaceta_deputy_vote fcdv
  WHERE fcdv.gaceta_vote_id = 'GACETA_L66_TABLA2EX1_4'
  )
  
  SELECT party, vote_choice, COUNT(deputy_id) AS n
  FROM vote_roll_call
  GROUP BY party, vote_choice
  ORDER BY party, n DESC"
  
  example_vote_agg <- dbGetQuery(con, example_vote_agg_query)
  
  example_role_call_augmented_query <- "
  SELECT dgi.title, dgi.vote_date, dgi.vote_context, dgi.source_url, 
  fgvc.origen, fgvc.etapa_votacion, fgvc.tipo_instrumento, fgvc.tema_politica, fgvc. review_status
  FROM dim_gaceta_vote dgi
  LEFT JOIN fact_gaceta_vote_classification fgvc
  ON dgi.gaceta_vote_id = fgvc.gaceta_vote_id
  WHERE dgi.gaceta_vote_id = 'GACETA_L66_TABLA2EX1_4'"
  
  example_role_call_augmented <- dbGetQuery(con, example_role_call_augmented_query)
  
  votes_legislator_augmented_query <- "
  SELECT
      dgd.deputy_id,
      dgd.deputy_name,
      fgdp.gaceta_vote_id,
      fgdp.party_key,
      fgdp.vote_choice,
      dgv.title,
      fgvc.tema_politica,
      fgvc.etapa_votacion,
      dgv.vote_date
  FROM dim_gaceta_deputy dgd
  LEFT JOIN fact_gaceta_deputy_vote fgdp
      ON dgd.deputy_id = fgdp.deputy_id
  LEFT JOIN dim_gaceta_vote dgv
      ON fgdp.gaceta_vote_id = dgv.gaceta_vote_id
  LEFT JOIN fact_gaceta_vote_classification fgvc
      ON fgdp.gaceta_vote_id = fgvc.gaceta_vote_id
  WHERE dgd.deputy_id = 'DEP_004CF9A25F6A'
    AND dgv.legislature = 66
  ORDER BY dgv.vote_date DESC"
  
  votes_legislator_augmented <- dbGetQuery(con, votes_legislator_augmented_query)
  
}


#Exercise: which are the most contentious topics?
{
  query_topic_vote <-  "SELECT fcdp.gaceta_vote_id, fgvc.tema_politica, fcdp.vote_choice, dgv.vote_date
  FROM fact_gaceta_deputy_vote fcdp
  LEFT JOIN dim_gaceta_vote dgv
  ON fcdp.gaceta_vote_id = dgv.gaceta_vote_id
  LEFT JOIN fact_gaceta_vote_classification fgvc 
  ON fcdp.gaceta_vote_id = fgvc.gaceta_vote_id
  WHERE dgv.legislature = 66"
   
  topic_vote_df <- dbGetQuery(con, query_topic_vote)
  
  vote_classification <- topic_vote_df %>%
    distinct(gaceta_vote_id, tema_politica)
  
  roll_call_agg <- topic_vote_df %>%
    group_by(gaceta_vote_id) %>%
    count(vote_choice) %>%
    pivot_wider(names_from =  vote_choice, 
                values_from = n) %>% 
    mutate(Favor = if_else(is.na(Favor), 0, Favor),
           Contra = if_else(is.na(Contra), 0, Contra),
           Total = Contra + Favor,
           Margen = abs(Favor - Contra)/Total) %>%
    left_join(vote_classification, by = 'gaceta_vote_id')
  
  agg_by_topic <- roll_call_agg %>%
    group_by(tema_politica) %>%
    summarise(n = n_distinct(gaceta_vote_id),
              avg_margen = mean(Margen, na.rm = T),
              min_margen = min(Margen, na.rm = T),
              max_margen = max(Margen, na.rm = T)) %>%
    filter(n >= 5) %>%
    arrange(desc(avg_margen))
  
  ggplotly(roll_call_agg %>%
    ggplot(aes(x = Total, y = Margen, colour = tema_politica)) +
    geom_point() +
    theme_classic())
}

#Exercise: finding multiple people in a seat per party and type of seat
{
  shared_seat_query <- "WITH seat_member_ine AS (
    SELECT fl_sm.seat_id, fl_sm.person_id, fl_sm.vote_count, dd.party_key, dd.seat_type
    FROM fact_legislature_66_seat_member fl_sm
    LEFT JOIN dim_diputados dd
    ON fl_sm.seat_id = dd.diputado_id
    WHERE chamber = 'DIP'
    AND vote_count > 0
    ORDER BY seat_id
  ),
  
  n_persons_per_seat AS (
    SELECT party_key, seat_type, seat_id, COUNT(person_id) AS n_persons
    FROM seat_member_ine
    GROUP BY 1, 2, 3
  )
  
  
  SELECT party_key, seat_type, 
  COUNT(DISTINCT seat_id) AS total_seats,
  SUM(CASE WHEN n_persons > 1 THEN 1 ELSE 0 END) AS seats_multiple_people,
  ROUND(1.0*SUM(CASE WHEN n_persons > 1 THEN 1 ELSE 0 END)/COUNT(seat_id), 2) AS percentage_shared
  FROM  n_persons_per_seat
  GROUP BY 1,2  
  ORDER  BY total_seats DESC"
  
  shared_seat_df <- dbGetQuery(con, shared_seat_query)
  
}

#Exercise: find deputies that have votes registered in different party
{
  different_parties_query <- "WITH deputy_id_name AS (
  SELECT fgdv.deputy_id, fgdv.gaceta_vote_id, dgd.deputy_name, fgdv.party_key, fgdv.vote_choice 
  FROM fact_gaceta_deputy_vote fgdv
  LEFT JOIN dim_gaceta_deputy dgd
  ON fgdv.deputy_id = dgd.deputy_id
  LEFT JOIN dim_gaceta_vote dgv
  ON fgdv.gaceta_vote_id = dgv.gaceta_vote_id
  WHERE dgv.legislature = 66
  )
  
  SELECT deputy_id, deputy_name, 
  COUNT(DISTINCT party_key) AS n_party_key,
  GROUP_CONCAT(DISTINCT party_key) AS parties_str
  FROM deputy_id_name
  GROUP BY 1, 2
  HAVING n_party_key > 1"
  
  different_parties_df <- dbGetQuery(con, different_parties_query)
}

#Exercise: function to approximate date where a deputy changed party
{
  find_date_diff <- function(deputy_id){
    #deputy_id <- 'DEP_FD897AB4E86E'
    query_parameter <- "WITH fgdv_augmented AS (
    SELECT fgdv.gaceta_vote_id, fgdv.deputy_id, fgdv.vote_choice, fgdv.party_key, dgv.vote_date, dgd.deputy_name, LAG(party_key) OVER (
      PARTITION BY fgdv.deputy_id
      ORDER BY dgv.vote_date, fgdv.gaceta_vote_id
    ) AS party_key_last_vote
    FROM fact_gaceta_deputy_vote fgdv
    LEFT JOIN dim_gaceta_vote dgv
    ON fgdv.gaceta_vote_id = dgv.gaceta_vote_id
    LEFT JOIN dim_gaceta_deputy dgd
    ON fgdv.deputy_id = dgd.deputy_id
    WHERE fgdv.deputy_id = '{{deputy_id_input}}'
    AND legislature = 66
  )
  
  SELECT * 
    FROM fgdv_augmented
  WHERE NOT party_key =  party_key_last_vote"
    
  query_sub <- (gsub('\\{\\{deputy_id_input\\}\\}', deputy_id, query_parameter))
    
  df_result <- dbGetQuery(con, query_sub)
  return(df_result)
  }

  all_results <- data.frame()
  deputy_id_changes <- different_parties_df$deputy_id
  for (d_id in deputy_id_changes) {
    all_results <- rbind.data.frame(all_results, find_date_diff(d_id))
  }
  
  transition_summary <- all_results %>%
    group_by(
      gaceta_vote_id,
      vote_date,
      previous_party = party_key_last_vote,
      new_party = party_key
    ) %>%
    summarise(
      n_deputies = n_distinct(deputy_id),
      .groups = "drop"
    ) %>%
    arrange(desc(n_deputies), vote_date)
  
  transition_summary
  
}