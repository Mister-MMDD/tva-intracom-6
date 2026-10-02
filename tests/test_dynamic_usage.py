import sys
import ast
import re
from pathlib import Path

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

code_text = "\n".join([p.read_text(encoding='utf-8', errors='ignore') for p in py_files])

# Check dynamic patterns in code_text:
# e.g., plan_achat_desc, plan_cabinet_desc, plan_pro_desc -> f"plan_{tier}_desc" in billing_gate.py
# rates_evidence_col_* -> f"rates_evidence_col_{c}"
# promo_* -> f"promo_{...}"
# payg_* -> f"payg_{...}"

dynamic_prefixes = [
    "country_", "glossary_terms_", "rates_evidence_col_", "rates_evidence_", 
    "plan_", "promo_", "payg_", "billing_", "donation_", "unlocked_", 
    "subscribe_", "unlock_", "managed_sirens_", "pricing_grid_", "purchase_history_",
    "amazon_blocking_", "last_sub_", "sub_scheduled_", "manage_sub_", "locked_preview_"
]

def is_key_used(k):
    if k in code_text:
        return True
    for dp in dynamic_prefixes:
        if k.startswith(dp):
            prefix_stem = dp.rstrip('_')
            if prefix_stem in code_text:
                return True
    return False

used_keys = {k for k in all_keys if is_key_used(k)}
unused_keys = all_keys - used_keys

print(f"Total keys across all files: {len(all_keys)}")
print(f"Keys determined as USED: {len(used_keys)}")
print(f"Keys determined as OBSOLETE/UNUSED: {len(unused_keys)}")

print("\n--- Obsolete / Unused keys ---")
for k in sorted(unused_keys):
    in_fr = "FR" if k in translations['fr'] else "  "
    in_en = "EN" if k in translations['en'] else "  "
    print(f"  [{in_fr}] [{in_en}] {k}")
