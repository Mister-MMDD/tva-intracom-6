import streamlit as st
import toml
from pathlib import Path
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)

# Dossier contenant les traductions (chemin absolu pour éviter les surprises en prod)
I18N_DIR = Path(__file__).resolve().parent

@lru_cache(maxsize=None)
def load_translations(lang: str):
    """Charge le fichier TOML pour une langue donnée."""
    file_path = I18N_DIR / f"{lang}.toml"
    
    if not file_path.exists():
        logger.warning(f"Translation file not found: {file_path}. Falling back to fr.toml")
        file_path = I18N_DIR / "fr.toml"
    
    if not file_path.exists():
        logger.error(f"Critical: Translation file not found: {file_path}")
        return {}

    try:
        # Lecture explicite en UTF-8 pour éviter les problèmes d'encodage selon l'OS
        content = file_path.read_text(encoding="utf-8")
        return toml.loads(content)
    except Exception as e:
        logger.error(f"Error parsing TOML file {file_path}: {e}")
        # En cas d'échec de parsing, on renvoie un dict vide pour éviter le crash complet
        return {}

def get_text(key: str, **kwargs) -> str:
    """Récupère la traduction pour une clé donnée."""
    # On s'assure d'avoir une langue par défaut si session_state n'est pas encore prêt
    try:
        lang = st.session_state.get("language", "fr")
    except Exception:
        lang = "fr"
        
    translations = load_translations(lang)
    
    # Récupération de la valeur, fallback sur la clé elle-même
    text = translations.get(key, key)
    
    if kwargs and isinstance(text, str):
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError) as e:
            logger.warning(f"Format error for key '{key}': {e}")
            return text
            
    return text

_ = get_text

def init_i18n():
    """Initialise la langue dans la session."""
    if "language" not in st.session_state:
        st.session_state["language"] = "fr"

def language_selector():
    """Affiche un sélecteur de langue dans la barre latérale."""
    langs = {
        "fr": "🇫🇷 Français",
        "en": "🇬🇧 English",
    }
    
    current_lang = st.session_state.get("language", "fr")
    options = list(langs.keys())
    try:
        index = options.index(current_lang)
    except ValueError:
        index = 0
        
    new_lang = st.sidebar.selectbox(
        _("language"),
        options=options,
        format_func=lambda x: langs.get(x),
        index=index,
        key="language_selector_ui"
    )
    
    if new_lang != current_lang:
        st.session_state["language"] = new_lang
        st.rerun()
