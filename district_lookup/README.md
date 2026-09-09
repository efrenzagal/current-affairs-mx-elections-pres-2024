# Federal district lookup

Small MVP for resolving a Mexican federal electoral district from the 2024
INE geography already loaded in `election_data.db`.

- `build_lookup.py` generates the browser index used by the website.
- `resolver.ts` contains the reusable lookup functions.
- Municipality lookups can return more than one district because some
  municipalities are split across federal districts.
- Each district carries the number of the municipality's secciones that sit in
  it, and the index lists them heaviest-first. Splits are rarely even --
  Cuauhtemoc is 300 secciones in district 12 against 89 in district 2, and
  Cuernavaca is 209 against 2 -- so district-number order gave a sliver the
  same weight as the obvious answer.
- A district is named after its cabecera, which can sit in a different
  municipality than the one the reader picked. The share shown beside each
  district is what tells that apart from an error.

Rebuild the index from the repository root:

```bash
python3 district_lookup/build_lookup.py
```

