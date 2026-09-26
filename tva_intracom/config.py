import os
try:
    import streamlit as st
except ImportError:
    st = None

from typing import Any

def get_secret(key: str, default: Any = None) -> Any:
    """
    Récupère une configuration depuis st.secrets (Streamlit Cloud / Local)
    ou depuis os.environ (Railway / Vercel / Docker), sans lever d'erreur
    si les secrets Streamlit ne sont pas initialisés.
    """
    if st is not None:
        try:
            # st.secrets.get() peut lever StreamlitSecretNotFoundError si aucun fichier de secrets n'existe
            val = st.secrets.get(key)
            if val is not None:
                return val
        except Exception:
            # On ignore l'erreur et on bascule sur os.environ
            pass
    
    return os.environ.get(key, default)
