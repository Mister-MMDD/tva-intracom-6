"""Module de validation pour les fichiers de traduction i18n.

Ce module fournit des fonctions pour vérifier la cohérence des fichiers
de traduction TOML entre différentes langues.
"""

from pathlib import Path
from typing import Dict, List, Set, Tuple
import toml


I18N_DIR = Path(__file__).resolve().parent
LANGUAGES = ["fr", "en", "de", "es", "it", "pl", "pt"]

# Clés qui peuvent être identiques à leur traduction (mots universels ou abréviations)
ALLOWED_FALLBACK_KEYS = {
    "TOTAL",  # Peut être identique dans plusieurs langues
    "TOTAL HT",  # Abréviation commune
}


def load_all_translations() -> Dict[str, Dict]:
    """Charge tous les fichiers de traduction pour toutes les langues.
    
    Returns:
        Dictionnaire avec les codes de langue comme clés et les traductions comme valeurs.
        
    Raises:
        FileNotFoundError: Si un fichier de traduction est manquant.
        toml.TomlDecodeError: Si un fichier TOML est mal formé.
    """
    translations = {}
    for lang in LANGUAGES:
        file_path = I18N_DIR / f"{lang}.toml"
        if not file_path.exists():
            raise FileNotFoundError(f"Fichier de traduction manquant: {file_path}")
        
        try:
            content = file_path.read_text(encoding="utf-8")
            translations[lang] = toml.loads(content)
        except Exception as e:
            raise toml.TomlDecodeError(f"Erreur de parsing TOML pour {lang}.toml: {e}")
    
    return translations


def get_all_keys(translations: Dict[str, Dict]) -> Set[str]:
    """Récupère l'ensemble de toutes les clés présentes dans toutes les langues.
    
    Args:
        translations: Dictionnaire des traductions par langue.
        
    Returns:
        Ensemble de toutes les clés uniques.
    """
    all_keys: set[str] = set()
    for lang_translations in translations.values():
        all_keys.update(lang_translations.keys())
    return all_keys


def compare_translations(translations: Dict[str, Dict]) -> Dict[str, Dict]:
    """Compare les clés de traduction entre toutes les langues.
    
    Args:
        translations: Dictionnaire des traductions par langue.
        
    Returns:
        Dictionnaire contenant les résultats de la comparaison avec les clés:
        - 'missing_keys': clés manquantes par langue
        - 'orphan_keys': clés orphelines par langue (présentes dans une langue mais pas dans d'autres)
        - 'empty_translations': traductions vides par langue
        - 'fallback_translations': traductions en fallback (clé = valeur)
    """
    all_keys = get_all_keys(translations)
    results: dict[str, dict] = {
        'missing_keys': {},
        'orphan_keys': {},
        'empty_translations': {},
        'fallback_translations': {}
    }
    
    for lang, lang_translations in translations.items():
        lang_keys = set(lang_translations.keys())
        
        # Clés manquantes (présentes dans d'autres langues mais pas dans celle-ci)
        missing = all_keys - lang_keys
        if missing:
            results['missing_keys'][lang] = sorted(missing)
        
        # Clés orphelines (présentes uniquement dans cette langue)
        orphan = lang_keys - all_keys
        # Note: avec la logique actuelle, orphan sera toujours vide car all_keys contient toutes les clés
        # On le garde pour cohérence si la logique change
        
        # Traductions vides
        empty = [key for key, value in lang_translations.items() if not value or value.strip() == ""]
        if empty:
            results['empty_translations'][lang] = sorted(empty)
        
        # Traductions en fallback (clé = valeur)
        fallback = [key for key, value in lang_translations.items() if value == key and key not in ALLOWED_FALLBACK_KEYS]
        if fallback:
            results['fallback_translations'][lang] = sorted(fallback)
    
    # Calculer les clés orphelines réelles (présentes dans une langue mais pas dans toutes les autres)
    key_counts: dict[str, int] = {}
    for lang_translations in translations.values():
        for key in lang_translations.keys():
            key_counts[key] = key_counts.get(key, 0) + 1
    
    # Une clé est orpheline si elle n'est présente que dans une seule langue
    orphan_keys_global = [key for key, count in key_counts.items() if count == 1]
    
    if orphan_keys_global:
        for lang, lang_translations in translations.items():
            lang_orphans = [key for key in orphan_keys_global if key in lang_translations]
            if lang_orphans:
                results['orphan_keys'][lang] = sorted(lang_orphans)
    
    return results


def generate_report(results: Dict[str, Dict], verbose: bool = False, use_ascii: bool = False) -> str:
    """Génère un rapport détaillé des incohérences trouvées.
    
    Args:
        results: Résultats de la comparaison des traductions.
        verbose: Si True, inclut plus de détails dans le rapport.
        use_ascii: Si True, utilise des caractères ASCII au lieu d'emojis.
        
    Returns:
        Chaîne de caractères contenant le rapport formaté.
    """
    report_lines = []
    
    # En-tête
    report_lines.append("=" * 70)
    report_lines.append("RAPPORT DE COHÉRENCE I18N")
    report_lines.append("=" * 70)
    report_lines.append("")
    
    total_errors = 0
    
    # Symboles selon le mode
    if use_ascii:
        error_sym = "[X]"
        success_sym = "[OK]"
        warning_sym = "[!]"
    else:
        error_sym = "❌"
        success_sym = "✅"
        warning_sym = "⚠️ "
    
    # Clés manquantes
    if results['missing_keys']:
        report_lines.append(f"{error_sym} CLÉS MANQUANTES (présentes dans d'autres langues mais absentes):")
        report_lines.append("-" * 70)
        for lang, keys in results['missing_keys'].items():
            report_lines.append(f"  {lang.upper()}: {len(keys)} clé(s) manquante(s)")
            if verbose:
                for key in keys:
                    report_lines.append(f"    - {key}")
            total_errors += len(keys)
        report_lines.append("")
    else:
        report_lines.append(f"{success_sym} Aucune clé manquante")
        report_lines.append("")
    
    # Clés orphelines
    if results['orphan_keys']:
        report_lines.append(f"{error_sym} CLÉS ORPHELINES (présentes uniquement dans une langue):")
        report_lines.append("-" * 70)
        for lang, keys in results['orphan_keys'].items():
            report_lines.append(f"  {lang.upper()}: {len(keys)} clé(s) orpheline(s)")
            if verbose:
                for key in keys:
                    report_lines.append(f"    - {key}")
            total_errors += len(keys)
        report_lines.append("")
    else:
        report_lines.append(f"{success_sym} Aucune clé orpheline")
        report_lines.append("")
    
    # Traductions vides
    if results['empty_translations']:
        report_lines.append(f"{warning_sym} TRADUCTIONS VIDES:")
        report_lines.append("-" * 70)
        for lang, keys in results['empty_translations'].items():
            report_lines.append(f"  {lang.upper()}: {len(keys)} traduction(s) vide(s)")
            if verbose:
                for key in keys:
                    report_lines.append(f"    - {key}")
            total_errors += len(keys)
        report_lines.append("")
    else:
        report_lines.append(f"{success_sym} Aucune traduction vide")
        report_lines.append("")
    
    # Traductions en fallback
    if results['fallback_translations']:
        report_lines.append(f"{warning_sym} TRADUCTIONS EN FALLBACK (clé = valeur):")
        report_lines.append("-" * 70)
        for lang, keys in results['fallback_translations'].items():
            report_lines.append(f"  {lang.upper()}: {len(keys)} traduction(s) en fallback")
            if verbose:
                for key in keys:
                    report_lines.append(f"    - {key}")
            total_errors += len(keys)
        report_lines.append("")
    else:
        report_lines.append(f"{success_sym} Aucune traduction en fallback")
        report_lines.append("")
    
    # Résumé
    report_lines.append("=" * 70)
    if total_errors == 0:
        report_lines.append(f"{success_sym} SUCCÈS: Toutes les traductions sont cohérentes!")
    else:
        report_lines.append(f"{error_sym} ÉCHEC: {total_errors} problème(s) détecté(s)")
    report_lines.append("=" * 70)
    
    return "\n".join(report_lines)


def validate_i18n(verbose: bool = False, use_ascii: bool = False) -> Tuple[bool, str]:
    """Valide la cohérence des fichiers i18n.
    
    Args:
        verbose: Si True, génère un rapport détaillé.
        use_ascii: Si True, utilise des caractères ASCII au lieu d'emojis.
        
    Returns:
        Tuple (is_valid, report) où is_valid est True si tout est cohérent,
        et report contient le rapport texte.
    """
    try:
        translations = load_all_translations()
        results = compare_translations(translations)
        report = generate_report(results, verbose=verbose, use_ascii=use_ascii)
        
        is_valid = (
            not results['missing_keys'] and
            not results['orphan_keys'] and
            not results['empty_translations'] and
            not results['fallback_translations']
        )
        
        return is_valid, report
    except Exception as e:
        error_sym = "[X]" if use_ascii else "❌"
        error_report = f"{error_sym} ERREUR LORS DE LA VALIDATION: {e}"
        return False, error_report