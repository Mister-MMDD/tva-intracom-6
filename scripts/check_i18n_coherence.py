#!/usr/bin/env python3
"""Script standalone pour vérifier la cohérence des fichiers i18n.

Ce script peut être exécuté manuellement pour vérifier que toutes les langues
ont les mêmes clés de traduction, sans variables orphelines ni traductions manquantes.

Usage:
    python check_i18n_coherence.py [--verbose] [--output-file FILE]
"""

import argparse
import json
import sys
from pathlib import Path

# Ajouter le répertoire parent au path pour importer le module
sys.path.insert(0, str(Path(__file__).parent))

from tva_intracom.i18n.i18n_validator import (
    load_all_translations,
    compare_translations,
    generate_report,
    validate_i18n,
    LANGUAGES
)


def parse_arguments():
    """Parse les arguments de ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Vérifier la cohérence des fichiers de traduction i18n"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Afficher un rapport détaillé avec toutes les clés problématiques"
    )
    parser.add_argument(
        "--output-file", "-o",
        type=str,
        help="Exporter le rapport dans un fichier (JSON ou texte selon l'extension)"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["text", "json"],
        default="text",
        help="Format de sortie (text ou json). Défaut: text"
    )
    parser.add_argument(
        "--ascii", "-a",
        action="store_true",
        help="Utiliser des caractères ASCII au lieu d'emojis (utile pour Windows)"
    )
    return parser.parse_args()


def export_json_report(results: dict, output_file: str, use_ascii: bool = False):
    """Exporte le rapport en format JSON."""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    success_sym = "[OK]" if use_ascii else "✅"
    print(f"{success_sym} Rapport JSON exporté dans: {output_file}")


def export_text_report(report: str, output_file: str, use_ascii: bool = False):
    """Exporte le rapport en format texte."""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    success_sym = "[OK]" if use_ascii else "✅"
    print(f"{success_sym} Rapport texte exporté dans: {output_file}")


def main():
    """Fonction principale du script."""
    args = parse_arguments()
    
    # Déterminer si on utilise le mode ASCII
    use_ascii = args.ascii or sys.platform == "win32"
    
    print(f"{'[?]' if use_ascii else '🔍'} Vérification de la cohérence i18n...")
    print(f"{'[+]' if use_ascii else '📂'} Langues vérifiées: {', '.join(LANGUAGES)}")
    print()
    
    try:
        # Valider les traductions
        is_valid, report = validate_i18n(verbose=args.verbose, use_ascii=use_ascii)
        
        # Afficher le rapport en console
        print(report)
        print()
        
        # Exporter si demandé
        if args.output_file:
            output_path = Path(args.output_file)
            
            if args.format == "json" or output_path.suffix == ".json":
                # Récupérer les résultats détaillés pour l'export JSON
                translations = load_all_translations()
                results = compare_translations(translations)
                
                # Ajouter des informations supplémentaires
                json_results = {
                    "validation": {
                        "is_valid": is_valid,
                        "languages_checked": LANGUAGES,
                        "total_languages": len(LANGUAGES)
                    },
                    "results": results,
                    "summary": {
                        "total_errors": sum(
                            len(v) for v in results.values() if isinstance(v, dict)
                        )
                    }
                }
                export_json_report(json_results, args.output_file, use_ascii)
            else:
                export_text_report(report, args.output_file, use_ascii)
        
        # Code de sortie
        if is_valid:
            print(f"{'[OK]' if use_ascii else '✅'} Validation réussie!")
            sys.exit(0)
        else:
            print(f"{'[X]' if use_ascii else '❌'} Validation échouée!")
            sys.exit(1)
            
    except FileNotFoundError as e:
        print(f"{'[X]' if use_ascii else '❌'} Erreur: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"{'[X]' if use_ascii else '❌'} Erreur inattendue: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()