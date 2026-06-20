
#Go to https://www.ine.mx/transparencia/datos-abiertos/#/
# Example: https://www.ine.mx/transparencia/datos-abiertos/#/archivo/bases-de-datos-de-los-resultados-electorales-federales-del-proceso-electoral-federal-2023-2024
# https://www.ine.mx/wp-content/uploads/2024/09/PRESIDENCIA_2024.zip
# https://www.ine.mx/wp-content/uploads/2024/09/SENADURIAS_MR_2024.zip
# https://www.ine.mx/wp-content/uploads/2024/09/SENADURIAS_RP_2024.zip
# https://www.ine.mx/wp-content/uploads/2024/09/DIPUTACIONES_FED_MR_2024.zip
# https://www.ine.mx/wp-content/uploads/2024/09/DIPUTACIONES_FED_RP_2024.zip

# Interesting map (cartografia electoral): https://cartografia.ine.mx/sige8/mapas/mapas-digitales, https://cartografia.ine.mx/sige8/productosCartograficos/bases to download in shapefile 
# Interesting data combining census data with geoelectoral aggregation: https://cartografia.ine.mx/sige8/estadisticos-Geoelectorales
# I think it could be interesting to use the census data to find correlations with voting results 


unzip("https://www.ine.mx/wp-content/uploads/2024/09/PRESIDENCIA_2024.zip")
# I downloaded https://www.ine.mx/wp-content/uploads/2024/09/PRESIDENCIA_2024.zip
setwd('Documents/GitHub/current-affairs-mx-elections-pres-2024/')
setwd('PRESIDENCIA_2024/CSV')
available_docs <- list.files()

available_docs
print(available_docs)
for (i in available_docs) {
  print(i)
  df <- read_csv(i)  
  print(glimpse(df))
}

# I need to inspect and document each table
# After that, repeat for all zip files, the structure should be similar
# Also, I need to learn all different kind of elections in Mexico and document. When are the next and when? 

# 2018: https://computos2018.ine.mx/#/descargaBase
# 2012: https://portalanterior.ine.mx/archivos3/portal/historico/contenido/Proceso_Electoral_Federal__2011-2012/
# 2006: https://portalanterior.ine.mx/documentos/Estadisticas2006/index.htm





setwd("~/Downloads")


folders <- c("DIPUTACIONES_FED_RP_2024", "DIPUTACIONES_FED_MR_2024", "SENADURIAS_RP_2024", "SENADURIAS_MR_2024")

for (f in folders) {
  
  f <- folders[2]
  cat("\n")
  print(f)
  cat("\n")
  files <- paste(f, "CSV", sep = "/") %>% list.files() 
  cas_file <- files[which(str_detect(files, "CAS"))]
  cas_path <- paste(f, "CSV", cas_file, sep = "/") 
  print(cas_path)
  cas_data <- read_csv(cas_path)
  glimpse(cas_data)
}
