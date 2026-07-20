"""Utilitaire pour distinguer un `st.rerun()` interne (changement de pays
d'origine, TTL cache, etc.) d'un véritable retrait de fichier par
l'utilisateur.

Problème corrigé : le filet de sécurité d'`app.py` (qui recharge les fichiers
depuis `st.session_state["_last_uploaded_files_bytes"]` quand le widget
`st.file_uploader` ressort vide après un rerun déclenché en plein rendu de la
sidebar) ne faisait pas la différence entre :
  - un rerun interne (le fichier est toujours là, juste pas encore revu par
    le widget à ce stade du script) ;
  - un vrai retrait du fichier par l'utilisateur (clic sur la croix).

Dans les deux cas, `uploaded_files` ressort vide du widget. Sans distinction,
le filet de sécurité ressuscitait TOUJOURS le fichier précédent, y compris
après un retrait volontaire — d'où les tableaux et la période qui restaient
affichés après suppression du fichier.

Tout code qui déclenche un `st.rerun()` SANS que l'utilisateur n'ait touché
au file_uploader doit passer par `preserve_upload_rerun()` plutôt que
`st.rerun()` directement, afin de signaler à `app.py` qu'il doit réutiliser
le cache de fichier au prochain passage.
"""

from __future__ import annotations

import streamlit as st

_PRESERVE_FLAG = "_preserve_upload_on_rerun"


def preserve_upload_rerun() -> None:
    """Comme `st.rerun()`, mais signale que ce rerun n'est PAS un retrait de
    fichier par l'utilisateur : le filet de sécurité d'app.py doit réutiliser
    les octets déjà en cache plutôt que de traiter le fichier comme retiré."""
    st.session_state[_PRESERVE_FLAG] = True
    st.rerun()


def consume_preserve_flag() -> bool:
    """A appeler une seule fois par run, côté app.py, avant de décider si un
    `uploaded_files` vide correspond à un vrai retrait ou à un rerun interne."""
    return st.session_state.pop(_PRESERVE_FLAG, False)
