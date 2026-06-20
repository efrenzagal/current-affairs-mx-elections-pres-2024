# DATABOOK INDEX: Mexico 2024 Election Ballot Count Records

This document defines the structure, relationships, and contents of five output tables created from Mexico's 2024 election PREP (Preliminary Official Electoral Results) data.

## TABLE OVERVIEW

### 1. dim_election (Dimension Table)
**Purpose**: Metadata for each election contest
**Grain**: One row per election
**Primary Key**: election_id
**Row Count**: 5 rows (one for each 2024 election)

Content: 5 elections covering Presidential (PRE), Federal Deputies MR/RP (DIP), and Senate MR/RP (SEN). For each, records the seat allocation method (direct, fptp, or proportional), total seats, and term length. Links fact table records to a specific election.

### 2. dim_geography (Dimension Table)
**Purpose**: Electoral geographic hierarchy (states, municipalities, districts, sections)
**Grain**: One row per electoral section (SECCION) per state
**Primary Key**: geo_id (format: {ID_ESTADO}_{SECCION})
**Row Count**: ~35,000 rows

Content: Unique electoral sections (secciones) nationally. Full geographic hierarchy: state → municipality → district → section. Includes optional circunscripción (proportional-representation grouping) for deputy elections. Deduplicated across all 5 elections (geography shared across all contests).

### 3. dim_casilla (Dimension Table)
**Purpose**: Polling station details for each election
**Grain**: One row per polling station per election
**Primary Key**: casilla_id (composite: {ID_ESTADO}_{SECCION}_{ACTA_CASILLA-MEC})
**Foreign Keys**: election_id, geo_id
**Row Count**: ~162,000 rows

Content: Complete details of each polling station in each election. Polling station type (B=basic, C=contagious/overflow, E=extraordinary, S=special, MEC=counting table). Eligible voter count (LISTA_NOMINAL) and processing status (ESTATUS_ACTA). Flags for electronic ballot box use. Reference to scanned ballot count record (RUTA_ACTA).

Scope: Scoped by election; same polling station may appear in multiple elections with potentially different configurations.

### 4. dim_party (Dimension Table)
**Purpose**: Political parties and electoral coalitions
**Grain**: One row per party or coalition ballot option
**Primary Key**: party_key
**Row Count**: 13 rows (7 parties + 6 coalition combinations)

Content: 7 individual parties: PAN, PRI, PRD, PVEM, PT, MC, MORENA. 6 electoral coalition ballot options:
- 3-party: PAN_PRI_PRD, PVEM_PT_MORENA
- 2-party: PAN_PRI, PAN_PRD, PRI_PRD, PVEM_PT, PVEM_MORENA, PT_MORENA
Indicates whether each is a coalition and lists member parties.

### 5. fact_casilla_vote (Fact Table)
**Purpose**: Vote counts by polling station, party, and election
**Grain**: One row per party per polling station per election
**Primary Keys**: election_id + casilla_id + party_key
**Foreign Keys**: election_id (dim_election), casilla_id (dim_casilla), party_key (dim_party)
**Row Count**: ~2,100,000 rows
**Partition**: Physically partitioned by election_id

Content: Exact vote count for each party at each polling station in each election. Metadata on vote validity: NUM_VOTOS_VALIDOS, NUM_VOTOS_NULOS, NUM_VOTOS_CAN_NREG, TOTAL_VOTOS. These metadata columns are repeated for every party record at the same casilla (denormalized for query convenience).

## RELATIONSHIPS & FOREIGN KEYS

dim_election (election_id)
    ↑
    └── fact_casilla_vote.election_id

dim_geography (geo_id)
    ↑
    ├── dim_casilla.geo_id
    └── (inferred from dim_casilla)

dim_casilla (casilla_id, election_id)
    ↑
    └── fact_casilla_vote.(casilla_id, election_id)

dim_party (party_key)
    ↑
    └── fact_casilla_vote.party_key

## KEY DESIGN DECISIONS

### 1. Composite Polling Station Keys
- casilla_id = ID_ESTADO + SECCION + ACTA_CASILLA-MEC
- Not scoped by election; same station can appear in multiple elections
- ACTA_CASILLA-MEC encodes type + number + contiguous extension hierarchy
  - "B" = basic station
  - "C01", "C02" = contiguous extensions
  - "E01", "E01C01" = extraordinary + extensions

### 2. Vote Denormalization
- fact_casilla_vote repeats vote metadata (NUM_VOTOS_VALIDOS, TOTAL_VOTOS, etc.) for every party
- This is intentional: enables single-row queries and avoids joins for common analyses
- Trade-off: slightly larger table size for better query performance

### 3. Geography Deduplication
- dim_geography is deduplicated across all 5 elections
- Electoral sections are stable; same geo_id appears in all elections
- Reduces redundancy and simplifies geographic analysis

### 4. Casilla Election Scoping
- dim_casilla is NOT deduplicated across elections
- Polling station configuration may change between elections
- Partitioned by election_id in the fact table for performance

### 5. Coalition Representation
- 13 distinct party_key values in dim_party
- Votes for coalition ballots are recorded separately from individual parties
- Analysts can aggregate: coalition votes = sum of coalition member individual votes + coalition ballot votes

## DATA QUALITY NOTES

### Validation Fields
- ESTATUS_ACTA: Processing status; look for "Cotejo" (verified) vs. "no contable" (not counted)
- TOTAL_VOTOS Reconciliation: Validate TOTAL_VOTOS = NUM_VOTOS_VALIDOS + NUM_VOTOS_NULOS + NUM_VOTOS_CAN_NREG
- LISTA_NOMINAL: Casillas especiales (type S) typically have LISTA_NOMINAL = 0 (accept any voter in transit)

### Known Characteristics
- Zero records are valid; represents candidate received no votes at that polling station
- Missing/null votes in original data are coerced to zero in the fact table
- Some coalition ballots may not appear in all elections

## USING THESE TABLES

### Basic Aggregations
Total votes for a party nationally:
  SELECT party_key, SUM(votes) as total_votes
  FROM fact_casilla_vote
  WHERE election_id = 'PRE_2024'
  GROUP BY party_key
  ORDER BY total_votes DESC;

Votes by state and party:
  SELECT g.NOMBRE_ESTADO, f.party_key, SUM(f.votes) as votes
  FROM fact_casilla_vote f
  JOIN dim_casilla c ON f.casilla_id = c.casilla_id AND f.election_id = c.election_id
  JOIN dim_geography g ON c.geo_id = g.geo_id
  WHERE f.election_id = 'DIP_MR_2024'
  GROUP BY g.NOMBRE_ESTADO, f.party_key;

### Turnout Analysis
Participation rate by section:
  SELECT geo_id, CAST(SUM(TOTAL_VOTOS) as float) / SUM(LISTA_NOMINAL) as turnout
  FROM fact_casilla_vote f
  JOIN dim_casilla c ON f.casilla_id = c.casilla_id AND f.election_id = c.election_id
  WHERE f.election_id = 'DIP_MR_2024'
    AND LISTA_NOMINAL > 0
  GROUP BY geo_id;

Created: 2024
Data Source: Mexico's INE PREP (Preliminary Official Electoral Results) system
Coverage: 2024 Elections (Presidential, Federal Deputies, Senate)
