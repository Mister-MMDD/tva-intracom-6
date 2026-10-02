import sys
import ast
from pathlib import Path

project_root = Path(r"D:\Utilisateurs\matth\Visual Studio projets\tva-intracom 6")
sys.path.insert(0, str(project_root))

from tva_intracom.i18n.i18n_validator import load_all_translations, get_all_keys
from tva_intracom.ui.glossary import GLOSSARY_TERM_KEYS

translations = load_all_translations()
all_keys = get_all_keys(translations)

py_files = list(project_root.rglob('*.py'))
py_files = [
    p for p in py_files 
    if '.venv' not in str(p) and 'venv' not in str(p) 
    and 'i18n_validator.py' not in str(p)
    and '.artifacts' not in str(p)
]

string_literals = set()

for p in py_files:
    try:
        content = p.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(content, filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                string_literals.add(node.value)
            elif isinstance(node, ast.JoinedStr): # f-string parts
                for value in node.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        string_literals.add(value.value)
    except Exception as e:
        pass

# Now check which keys in all_keys are used
used_keys = set()
unused_keys = set()

for k in sorted(all_keys):
    # 1. Exact string literal in AST
    if k in string_literals:
        used_keys.add(k)
        continue
        
    # 2. Glossary terms
    if any(k in [f'glossary_terms_{g}_title', f'glossary_terms_{g}_definition', f'glossary_terms_{g}_tooltip'] for g in GLOSSARY_TERM_KEYS):
        used_keys.add(k)
        continue

    # 3. Country ISO keys
    if k.startswith('country_'):
        used_keys.add(k)
        continue

    # 4. Prefix in f-string constants (e.g., "siren_stepper_", "onboarding_tour_", "onboarding_guide_")
    prefix = k.rsplit('_', 1)[0]
    prefix2 = k.split('_')[0]
    
    # Check if prefix is in string_literals
    if any(s.endswith(prefix) or s.endswith(prefix + '_') or s.endswith(prefix2 + '_') for s in string_literals):
        used_keys.add(k)
        continue

    unused_keys.add(k)

print(f"Total keys across TOMLs: {len(all_keys)}")
print(f"Keys kept (used in python code): {len(used_keys)}")
print(f"Keys unused (obsolete / dead): {len(unused_keys)}")

print("\n--- Detailed List of Unused Keys ---")
for k in sorted(unused_keys):
    in_fr = "FR" if k in translations['fr'] else "  "
    in_en = "EN" if k in translations['en'] else "  "
    print(f"  [{in_fr}] [{in_en}] {k}")
