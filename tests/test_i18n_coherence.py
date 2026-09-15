"""Tests de cohérence des fichiers de traduction i18n.

Ces tests vérifient que toutes les langues ont exactement les mêmes clés
de traduction, sans variables orphelines ni traductions manquantes.
"""

import pytest
from tva_intracom.i18n.i18n_validator import (
    load_all_translations,
    get_all_keys,
    compare_translations,
    generate_report,
    validate_i18n,
    LANGUAGES
)


class TestLoadAllTranslations:
    """Tests pour la fonction load_all_translations."""
    
    def test_load_all_translations_success(self):
        """Le chargement de toutes les traductions doit réussir."""
        translations = load_all_translations()
        assert isinstance(translations, dict)
        assert len(translations) == len(LANGUAGES)
        for lang in LANGUAGES:
            assert lang in translations
            assert isinstance(translations[lang], dict)
    
    def test_load_all_translations_structure(self):
        """Les traductions chargées doivent avoir la structure attendue."""
        translations = load_all_translations()
        for lang, lang_translations in translations.items():
            # Vérifier que c'est un dictionnaire
            assert isinstance(lang_translations, dict)
            # Vérifier qu'il y a des clés
            assert len(lang_translations) > 0
            # Vérifier que les valeurs sont des chaînes
            for key, value in lang_translations.items():
                assert isinstance(key, str)
                assert isinstance(value, str)


class TestGetAllKeys:
    """Tests pour la fonction get_all_keys."""
    
    def test_get_all_keys_returns_set(self):
        """La fonction doit retourner un ensemble de clés."""
        translations = load_all_translations()
        all_keys = get_all_keys(translations)
        assert isinstance(all_keys, set)
    
    def test_get_all_keys_non_empty(self):
        """L'ensemble des clés ne doit pas être vide."""
        translations = load_all_translations()
        all_keys = get_all_keys(translations)
        assert len(all_keys) > 0
    
    def test_get_all_keys_completeness(self):
        """Toutes les clés de chaque langue doivent être dans l'ensemble."""
        translations = load_all_translations()
        all_keys = get_all_keys(translations)
        for lang_translations in translations.values():
            for key in lang_translations.keys():
                assert key in all_keys


class TestCompareTranslations:
    """Tests pour la fonction compare_translations."""
    
    def test_compare_translations_structure(self):
        """La fonction doit retourner la structure attendue."""
        translations = load_all_translations()
        results = compare_translations(translations)
        assert isinstance(results, dict)
        assert 'missing_keys' in results
        assert 'orphan_keys' in results
        assert 'empty_translations' in results
        assert 'fallback_translations' in results
    
    def test_compare_translations_all_languages_have_same_keys(self):
        """Toutes les langues doivent avoir exactement les mêmes clés."""
        translations = load_all_translations()
        results = compare_translations(translations)
        
        # Vérifier qu'il n'y a pas de clés manquantes
        assert len(results['missing_keys']) == 0, (
            f"Clés manquantes détectées: {results['missing_keys']}"
        )
        
        # Vérifier qu'il n'y a pas de clés orphelines
        assert len(results['orphan_keys']) == 0, (
            f"Clés orphelines détectées: {results['orphan_keys']}"
        )
    
    def test_compare_translations_no_empty_translations(self):
        """Il ne doit pas y avoir de traductions vides."""
        translations = load_all_translations()
        results = compare_translations(translations)
        
        assert len(results['empty_translations']) == 0, (
            f"Traductions vides détectées: {results['empty_translations']}"
        )
    
    def test_compare_translations_no_fallback_translations(self):
        """Il ne doit pas y avoir de traductions en fallback (clé = valeur)."""
        translations = load_all_translations()
        results = compare_translations(translations)
        
        assert len(results['fallback_translations']) == 0, (
            f"Traductions en fallback détectées: {results['fallback_translations']}"
        )


class TestGenerateReport:
    """Tests pour la fonction generate_report."""
    
    def test_generate_report_returns_string(self):
        """La fonction doit retourner une chaîne de caractères."""
        translations = load_all_translations()
        results = compare_translations(translations)
        report = generate_report(results)
        assert isinstance(report, str)
    
    def test_generate_report_contains_header(self):
        """Le rapport doit contenir l'en-tête."""
        translations = load_all_translations()
        results = compare_translations(translations)
        report = generate_report(results)
        assert "RAPPORT DE COHÉRENCE I18N" in report
    
    def test_generate_report_verbose_mode(self):
        """Le mode verbose doit inclure plus de détails."""
        translations = load_all_translations()
        results = compare_translations(translations)
        
        report_normal = generate_report(results, verbose=False)
        report_verbose = generate_report(results, verbose=True)
        
        # En mode normal sans erreurs, les rapports doivent être similaires
        # Si on voulait tester avec des erreurs, on pourrait vérifier la différence
        assert isinstance(report_verbose, str)
        assert len(report_verbose) >= len(report_normal)
    
    def test_generate_report_ascii_mode(self):
        """Le mode ASCII doit utiliser des caractères ASCII au lieu d'emojis."""
        translations = load_all_translations()
        results = compare_translations(translations)
        
        report_unicode = generate_report(results, use_ascii=False)
        report_ascii = generate_report(results, use_ascii=True)
        
        # Vérifier que le mode ASCII n'a pas d'emojis
        assert isinstance(report_ascii, str)
        assert "✅" not in report_ascii
        assert "❌" not in report_ascii
        assert "⚠️" not in report_ascii
        assert "[OK]" in report_ascii or "[X]" in report_ascii


class TestValidateI18n:
    """Tests pour la fonction validate_i18n."""
    
    def test_validate_i18n_returns_tuple(self):
        """La fonction doit retourner un tuple (is_valid, report)."""
        is_valid, report = validate_i18n()
        assert isinstance(is_valid, bool)
        assert isinstance(report, str)
    
    def test_validate_i18n_success_case(self):
        """La validation doit réussir si les traductions sont cohérentes."""
        is_valid, report = validate_i18n()
        assert is_valid is True, f"Validation échouée mais devrait réussir:\n{report}"
        assert "SUCCÈS" in report or "SUCCESS" in report
    
    def test_validate_i18n_includes_report(self):
        """Le rapport doit être inclus dans le résultat."""
        is_valid, report = validate_i18n()
        assert len(report) > 0
        assert "RAPPORT DE COHÉRENCE I18N" in report
    
    def test_validate_i18n_verbose_mode(self):
        """Le mode verbose doit fonctionner."""
        is_valid, report = validate_i18n(verbose=True)
        assert isinstance(is_valid, bool)
        assert isinstance(report, str)
        assert len(report) > 0
    
    def test_validate_i18n_ascii_mode(self):
        """Le mode ASCII doit fonctionner."""
        is_valid, report = validate_i18n(use_ascii=True)
        assert isinstance(is_valid, bool)
        assert isinstance(report, str)
        assert len(report) > 0
        # Vérifier qu'il n'y a pas d'emojis
        assert "✅" not in report
        assert "❌" not in report


class TestIntegration:
    """Tests d'intégration complets."""
    
    def test_all_languages_have_identical_key_count(self):
        """Toutes les langues doivent avoir le même nombre de clés."""
        translations = load_all_translations()
        key_counts = [len(lang_translations) for lang_translations in translations.values()]
        
        # Toutes les langues doivent avoir le même nombre de clés
        assert len(set(key_counts)) == 1, (
            f"Les langues n'ont pas le même nombre de clés: {dict(zip(LANGUAGES, key_counts))}"
        )
    
    def test_no_missing_keys_across_languages(self):
        """Il ne doit y avoir aucune clé manquante entre les langues."""
        translations = load_all_translations()
        all_keys = get_all_keys(translations)
        
        for lang, lang_translations in translations.items():
            lang_keys = set(lang_translations.keys())
            missing = all_keys - lang_keys
            assert len(missing) == 0, (
                f"La langue {lang} a {len(missing)} clé(s) manquante(s): {sorted(missing)}"
            )
    
    def test_common_keys_consistency(self):
        """Les clés communes doivent être présentes dans toutes les langues."""
        translations = load_all_translations()
        
        # Prendre les clés de la première langue comme référence
        reference_lang = LANGUAGES[0]
        reference_keys = set(translations[reference_lang].keys())
        
        for lang in LANGUAGES[1:]:
            lang_keys = set(translations[lang].keys())
            assert reference_keys == lang_keys, (
                f"Les clés de {lang} ne correspondent pas à celles de {reference_lang}. "
                f"Manquantes dans {lang}: {sorted(reference_keys - lang_keys)}. "
                f"En trop dans {lang}: {sorted(lang_keys - reference_keys)}."
            )


# Test principal qui résume tout
def test_i18n_coherence_comprehensive():
    """Test complet de cohérence i18n - point d'entrée principal."""
    is_valid, report = validate_i18n(verbose=True)
    
    # Afficher le rapport pour debugging
    print("\n" + report)
    
    # Le test doit passer si la validation réussit
    assert is_valid, f"Test de cohérence i18n échoué:\n{report}"