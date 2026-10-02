import sys
from pathlib import Path

project_root = Path(r"D:\Utilisateurs\matth\Visual Studio projets\tva-intracom 6")
sys.path.insert(0, str(project_root))

from tva_intracom.i18n.i18n_validator import load_all_translations

translations = load_all_translations()

dead_candidates = [
    "audit_col_stock_dest",
    "bce_rates_oss_disclaimer_range",
    "dl_reduced_rate_metric",
    "file_detail_expander",
    "help_tip_audit",
    "help_tip_declarations",
    "help_tip_detail",
    "help_tip_downloads",
    "help_tip_general",
    "help_tip_vies",
    "help_tip_viz",
    "import_summary_multi",
    "import_summary_single",
    "onboarding_download_example_btn",
    "onboarding_upload_label",
    "onboarding_upload_success",
    "quick_action_audit",
    "quick_action_declarations",
    "quick_action_downloads",
    "quick_action_vies",
    "quick_actions_title",
    "viz_annotation_peak",
    "viz_annotation_top_countries",
    "viz_annotations_title",
    "viz_data_ca_ht",
    "viz_data_refund_ht",
    "viz_data_vat_due",
    "viz_data_vat_refund",
    "viz_download_image_btn",
    "viz_export_error",
    "viz_export_image_btn",
    "viz_export_kaleido_hint",
    "viz_filter_country",
    "viz_filter_include_refunds",
    "viz_filter_period",
    "viz_filters_title",
    "viz_period_all",
    "viz_period_monthly",
    "viz_period_quarterly",
    "viz_period_yearly"
]

py_files = list(project_root.rglob('*.py'))
py_files = [
    p for p in py_files 
    if '.venv' not in str(p) and 'venv' not in str(p) 
    and 'i18n' not in str(p)
    and '.artifacts' not in str(p)
]

code_map = {str(p.relative_to(project_root)): p.read_text(encoding='utf-8', errors='ignore') for p in py_files}

print("=== VERIFICATION DES CLEFS PAR RAPPORT AUX FICHIERS DU PROJET ===")
for key in dead_candidates:
    found_in = []
    for rel_path, content in code_map.items():
        if key in content:
            found_in.append(rel_path)
    
    in_fr = "FR" if key in translations['fr'] else "  "
    in_en = "EN" if key in translations['en'] else "  "
    if found_in:
        print(f"✅ [{in_fr}][{in_en}] {key} -> Trouvée dans : {', '.join(found_in)}")
    else:
        print(f"❌ [{in_fr}][{in_en}] {key} -> ABSENTE de tout le code python")
