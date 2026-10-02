import sys
from pathlib import Path
import re
import toml

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

code_contents = [p.read_text(encoding='utf-8', errors='ignore') for p in py_files]
combined_code = '\n'.join(code_contents)

# Find all f-strings or format patterns in code
fstring_patterns = re.findall(r'[fF][\'"]([^\'"]+)[\'"]', combined_code)

def is_key_used(key):
    # 1. Literal presence in code
    if key in combined_code:
        return True
        
    # 2. Glossary terms
    for g_key in GLOSSARY_TERM_KEYS:
        if key in [
            f'glossary_terms_{g_key}_title',
            f'glossary_terms_{g_key}_definition',
            f'glossary_terms_{g_key}_tooltip'
        ]:
            return True

    # 3. Dynamic ISO country codes
    if key.startswith('country_'):
        return True

    # 4. Other dynamic prefixes
    for pat in fstring_patterns:
        if '{' in pat and '}' in pat:
            regex_pat = '^' + re.sub(r'\{[^\}]+\}', '.*', pat) + '$'
            try:
                if re.match(regex_pat, key):
                    return True
            except Exception:
                pass
                
    return False

used_keys = {k for k in all_keys if is_key_used(k)}
unused_keys = all_keys - used_keys

print(f"Total unique keys before cleanup: {len(all_keys)}")
print(f"Keys kept (used in code): {len(used_keys)}")
print(f"Keys removed (obsolete/dead): {len(unused_keys)}")

fr_dict = translations['fr']
missing_in_en_for_used = [k for k in sorted(used_keys) if k in fr_dict and k not in translations['en']]
print(f"Used keys in FR that need translation in the other 6 languages: {len(missing_in_en_for_used)}")
for k in missing_in_en_for_used:
    print(f"  - {k}: {fr_dict[k]}")
