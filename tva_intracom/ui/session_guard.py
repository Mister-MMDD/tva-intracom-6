"""Purge de l'état de session « dérivé » quand aucun fichier n'est chargé.

BUGFIX (2026-10-02) : app.py purgeait TOUTE clé de `st.session_state` absente
de la whitelist à chaque run sans fichier uploadé — donc aussi l'état des
formulaires multi-étapes (stepper de création de SIREN, wizard d'onboarding),
qui n'ont pourtant aucun lien avec les fichiers. Comme ces composants
déclenchent un `st.rerun()` (run complet) à chaque changement d'étape, leur
étape courante et leurs saisies étaient effacées juste après le rendu :
  - stepper : retour à l'étape 1 avec « Le numéro SIREN est requis » ;
  - wizard : « Passer »/« Suivant » ramenaient à l'étape précédente.
Les préfixes ci-dessous sont épargnés par la purge. Toute nouvelle clé d'un
formulaire multi-étapes doit être ajoutée ici (voir tests/test_session_guard.py).
"""
from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from typing import Any

PROTECTED_KEY_PREFIXES: tuple[str, ...] = (
    # Stepper de création de SIREN (ui/sidebar.py)
    "siren_stepper_",
    "nom_new",
    "siren_new",
    "vat_countries_new",
    "vat_num_new_",
    "ioss_new",
    "ioss_own_active_new",
    "ddp_new",
    "oss_thr_new",
    "oss_thr_prevyear_new",
    # Onboarding : wizard (onboarding_wizard.py) + checklist (onboarding.py)
    "onboarding_",
    "_onboarding",
)


def purge_stale_session_keys(
    session_state: MutableMapping[str, Any], whitelist: Iterable[str]
) -> None:
    """Supprime les clés qui ne sont ni whitelistées ni protégées."""
    _keep = set(whitelist)
    for _key in list(session_state.keys()):
        if _key in _keep or str(_key).startswith(PROTECTED_KEY_PREFIXES):
            continue
        session_state.pop(_key, None)
