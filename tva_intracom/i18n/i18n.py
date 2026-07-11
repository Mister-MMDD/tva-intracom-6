import streamlit as st
import toml
from pathlib import Path
from functools import lru_cache

# Dossier contenant les traductions
I18N_DIR = Path(__file__).parent

@lru_cache(maxsize=None)
def load_translations(lang: str):
    """Charge le fichier TOML pour une langue donnée."""
    file_path = I18N_DIR / f"{lang}.toml"
    if not file_path.exists():
        # Fallback sur le français
        file_path = I18N_DIR / "fr.toml"
    
    try:
        return toml.load(file_path)
    except Exception:
        return {}

def get_text(key: str, **kwargs) -> str:
    """Récupère la traduction pour une clé donnée."""
    lang = st.session_state.get("language", "fr")
    translations = load_translations(lang)
    
    # Récupération de la valeur
    text = translations.get(key, key)
    
    if kwargs and isinstance(text, str):
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
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
