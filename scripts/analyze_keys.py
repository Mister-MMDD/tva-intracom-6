import sys
from pathlib import Path
import re

project_root = Path(r"D:\Utilisateurs\matth\Visual Studio projets\tva-intracom 6")
sys.path.insert(0, str(project_root))

from tva_intracom.i18n.i18n_validator import load_all_translations, get_all_keys

translations = load_all_translations()
all_keys = get_all_keys(translations)

py_files = list(project_root.rglob('*.py'))
py_files = [
    p for p in py_files 
    if '.venv' not in str(p) and 'venv' not in str(p) 
    and 'i18n_validator.py' not in str(p)
    and '.artifacts' not in str(p)
]

code_contents = [p.read_text(encoding='utf-8', errors='ignore') for p in py_files]
combined_code = '\n'.join(code_contents)

literal_keys = {k for k in all_keys if k in combined_code}
candidates = all_keys - literal_keys

# Let's inspect each candidate
dynamic_matches = {}
truly_unused = []

for k in sorted(candidates):
    if k.startswith('country_'):
        dynamic_matches[k] = "Dynamique via country_label(code) -> f'country_{code}'"
    elif k.startswith('glossary_terms_'):
        dynamic_matches[k] = "Dynamique via ui/glossary.py -> f'glossary_terms_{term}_{type}'"
    elif k.startswith('rates_evidence_col_') or k == 'rates_evidence_vat':
        # Check if rates_evidence is in code
        if 'rates_evidence' in combined_code:
            dynamic_matches[k] = "Dynamique via rates_evidence.py"
        else:
            truly_unused.append(k)
    else:
        # Search if prefix is in code as f-string or string construction
        # e.g. for help_tip_audit -> is help_tip_ in code?
        prefix = k.rsplit('_', 1)[0]
        if prefix in combined_code:
            dynamic_matches[k] = f"Prefixe '{prefix}' présent dans le code"
        else:
            truly_unused.append(k)

print(f"Total clés: {len(all_keys)}")
print(f"Clés littérales: {len(literal_keys)}")
print(f"Clés dynamiques identifiées: {len(dynamic_matches)}")
print(f"Clés VRAIMENT inutilisées / obsolètes: {len(truly_unused)}")

print("\n--- Clés totalement absentes du code (Clés mortes / orphelines) ---")
for k in truly_unused:
    in_fr = "FR" if k in translations['fr'] else "  "
    in_en = "EN" if k in translations['en'] else "  "
    print(f"  [{in_fr}] [{in_en}] {k}")

print("\n--- Détail des clés dynamiques par préfixe ---")
prefix_counts = {}
for k, reason in dynamic_matches.items():
    p = k.split('_')[0]
    prefix_counts[p] = prefix_counts.get(p, 0) + 1
for p, count in sorted(prefix_counts.items()):
    print(f"  - {p}_* : {count} clés (utilisées dynamiquement)")
