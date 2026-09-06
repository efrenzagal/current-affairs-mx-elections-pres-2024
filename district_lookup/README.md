# Federal district lookup

Small MVP for resolving a Mexican federal electoral district from the 2024
INE geography already loaded in `election_data.db`.

- `build_lookup.py` generates the browser index used by the website.
- `resolver.ts` contains the reusable lookup functions.
- Municipality lookups can return more than one district because some
  municipalities are split across federal districts.
- Postal-code lookup is a beta convenience powered by Zippopotam.us. If its
  place name cannot be matched to an INE municipality, the UI asks the reader
  to use the state/municipality selectors instead.

Rebuild the index from the repository root:

```bash
python3 district_lookup/build_lookup.py
```

