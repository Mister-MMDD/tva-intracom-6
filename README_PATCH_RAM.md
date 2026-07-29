# Patch optimisation RAM — dev

Fichiers modifiés (chemins repo-relatifs, prêts à écraser sur `dev`) :

- `app.py` — vide `parse_result.sales/.refunds/.fc_transfers` avant mise en
  cache dans `_parse_cache_data` (session_state). Ces attributs étaient
  dupliqués avec `all_sales`/`all_refunds` sans plus jamais être relus après
  l'agrégation. Gain mesuré : ~1,45 Mo sur les 2 fichiers projet (~1700
  lignes), extrapolé à ~77 Mo sur un import PAN-EU de 150k lignes.
- `tva_intracom/parsers/amazon/loader.py` — libère explicitement le
  DataFrame polars/pandas juste après conversion en `raw_rows`. Gain mesuré
  négligeable (raw_rows domine déjà la mémoire au même moment), mais
  correction sans risque conservée.
- `tva_intracom/oss_export.py` — 3 onglets Excel (OSS_Résumé, OSS_Détail,
  B2B_Recap) réécrits en `Workbook(write_only=True)`, alignés avec
  `excel_report.py`. Onglets qui scalent avec le nombre de ventes
  cross-border, pas juste l'agrégation par pays.
- `tests/test_oss_export.py` — nouveau, aucune couverture n'existait avant
  sur `oss_export.py`.
- `scripts/profile_ram_parsing.py` — script de mesure réutilisable
  (nécessite `pip install pympler --break-system-packages`).
  Usage : `PYTHONPATH=. python3 scripts/profile_ram_parsing.py fichier1.txt fichier2.txt`

Validation faite : `py_compile` OK sur tous les fichiers modifiés, suite de
tests complète = 149 passed / 12 failed / 37 errors (baseline 143/12/37 +
6 nouveaux tests, aucune régression).
