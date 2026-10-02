"""Test pour valider le fonctionnement du stepper de création de SIREN.

Vérifie que :
1. Les données saisies à l'étape 1 (Informations de base : Nom, SIREN) et l'étape 2 (Immatriculations TVA)
   sont conservées en session_state lors de la navigation vers l'étape 3 (Options avancées).
2. Le clic sur "Enregistrer ce SIREN" à l'étape 3 réussit sans bloquer sur "Le numéro SIREN est requis.".
3. La navigation arrière via "Précédent" conserve bien les saisies.
"""
from streamlit.testing.v1 import AppTest


def _create_apptest_for_stepper():
    _SCRIPT = "tests/_sidebar_app_script.py"
    at = AppTest.from_file(_SCRIPT, default_timeout=30)
    scn = {"sirens": [], "quota": 5, "_new": True}
    at.session_state["_scn"] = scn
    at.run()
    return at


def test_siren_stepper_full_flow():
    """Test du flux complet de création d'un SIREN via le stepper 3 étapes."""
    at = _create_apptest_for_stepper()
    
    # Étape 1/3
    assert len(at.text_input) >= 2
    at.text_input(key="nom_new").set_value("ACME SARL")
    at.text_input(key="siren_new").set_value("123456789")
    at.button(key="stepper_next").click()
    at.run()
    
    # Étape 2/3
    assert at.session_state["siren_stepper_step"] == 1
    at.text_input(key="vat_num_new_FR").set_value("FR123456789")
    at.button(key="stepper_next").click()
    at.run()
    
    # Étape 3/3
    assert at.session_state["siren_stepper_step"] == 2
    # Vérifier que le SIREN est bien préservé dans la structure de données
    data = at.session_state["siren_stepper_data"]
    assert data.get("siren_new") == "123456789"
    assert data.get("nom_new") == "ACME SARL"
    assert data.get("vat_numbers", {}).get("FR") == "FR123456789"
    
    # Cliquer sur "Enregistrer ce SIREN"
    at.button(key="stepper_finish").click()
    at.run()
    
    # Doit avoir réinitialisé le stepper après succès
    assert at.session_state["siren_stepper_step"] == 0
    assert "siren_select_box" in at.session_state and at.session_state["siren_select_box"] == "123456789"


def test_siren_stepper_navigation_back_and_forth():
    """Test de la navigation arrière (Précédent) puis avant (Suivant) dans le stepper."""
    at = _create_apptest_for_stepper()
    
    # Étape 1/3 : Saisie
    at.text_input(key="nom_new").set_value("TEST COMP")
    at.text_input(key="siren_new").set_value("987654321")
    at.button(key="stepper_next").click()
    at.run()
    
    # Étape 2/3 : Saisie
    at.text_input(key="vat_num_new_FR").set_value("FR987654321")
    
    # Retour à l'étape 1 via Précédent
    at.button(key="stepper_prev").click()
    at.run()
    
    assert at.session_state["siren_stepper_step"] == 0
    # Les valeurs saisies doivent réapparaître dans les champs
    assert at.text_input(key="nom_new").value == "TEST COMP"
    assert at.text_input(key="siren_new").value == "987654321"
    
    # Ré-avancer à l'étape 2
    at.button(key="stepper_next").click()
    at.run()
    
    assert at.session_state["siren_stepper_step"] == 1
    assert at.text_input(key="vat_num_new_FR").value == "FR987654321"
