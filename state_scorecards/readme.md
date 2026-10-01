This is a project to create a robust database for many public policy dimensions from all the states (even municipios, when data is available), this should help, first, to make an easy, holistic understanding of each State and eventually evaluate specific measures (doing pre-post and synthetic control). 
We should follow the ingestion process used for the PIBE data. 
The usual workflow is as follows:
- Help me research data sources, I'll download everything
- Usually the files will be in ugly formats, so I want them to inspect them in R (I like R studio better), after we agree on the data structure, we will productize this in a cv called raw_to_parquet.py and saved the data locally. 
- Then we will do an ingest.py file to uploaded to my data warehourse (currently called election_data, probablly i need to re-name). 
- After that I'll do some analysis in R studio and eventually productize everything in the website. 
