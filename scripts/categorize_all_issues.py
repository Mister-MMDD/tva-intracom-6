import sys
from pathlib import Path

project_root = Path(r"D:\Utilisateurs\matth\Visual Studio projets\tva-intracom 6")
sys.path.insert(0, str(project_root))

from tva_intracom.i18n.i18n_validator import load_all_translations

translations = load_all_translations()
fr_keys = set(translations['fr'].keys())
en_keys = set(translations['en'].keys())

extra_fr_keys = fr_keys - en_keys
common_keys = fr_keys & en_keys

py_files = list(project_root.rglob('*.py'))
py_files = [
    p for p in py_files 
    if '.venv' not in str(p) and 'venv' not in str(p) 
    and 'i18n' not in str(p)
    and '.artifacts' not in str(p)
]

code_contents = '\n'.join([p.read_text(encoding='utf-8', errors='ignore') for p in py_files])

# Check extra FR keys usage
extra_fr_used = []
extra_fr_unused = []

for k in sorted(extra_fr_keys):
    if k in code_contents:
        extra_fr_used.append(k)
    else:
        extra_fr_unused.append(k)

# Check common keys usage (keys in both FR and EN)
common_unused = []
for k in sorted(common_keys):
    if k not in code_contents:
        # Check dynamic prefix match
        prefix = k.split('_')[0]
        if prefix in ['country', 'glossary', 'rates', 'badge', 'status', 'vat']:
            continue
        common_unused.append(k)

print(f"=== RECAPITULATIF DE L'ANALYSE POUSSEE DE L'I18N ===")
print(f"Total clés en Français : {len(fr_keys)}")
print(f"Total clés dans les 6 autres langues (EN, DE, ES, IT, PL, PT) : {len(en_keys)}")
print(f"Clés présentes uniquement en FR : {len(extra_fr_keys)}")
print(f"  - Clés uniques FR réellement UTILISÉES dans le code : {len(extra_fr_used)} (à traduire dans les 6 langues)")
print(f"  - Clés uniques FR OBSOLÈTES / MORTE (non utilisées) : {len(extra_fr_unused)} (à supprimer de fr.toml)")
print(f"Clés communes à TOUTES les langues mais OBSOLÈTES dans le code : {len(common_unused)} (à supprimer de tous les fichiers)")

print("\n--- 1. Clés FR réellement UTILISÉES à traduire en EN, DE, ES, IT, PL, PT (52) ---")
for k in extra_fr_used:
    print(f"  - {k}")

print("\n--- 2. Clés FR OBSOLÈTES uniquement dans fr.toml (32) ---")
for k in extra_fr_unused:
    print(f"  - {k}")

print("\n--- 3. Clés OBSOLÈTES présentes dans TOUTES les langues (8) ---")
for k in common_unused:
    print(f"  - {k}")
