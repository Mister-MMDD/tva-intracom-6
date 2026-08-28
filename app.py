"""Application Streamlit — Moteur TVA Intracommunautaire."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import tempfile
import gzip
import logging
from typing import Optional, Callable
from decimal import Decimal
from datetime import datetime

import streamlit as st
import pandas as pd

from tva_intracom.historical_rates_widget import render_historical_rates_alert
from tva_intracom.i18n import _, init_i18n, language_selector, country_label
from tva_intracom.ecb_rates import get_rate as _ecb_get_rate
from tva_intracom.engine import compute_all_with_vies
from tva_intracom.ui.background_calc import (
    start_background_job,
    get_job_state,
    clear_job,
    render_job_progress,
)
from tva_intracom.report import build_report
from tva_intracom.rates import is_eu, COUNTRY_CURRENCIES, CURRENCY_SYMBOLS
from tva_intracom.ui.theme import apply_theme
from tva_intracom.ui.formatting import _fmt
from tva_intracom import auth as tva_auth
from tva_intracom import ecb_rates as _tva_ecb_rates
from tva_intracom import billing as _tva_billing
from tva_intracom import vies_engine as _tva_vies_engine
from tva_intracom.ui.auth_flow import ensure_cookie_manager, run_auth_flow
from tva_intracom.ui.rerun_utils import preserve_upload_rerun, consume_preserve_flag
from tva_intracom.ui.sidebar import render_sidebar
from tva_intracom.ui.files import _CachedUploadedFile, _upload_sig
from tva_intracom.ui.calc_cache import CalcCacheState
from tva_intracom.ui.display_mode import ensure_display_mode, is_detailed, render_mode_toggle
from tva_intracom.ui.onboarding import (
    ensure_onboarding_state,
    render_onboarding_banner,
    compute_pulse_target,
)

_ZERO = Decimal("0.00")

# Initialisation I18N
init_i18n()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# --- Fermeture des connexions DB idle laissées par le run précédent ---
#
# 4 pools psycopg2 globaux (auth.py, ecb_rates.py, billing.py, vies_engine.py)
# gardaient au moins 1 connexion ouverte en permanence vers le pooler
# Supabase (aws-0-eu-west-1.pooler.supabase.com), dès leur première
# utilisation, jusqu'à l'arrêt du process — indépendamment du nombre
# d'utilisateurs actifs. Ce trafic keepalive TCP empêchait Railway de
# détecter l'inactivité réelle et bloquait le scale-to-zero serverless.
#
# IMPORTANT : ce nettoyage doit s'exécuter AVANT run_auth_flow(), pas après.
# run_auth_flow() contient plusieurs st.stop() (écran de login, callback
# OAuth...) : pour un visiteur non authentifié (le cas de la nuit, sans
# personne connecté), le script s'arrête bien avant d'atteindre la fin du
# fichier — un nettoyage placé en fin de script n'était donc JAMAIS atteint
# dans ce cas précis, alors même qu'une connexion avait déjà été ouverte par
# la simple vérification de session/cookie. En le plaçant ici, chaque
# nouveau run ferme d'abord ce qui restait du run précédent, avant même de
# commencer l'auth — une connexion ne peut donc plus jamais vivre plus
# longtemps qu'un seul run (quelques centaines de ms), quel que soit
# l'endroit où le script s'arrête ensuite.

# Pas de garde ici (voir README - évolution.md, 2026-08-14) : close_idle_connections()
# ne ferme que la connexion mise en cache par threading.local() sur LE THREAD APPELANT
# (ce run Streamlit), jamais celle d'un job de calcul en cours dans son propre thread
# (background_calc.py, thread bgjob-*). Un garde any_job_running() ne protégerait donc
# rien et retarderait seulement, sans raison, le nettoyage des connexions d'utilisateurs
# par ailleurs inactifs pendant qu'un job tourne n'importe où sur le process.
for _close_fn in (
        tva_auth.close_idle_connections,
        _tva_ecb_rates.close_idle_connections,
        _tva_billing.close_idle_connections,
        _tva_vies_engine.close_idle_connections,
):
    try:
        _close_fn()
    except Exception:
        pass

# =============================================================================
# PAGE CONFIG + PURGE CACHE MAL-PREFIXÉ (une fois par session)
# =============================================================================
apply_theme()

cookie_manager = ensure_cookie_manager()

# Le sélecteur de langue doit être utilisable AVANT la connexion (écran de
# lien magique compris) — appelé ici, avant run_auth_flow()/st.stop(),
# contrairement à l'ancien emplacement (après l'auth) qui le rendait
# invisible tant que l'utilisateur n'était pas connecté.
language_selector()

st.title(f"🇪🇺 {_('title')}")

_auth_ctx = run_auth_flow(cookie_manager)
if _auth_ctx is None:
    st.stop()

_current_user = _auth_ctx.current_user
_APP_BASE_URL = _auth_ctx.app_base_url
_vies_scope_id = _auth_ctx.vies_scope_id
_stripe_success_url = _auth_ctx.stripe_success_url
_stripe_cancel_url = _auth_ctx.stripe_cancel_url

# NOTE : le mécanisme applicatif de "veille" (détection d'inactivité côté
# app + purge mémoire + écran ?sleep=1) a été retiré volontairement.
#
# Toute vérification périodique — même très espacée (fragment run_every,
# polling JS régulier, etc.) — génère du trafic WebSocket réel qui empêche
# Railway de considérer le service comme réellement idle, et bloque donc le
# scale-to-zero serverless (Settings > Deploy > "Enable Serverless").
#
# Railway serverless gère maintenant cette responsabilité au niveau infra :
# le container entier est coupé après une période d'inactivité réelle (RAM
# rendue à 0, pas juste un gc.collect()/malloc_trim applicatif), et redémarre
# à la prochaine requête entrante (mise en file d'attente le temps du
# redémarrage). C'est strictement supérieur à toute purge applicative, et
# ne fonctionne QUE si l'app elle-même reste silencieuse quand personne ne
# l'utilise — d'où la suppression de tout polling ici.

# --- Synchro langue <-> compte ---
# `language_selector()` (appelé plus haut, avant l'authentification, pour que
# l'écran de connexion lui-même soit localisé) ne connaît que la session
# Streamlit, pas encore le compte. Une fois l'utilisateur identifié :
# - Première fois que ce compte est vu dans cette session : on applique sa
#   langue sauvegardée (tva_users.language) si elle diffère de la langue de
#   session actuelle, puis on ne le refait plus (pour ne pas écraser un
#   changement manuel ultérieur de l'utilisateur dans la même session).
# - Sinon, si la langue de session a changé depuis (l'utilisateur vient
#   d'utiliser le sélecteur) : on persiste ce choix sur le compte.
_sess_lang = st.session_state.get("language", "fr")
ensure_display_mode()
_sess_display_mode = st.session_state.get("display_mode", "simple")

# Un seul indicateur "première vue de ce compte dans cette session", partagé
# par toutes les préférences synchronisées (langue + mode d'affichage) — lu
# UNE SEULE FOIS avant de traiter quoi que ce soit, pour ne pas que le
# traitement de la langue positionne déjà le flag avant que le mode
# d'affichage n'ait pu le lire à son tour.
_is_first_prefs_sync = st.session_state.get("_prefs_synced_user") != _current_user.id
_needs_prefs_rerun = False

if _is_first_prefs_sync:
    if _current_user.language and _current_user.language != _sess_lang:
        st.session_state["language"] = _current_user.language
        _needs_prefs_rerun = True
    if _current_user.display_mode and _current_user.display_mode != _sess_display_mode:
        st.session_state["display_mode"] = _current_user.display_mode
        _needs_prefs_rerun = True
    st.session_state["_prefs_synced_user"] = _current_user.id
    if _needs_prefs_rerun:
        preserve_upload_rerun()
else:
    if _current_user.language != _sess_lang:
        tva_auth.set_language(_current_user.id, _sess_lang)
        _current_user.language = _sess_lang
    if _current_user.display_mode != _sess_display_mode:
        try:
            tva_auth.set_display_mode(_current_user.id, _sess_display_mode)
            _current_user.display_mode = _sess_display_mode
        except Exception:
            # Robustesse : set_display_mode() est du code neuf (2026-08-21),
            # jamais éprouvé contre la base de production, contrairement à
            # set_language() ci-dessus. Une exception DB ici cassait TOUTE
            # la page au moment précis du basculement de mode (script
            # interrompu en plein milieu), sans forcément produire de
            # traceback visible selon la configuration Streamlit — plausible
            # explication au "bug d'affichage" signalé. On log l'échec et on
            # continue le run avec le mode déjà appliqué en session (juste
            # non persisté en base pour cette fois) plutôt que de planter.
            logger.exception(
                "PREFS_SYNC échec de persistance display_mode en base (user=%s, mode=%s) "
                "— le mode reste appliqué dans cette session, non sauvegardé pour la prochaine connexion.",
                _current_user.id, _sess_display_mode,
            )


# BUGFIX : la sidebar (rendue ci-dessous) affiche `_period_label` tel qu'il
# était à LA FIN DU RUN PRÉCÉDENT (upload/retrait de fichier/calcul n'ont pas
# encore eu lieu à ce stade). On mémorise cette valeur "affichée" pour pouvoir
# la comparer, en toute fin de run, à la valeur réellement à jour — et ne
# forcer qu'UN SEUL rerun de synchro si elles diffèrent (voir plus bas). Sans
# ça, un simple changement d'onglet Streamlit (qui ne déclenche aucun rerun
# Python) laissait la sidebar bloquée indéfiniment sur l'ancien état, tout
# comme un retrait de fichier sans interaction serveur ultérieure.
_period_label_shown_by_sidebar = st.session_state.get("_period_label", "")

# Cible du guidage visuel "Lighthouse" (voir ui/onboarding.py::compute_pulse_target) :
# calculée à la fin du run PRÉCÉDENT, relue ici avant render_sidebar() pour que le
# marqueur CSS puisse être placé au bon endroit dans la sidebar dès ce run.
_onboarding_pulse_target = st.session_state.get("_onboarding_pulse_target")

_sb = render_sidebar(_auth_ctx, pulse_target=_onboarding_pulse_target)
file_format = _sb.file_format
enable_vies = _sb.enable_vies
on_invalid_behavior = _sb.on_invalid_behavior
convert_fx = _sb.convert_fx
encoding = _sb.encoding
asin_to_category = _sb.asin_to_category
ioss_number = _sb.ioss_number
seller_is_importer = _sb.seller_is_importer
apply_fr_under_threshold = _sb.apply_fr_under_threshold
ioss_own_number_active = _sb.ioss_own_number_active
countries_with_vat = _sb.countries_with_vat
nom_entreprise = _sb.nom_entreprise
siren_entreprise = _sb.siren_entreprise
tva_fr = _sb.tva_fr
local_vat_numbers = _sb.local_vat_numbers
oss_period = _sb.oss_period
_siren_quota_status = _sb.siren_quota_status
home_country = _sb.home_country
display_currency = _sb.display_currency

# --- Configuration de la monnaie de référence ---
# `display_currency` (sélecteur sous le pays d'origine, voir ui/sidebar.py)
# permet de choisir une devise d'affichage indépendante du pays d'origine —
# ex. rester en EUR tout en ayant choisi la Pologne comme pays d'origine pour
# la classification fiscale. "DEFAULT" retombe sur la devise du pays
# d'origine choisi (comportement historique). N'affecte jamais la devise de
# calcul du moteur (toujours EUR) ni les déclarations légales.
if display_currency and display_currency != "DEFAULT":
    target_currency = display_currency
else:
    target_currency = COUNTRY_CURRENCIES.get(home_country, "EUR")
currency_symbol = CURRENCY_SYMBOLS.get(target_currency, "€")
st.session_state["target_currency"] = target_currency
st.session_state["currency_symbol"] = currency_symbol

# =============================================================================
# BARRE DE STATUT PERSISTANTE (fichier / période / calcul / mode d'affichage)
# =============================================================================
# Toujours rendue, fichier chargé ou non — contrairement à l'ancien toggle
# Simple/Détaillé (ex-L874, déplacé ici via render_mode_toggle) qui n'existait
# qu'après un calcul complet. `ensure_display_mode()` doit être appelé avant
# toute lecture de is_detailed() plus bas dans ce script (sidebar.py et
# telechargements.py lisent directement session_state, donc l'ordre exact de
# CET appel importe peu pour eux, mais render_mode_toggle() l'appelle aussi
# en interne par sécurité — idempotent).
ensure_display_mode()

_status_file_count = st.session_state.get("_status_bar_file_count", 0)
_status_period = st.session_state.get("_period_label", "")
_status_has_results = bool(CalcCacheState.load().results)

# --- Onboarding guidé (checklist) ---
# Chaque signal reflète un champ RÉELLEMENT enregistré/saisi côté
# render_sidebar() (mêmes valeurs que celles utilisées pour le calcul) —
# aucun nouveau champ, aucune copie parallèle. Ce flag d'étape ne rentre
# dans aucun calc_key/parse_key (voir ui/onboarding.py) : la checklist ne
# peut donc pas, par construction, déclencher de recalcul.
_ob_entreprise_ok = bool(
    _siren_quota_status and _siren_quota_status.registered_count > 0
    and nom_entreprise and siren_entreprise
)
_ob_tva_local_ok = bool(tva_fr)
_ob_ioss_filled = bool(ioss_number or ioss_own_number_active)
ensure_onboarding_state(
    _current_user,
    entreprise_ok=_ob_entreprise_ok,
    tva_local_ok=_ob_tva_local_ok,
    upload_ok=_status_has_results,
)

# Mémorise la cible du prochain "Lighthouse" (voir plus haut, avant
# render_sidebar).
st.session_state["_onboarding_pulse_target"] = compute_pulse_target(
    step=st.session_state.get("_onboarding_step", "done"),
    entreprise_ok=_ob_entreprise_ok,
    tva_local_ok=_ob_tva_local_ok,
    upload_ok=_status_has_results,
)

_status_dot_class = "ok" if _status_has_results else ("pending" if _status_file_count else "off")
_status_calc_text = (
    _("status_bar_status_ready") if _status_has_results else _("status_bar_status_none")
)
_status_file_value = (
    _("status_bar_files_count", count=_status_file_count) if _status_file_count
    else _("status_bar_no_file")
)

_status_col_bar, _status_col_toggle = st.columns([3, 1])
with _status_col_bar:
    st.markdown(
        f"""
        <div class="status-bar">
            <div class="status-bar-item">
                <span class="status-bar-label">📁 {_('status_bar_file_label')} :</span>
                <span class="status-bar-value">{_status_file_value}</span>
            </div>
            <span class="status-bar-sep">|</span>
            <div class="status-bar-item">
                <span class="status-bar-label">🗓️ {_('status_bar_period_label')} :</span>
                <span class="status-bar-value">{_status_period or _('status_bar_period_pending')}</span>
            </div>
            <span class="status-bar-sep">|</span>
            <div class="status-bar-item">
                <span class="status-bar-dot {_status_dot_class}"></span>
                <span class="status-bar-value">{_status_calc_text}</span>
            </div>
        </div>

        """,
        unsafe_allow_html=True,
    )
with _status_col_toggle:
    render_mode_toggle()

render_onboarding_banner(
    _current_user,
    entreprise_ok=_ob_entreprise_ok,
    tva_local_ok=_ob_tva_local_ok,
    ioss_filled=_ob_ioss_filled,
    upload_ok=_status_has_results,
)

# =============================================================================
# UPLOAD
# =============================================================================
if _onboarding_pulse_target == "upload":
    with st.container(key="onb_pulse_upload"):
        pass
uploaded_files = st.file_uploader(
    _("upload_label"),
    type=["csv","tsv","txt"],
    accept_multiple_files=True,
    help=_("upload_help"),
    # Clé stable et indépendante de la langue : sans elle, l'identité du
    # widget est dérivée de son label — qui change de texte selon la langue
    # (i18n). Streamlit traitait alors un changement de langue comme un
    # NOUVEAU widget et vidait les fichiers déjà chargés. Avec une clé fixe,
    # les fichiers restent chargés quelle que soit la langue affichée.
    key="main_file_uploader",
)

# Cache/signature des fichiers uploadés (_CachedUploadedFile, _upload_sig) :
# voir tva_intracom/ui/files.py pour le détail (extrait de app.py, aucune
# modification de comportement).
_preserve_upload_this_run = consume_preserve_flag()

if uploaded_files:
    # ── Vérification technique de la taille (100 Mo par fichier) ────────────
    # Streamlit limite l'upload au niveau serveur (config.toml), mais on
    # rajoute une sécurité explicite ici pour informer l'utilisateur si un
    # fichier dépasse notre limite métier de 100 Mo.
    _OVERSIZED = [f.name for f in uploaded_files if f.size > 100 * 1024 * 1024]
    if _OVERSIZED:
        st.error(_("files_too_large_error", files=", ".join(f"`{n}`" for n in _OVERSIZED), max_mb=100))
        st.stop()

    # On ne recompresse que si le jeu de fichiers a réellement changé
    # (nom + taille). Sans cette garde, Streamlit ré-exécute tout le script
    # à chaque interaction (changement d'onglet, filtre, curseur...) tant
    # que les fichiers restent dans le widget, et ce bloc gzip.compress()
    # (coûteux en CPU sur de gros fichiers) tournait donc à chaque rerun
    # pour rien, alors que le contenu déjà en cache est strictement
    # identique la plupart du temps.
    #
    # Clé (name, size) plutôt que name seul : deux fichiers différents
    # portant le même nom (ex: deux exports "rapport.csv" de tailles
    # différentes) ne doivent PAS s'écraser dans ce cache — sinon l'un des
    # deux disparaît (ou est remplacé par le contenu de l'autre) au premier
    # rerun interne (ex: changement de langue), voir _CachedUploadedFile
    # ci-dessous qui reconstruit uploaded_files à partir de ce cache.
    _new_signature = {_upload_sig(f) for f in uploaded_files}
    _cached = st.session_state.get("_last_uploaded_files_bytes")
    _cached_signature = set(_cached.keys()) if _cached else set()
    if _cached is None or _new_signature != _cached_signature:
        st.session_state["_last_uploaded_files_bytes"] = {
            _upload_sig(f): gzip.compress(f.getvalue(), compresslevel=6)
            for f in uploaded_files
        }
elif _preserve_upload_this_run and st.session_state.get("_last_uploaded_files_bytes"):
    uploaded_files = [
        _CachedUploadedFile(_name, _compressed, _size, _hash)
        for (_name, _size, _hash), _compressed in st.session_state["_last_uploaded_files_bytes"].items()
    ]
else:
    # Vrai retrait de fichier (ou aucun fichier n'a jamais été chargé) :
    # on purge tout l'état dérivé pour que les tableaux, la période
    # auto-détectée, etc. disparaissent immédiatement plutôt que de rester
    # affichés avec les anciennes données.
    # On nettoie également les jobs en arrière-plan et les caches de session.
    _WHITELIST = {
        "language", "auth_user", "manual_logout", "_cookie_sync_attempts",
        "_prefs_synced_user", "tva_cookie_manager", "main_file_uploader",
        "language_selector_ui", "home_country_select", "display_currency_select",
        "target_currency", "currency_symbol", "display_currency_choice",
        "confirm_delete_account", "_malformed_vies_purged",
        "display_mode", "_display_mode_widget", "_asin_catalog_data",
        "_file_encoding_choice",
    }
    for _stale_key in list(st.session_state.keys()):
        if _stale_key not in _WHITELIST:
            st.session_state.pop(_stale_key, None)

    # Force le nettoyage de la mémoire après suppression de gros objets
    from tva_intracom.mem_utils import release_memory
    release_memory()

if uploaded_files:
    from tva_intracom.parsers import amazon as parser_amazon
    from tva_intracom.parsers import mirakl as parser_mirakl
    from tva_intracom.parsers import shopify as parser_shopify
    from tva_intracom.parsers import woocommerce as parser_woocommerce
    from tva_intracom.parsers import aliexpress as parser_aliexpress

    # Déduplication silencieuse
    _seen_file_keys: set = set()
    _deduped: list = []
    _dup_names: list = []
    for _f in uploaded_files:
        _fkey = _upload_sig(_f)
        if _fkey in _seen_file_keys:
            _dup_names.append(_f.name)
        else:
            _seen_file_keys.add(_fkey)
            _deduped.append(_f)
    if _dup_names:
        st.warning(_("duplicate_files_warning", count=len(_dup_names), files=", ".join(f"`{n}`" for n in _dup_names)))
    uploaded_files = _deduped

    # Nombre affiché par la barre de statut (voir plus haut dans ce fichier)
    # — mis à jour ici, avant tout parsing, pour rester correct même si le
    # parsing échoue plus loin (st.stop()). Uniquement le nombre, pas les
    # noms : peu d'intérêt pour l'utilisateur et prend trop de place dans un
    # bandeau censé rester compact (accord 2026-08-21).
    st.session_state["_status_bar_file_count"] = len(uploaded_files)

    # Cache de l'analyse des fichiers (indépendant du cache de calcul TVA plus
    # bas) : Streamlit ré-exécute tout le script à chaque interaction widget
    # (rerun), ce qui relançait sans le vouloir toute la boucle de parsing —
    # invisible sur un petit fichier, mais doublant le temps de chargement sur
    # un gros fichier. On ne ré-analyse que si les fichiers ou les options
    # d'import (pays d'origine, encodage, conversion devise, format,
    # catalogue ASIN) ont réellement changé.
    #
    # Le catalogue ASIN peut compter des dizaines de milliers d'entrées.
    # `_parse_catalog_bytes` est en `@st.cache_resource` côté sidebar (même
    # instance mémoire partagée entre sessions, pas de copie par appel) :
    # l'id() de asin_to_category reste donc stable pour un même contenu.
    # On garde malgré tout un hash de contenu (plutôt que de comparer sur
    # id()) pour ne pas faire reposer ce cache de clé sur un détail
    # d'implémentation de st.cache_resource : tuple(sorted(dict.items()))
    # retriait tout le catalogue (O(n log n)) à *chaque* rerun (changement
    # de filtre, d'onglet...) ; hash sur un frozenset est O(n), sans tri, et
    # ne compare qu'un simple int au rerun suivant au lieu d'un tuple de 20k
    # éléments.
    _asin_catalog_sig = hash(frozenset(asin_to_category.items())) if asin_to_category else None
    _parse_cache_key = (
        tuple(sorted(_upload_sig(f) for f in uploaded_files)),
        home_country, encoding, convert_fx, file_format,
        _asin_catalog_sig,
    )

    _calc_cache = CalcCacheState.load()
    if _calc_cache.parse_key == _parse_cache_key:
        _cached = _calc_cache.parse_data
        (all_sales, all_refunds, all_fc_transfers, all_invoice_credit_notes,
         all_stock_countries, all_account_identifiers, all_warnings, all_platforms,
         total_rows_sum, skipped_rows_sum, file_summaries, _parse_results) = _cached
        tmp_paths: list = []
    else:
        all_sales, all_refunds, all_fc_transfers = [], [], []
        all_invoice_credit_notes = []
        all_stock_countries, all_warnings, all_platforms = set(), [], []
        # Identifiants de compte Amazon (UNIQUE_ACCOUNT_IDENTIFIER) rencontrés dans
        # les fichiers importés — utilisés pour le gating anti-abus SIREN
        # (voir tva_intracom/ui/billing_gate.py). Vide pour les autres plateformes.
        all_account_identifiers: set = set()
        total_rows_sum = skipped_rows_sum = 0
        file_summaries, tmp_paths, _parse_results = [], [], []

        # Placeholder stable pour éviter les sauts d'interface pendant l'analyse des fichiers
        parse_progress_ph = st.empty()

        for uploaded_file in uploaded_files:
            _ext = Path(uploaded_file.name).suffix or ".csv"
            with tempfile.NamedTemporaryFile(delete=False, suffix=_ext, mode="wb") as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = Path(tmp.name)
            tmp_paths.append(tmp_path)
            try:
                parse_result = None
                if "Amazon" in file_format:
                    _progress_label = (
                        _("analysis_progress", name=uploaded_file.name)
                        if convert_fx else _("analysis_progress_simple", name=uploaded_file.name)
                    )
                    _progress_bar = parse_progress_ph.progress(0.0, text=_progress_label)

                    def _on_parse_progress(processed: int, total: int, label: Optional[str] = None, _fname=uploaded_file.name) -> None:
                        if label:
                            text = label
                            # On utilise le pourcentage fourni par processed/total si pertinent,
                            # ou on laisse à la valeur actuelle si on est dans une phase de pre-calcul.
                            pct = processed / total if total else 0.0
                        else:
                            pct = processed / total if total else 1.0
                            _suffix = f" ({_('fx_conv_suffix')})" if convert_fx else ""
                            text = _("analysis_progress_count", name=_fname, processed=f"{processed:,}".replace(",", " "), total=f"{total:,}".replace(",", " "), suffix=_suffix)

                        _progress_bar.progress(min(pct, 1.0), text=text)

                    parse_result = parser_amazon.load_amazon_report(
                        tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx,
                        asin_to_category=asin_to_category,
                        progress_callback=_on_parse_progress,
                        bce_label=_("calc_progress_bce_count"),
                        bce_wait_label=_("calc_progress_bce"),
                        target_currency=target_currency,
                        ioss_number=ioss_number,
                        seller_is_importer=seller_is_importer,
                    )
                    parse_progress_ph.empty()
                elif "Mirakl" in file_format:
                    parse_result = parser_mirakl.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                elif "Shopify" in file_format:
                    parse_result = parser_shopify.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                elif "WooCommerce" in file_format:
                    parse_result = parser_woocommerce.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                elif "AliExpress" in file_format:
                    parse_result = parser_aliexpress.parse(tmp_path, seller_country=home_country, encoding=encoding, convert_currencies=convert_fx)
                if parse_result is not None:
                    # BUGFIX (2026-08-25) : parse_result.platform est
                    # désormais "amazon" en minuscule en interne (cohérent
                    # avec cli.py et les autres parsers — voir
                    # parsers/amazon/loader.py). Ce champ atterrit
                    # directement dans l'UI (col_source, KPI "kpi_vat_amazon",
                    # onglet "tab_amazon_audit" via platform_name plus bas) :
                    # on le capitalise ICI, au seul point de construction,
                    # pour l'affichage — sans toucher à la valeur interne.
                    platform = (parse_result.platform or file_format.split("(")[0].strip()).capitalize()
                    all_sales.extend(parse_result.sales); all_refunds.extend(parse_result.refunds)
                    all_fc_transfers.extend(parse_result.fc_transfers)
                    all_invoice_credit_notes.extend(getattr(parse_result, "invoice_credit_notes", []))
                    all_stock_countries |= parse_result.stock_countries
                    all_account_identifiers |= getattr(parse_result, "account_identifiers", set())
                    all_warnings.extend(parse_result.warnings); all_platforms.append(platform)
                    total_rows_sum += parse_result.total_rows; skipped_rows_sum += parse_result.skipped_rows
                    _parse_results.append(parse_result)
                    file_summaries.append({
                        _("col_file"): uploaded_file.name, _("col_source"): platform,
                        _("col_sales"): len(parse_result.sales), _("col_refunds"): len(parse_result.refunds),
                        _("col_fba_trans"): len(parse_result.fc_transfers),
                        _("col_phys_returns"): getattr(parse_result, "return_rows", 0),
                        _("col_invoices"): getattr(parse_result, "invoice_rows", 0),
                        _("col_credit_notes"): getattr(parse_result, "credit_note_rows", 0),
                        _("col_rows_read"): parse_result.total_rows, _("col_ignored"): parse_result.skipped_rows
                    })
            except Exception as e:
                st.error(f"Erreur sur **{uploaded_file.name}** : {e}")
                for p in tmp_paths: p.unlink(missing_ok=True)
                st.stop()

        # Optimisation RAM : `parse_result.sales` / `.refunds` / `.fc_transfers`
        # de chaque ParseResult sont déjà entièrement recopiés dans
        # `all_sales` / `all_refunds` / `all_fc_transfers` ci-dessus (via
        # `.extend()`) et ne sont plus jamais relus après ce point (seuls
        # les scalaires/petites listes comme `return_rows`, `invoice_rows`,
        # `credit_note_rows`, `skipped_rows`, `period_mismatches` le sont,
        # cf. usages de `_parse_results` plus bas). Les garder dans les
        # ParseResult mis en cache en session_state revenait donc à
        # dupliquer en RAM la totalité des ventes/remboursements importés,
        # en continu pendant toute la session (pas juste un pic transitoire).
        # On les vide juste avant la mise en cache pour ne garder que les
        # métadonnées réellement utilisées en aval.
        for _pr in _parse_results:
            _pr.sales = []
            _pr.refunds = []
            _pr.fc_transfers = []

        CalcCacheState.save_parse(
            _parse_cache_key,
            (
                all_sales, all_refunds, all_fc_transfers, all_invoice_credit_notes,
                all_stock_countries, all_account_identifiers, all_warnings, all_platforms,
                total_rows_sum, skipped_rows_sum, file_summaries, _parse_results,
            ),
        )

    platform_name = all_platforms[0] if all_platforms else file_format.split("(")[0].strip()
    unique_platforms = list(dict.fromkeys(all_platforms))
    _total_returns      = sum(getattr(pr, "return_rows", 0) for pr in _parse_results)
    _total_invoice      = sum(getattr(pr, "invoice_rows", 0) for pr in _parse_results)
    _total_credit_note  = sum(getattr(pr, "credit_note_rows", 0) for pr in _parse_results)
    _total_skipped      = sum(getattr(pr, "skipped_rows", 0) for pr in _parse_results)

    # Résumé import
    _return_part  = _("summary_part_returns", count=_total_returns) if _total_returns else ""
    _invoice_part = _("summary_part_invoices", count=_total_invoice) if _total_invoice else ""
    _credit_part  = _("summary_part_credits", count=_total_credit_note) if _total_credit_note else ""
    _skip_part    = _("summary_part_skipped", count=_total_skipped) if _total_skipped else ""

    # =====================================================================
    # MODE D'AFFICHAGE (Simple / Détaillé) — n'affecte QUE la présentation.
    # Stocké dans session_state, jamais inclus dans _parse_cache_key ni
    # _cache_key (voir plus bas) : basculer de mode ne redéclenche AUCUN
    # recalcul, seule la partie affichage est reparcourue au rerun.
    # =====================================================================
    # Initialisation déplacée en tête de script (voir ensure_display_mode(),
    # appelée avant la barre de statut) — conservé ici comme alias local
    # `_is_detailed` pour ne pas devoir toucher chacun de ses usages plus
    # bas dans ce même bloc.
    _is_detailed = is_detailed()

    if len(uploaded_files) == 1:
        fs = file_summaries[0]
        if _is_detailed:
            st.info(_("import_summary_single", platform=platform_name, sales=fs[_('col_sales')], refunds=fs[_('col_refunds')], fc=len(all_fc_transfers), returns=_return_part, invoices=_invoice_part, credits=_credit_part, skipped=_skip_part))
    else:
        if _is_detailed:
            st.success(_("import_summary_multi", count=len(uploaded_files), sales=len(all_sales), refunds=len(all_refunds), fc=len(all_fc_transfers), returns=_return_part, invoices=_invoice_part, credits=_credit_part, skipped=_skip_part, total_rows=total_rows_sum))
            with st.expander(_("file_detail_expander", count=len(uploaded_files))):
                st.table(file_summaries)
            if len(unique_platforms) > 1:
                st.warning(_("different_sources_warning", sources=', '.join(unique_platforms)))
    if all_warnings and _is_detailed:
        with st.expander(_("import_warnings_header", count=len(all_warnings))):
            for w in all_warnings: st.text(w)

    all_period_mismatches = []
    for pr in _parse_results:
        all_period_mismatches.extend(getattr(pr, "period_mismatches", []))
    if all_period_mismatches and _is_detailed:
        with st.expander(
                _("period_mismatch_title", count=len(all_period_mismatches)),
                expanded=False,
        ):
            st.caption(_("period_mismatch_caption"))
            st.dataframe(
                pd.DataFrame([
                    {_("period_mismatch_col_id"): m["sale_id"], _("period_mismatch_col_order"): m["order_date"],
                     _("period_mismatch_col_shipment"): m["shipment_date"],
                     _("period_mismatch_col_amount"): float(m["amount_ht"])}
                    for m in all_period_mismatches
                ]),
                width="stretch", hide_index=True,
            )

    # Devises étrangères utilisées (le taux BCE de clôture de période
    # réellement appliqué à la déclaration OSS est affiché plus bas, une
    # fois `period_label` résolu — voir bloc après build_billing_gate).
    _fx_currencies_used: set = set()
    if convert_fx:
        for _s in all_sales:
            if _s.original_currency and _s.original_currency != "EUR":
                _fx_currencies_used.add(_s.original_currency)

    sales, refunds = all_sales, all_refunds

    try:
        if not sales:
            st.error(_("no_sale_error"))
            st.stop()

        import dataclasses as _dc
        if ioss_number or seller_is_importer:
            sales = [_dc.replace(s,
                                 ioss_number=ioss_number.strip() if ioss_number else s.ioss_number,
                                 seller_is_importer=seller_is_importer if seller_is_importer else s.seller_is_importer)
                     for s in sales]

        if not convert_fx:
            foreign = {s.original_currency for s in sales if s.original_currency and s.original_currency != "EUR"}
            if foreign:
                st.warning(_("foreign_currency_warning", currencies=', '.join(sorted(foreign))))

        # === CALCUL (mis en cache dans session_state) ===
        _vies_retry_nonce = CalcCacheState.load().vies_retry_nonce
        _cache_key = (
            # SÉCURITÉ (voir README - évolution.md) : `current_user.id` inclus
            # explicitement en tête de clé. Sans cela, deux comptes distincts
            # uploadant un fichier strictement identique avec les mêmes
            # réglages fiscaux partageaient la même entrée de cache
            # (st.cache_data est un cache global au process, pas par session
            # -- voir aussi heavy_cache_data dans mem_utils.py). Les données
            # actuelles sont dérivées du seul fichier importé (pas de PII
            # propre à l'utilisateur au-delà de ce qu'il a lui-même fourni),
            # mais l'isolation cryptographique par utilisateur est appliquée
            # par prudence, avant toute évolution qui stockerait davantage
            # dans ces DataFrames mis en cache.
            _current_user.id,
            tuple(_upload_sig(f) for f in uploaded_files),
            enable_vies, convert_fx, file_format,
            tuple(sorted(asin_to_category.items())),
            ioss_number, seller_is_importer,
            tuple(sorted(countries_with_vat)),
            apply_fr_under_threshold,
            # BUGFIX (voir README - évolution.md) : `ioss_own_number_active`
            # influence bien engine.compute_vat() (voir engine.py ~l.312)
            # mais était absent de cette clé — cocher/décocher ce toggle
            # réutilisait silencieusement l'ancien résultat en cache.
            ioss_own_number_active,
            home_country,
            target_currency,
            _vies_retry_nonce,
            _vies_scope_id,  # Indispensable : les overrides VIES sont propres à chaque SIREN
            # BUGFIX (voir README - évolution.md) : ces trois variables
            # influencent bien le résultat du calcul (langue des notes
            # générées, taux BCE du seuil OSS affiché selon la période,
            # comportement sur numéro TVA invalide) mais étaient absentes
            # de la clé de cache — un changement de langue ou de période
            # OSS réutilisait silencieusement l'ancien résultat en cache.
            st.session_state.get("language"),
            oss_period,
            on_invalid_behavior,
        )

        calc_progress_ph = st.empty()

        # Seuil au-delà duquel le calcul (VIES + moteur TVA) est délégué à un
        # thread séparé plutôt qu'exécuté en direct dans le script — voir
        # tva_intracom/ui/background_calc.py pour la justification détaillée.
        # En dessous, le chemin synchrone d'origine est conservé à
        # l'identique (aucun changement de comportement pour les cas
        # courants, aucun risque de régression sur le chemin le plus
        # emprunté).
        # CORRECTIF (voir README - évolution.md) : le seuil se basait sur
        # `total_rows_sum`, qui compte TOUTES les lignes lues dans les CSV
        # (transferts FBA, factures, lignes ignorées comprises), pas
        # seulement le volume réellement soumis au calcul fiscal
        # (VIES + compute_vat). Un fichier de quelques Mo dépassait 20k
        # lignes brutes en un instant, basculant trop souvent sur le
        # chemin thread (délai de démarrage + rafraîchissement fragment)
        # pour des volumes que le script principal traite en un instant.
        # On se base désormais sur `len(sales) + len(refunds)`, le volume
        # qui alimente réellement `_run_oss_loop`/VIES.
        _BIG_FILE_ROW_THRESHOLD = 20_000
        _is_big_file = (len(sales) + len(refunds)) > _BIG_FILE_ROW_THRESHOLD

        vies_summary = None
        if CalcCacheState.load().calc_key != _cache_key:
            # On capture le contexte de session AVANT de lancer le thread pour
            # éviter les appels à st.session_state (ScriptRunContext)
            _lang_for_thread = st.session_state.get("language", "fr")
            _curr_for_thread = st.session_state.get("target_currency", "EUR")
            _sym_for_thread  = st.session_state.get("currency_symbol", "€")

            def _run_full_calc(report: "Callable[[float, str], None]"):
                """Exécute le calcul complet (VIES + moteur + rapport). Ne fait
                JAMAIS d'appel st.* — voir docstring de background_calc.py.
                Signature commune, que ce soit appelé en direct (petit
                fichier) ou dans un thread (gros fichier)."""
                # RÉPARTITION 2026-08-27 (voir README - évolution.md) : la
                # phase VIES (souvent quelques numéros uniques, cf.
                # dédoublonnage dans engine.py) est presque toujours rapide,
                # même sur un fichier de 100k lignes — c'est la boucle OSS
                # qui suit (_run_oss_loop) qui domine le temps de calcul, et
                # qui restait jusqu'ici silencieuse : la barre affichait
                # "Interrogation VIES..." figée à 85% pendant tout ce temps,
                # donnant l'impression trompeuse que l'app était bloquée.
                # On resserre donc la plage VIES (0-0.3) et on ouvre une
                # vraie plage pour la boucle OSS (0.3-0.85).
                def _vies_progress_cb(done: int, total: int) -> None:
                    if total <= 0:
                        return
                    report(min(done / total, 1.0) * 0.3, _("calc_progress_vies_count", lang=_lang_for_thread, done=done, total=total))

                def _oss_progress_cb(done: int, total: int) -> None:
                    if total <= 0:
                        return
                    report(0.3 + min(done / total, 1.0) * 0.55, _("calc_progress_oss_count", lang=_lang_for_thread, done=done, total=total))

                # Ventes ET avoirs sont désormais calculés en un seul appel
                # (voir compute_all_with_vies / _run_oss_loop dans engine.py) :
                # le second appel dédié aux avoirs, qui refaisait entièrement
                # le tri chronologique, la normalisation TVA et le lookup
                # VIES pour rien (déjà fait en interne par le premier appel),
                # a été supprimé. asin_to_category et apply_fr_under_threshold
                # sont toujours transmis pour les mêmes raisons qu'avant (voir
                # historique) : sans eux, un avoir retomberait sur la
                # catégorie STANDARD et/ou ne matcherait pas le régime de la
                # vente d'origine.
                _results, _refund_results, _vies_summary, _oss_summary = compute_all_with_vies(
                    sales, scope_id=_vies_scope_id, asin_to_category=asin_to_category,
                    on_invalid=on_invalid_behavior, marketplace_name=platform_name,
                    apply_fr_under_threshold=apply_fr_under_threshold,
                    refunds=refunds if refunds else None,
                    vies_progress_callback=_vies_progress_cb,
                    oss_progress_callback=_oss_progress_cb,
                    lang=_lang_for_thread, currency=_curr_for_thread, symbol=_sym_for_thread,
                    ioss_own_number_active=ioss_own_number_active)

                report(0.9, _("calc_progress_vat", lang=_lang_for_thread))
                _summary = build_report(_results, refund_results=_refund_results or None, lang=_lang_for_thread)
                report(1.0, "")
                return _results, _vies_summary, _oss_summary, _refund_results, _summary

            if _is_big_file:
                _job_id = "calc_" + str(abs(hash(_cache_key)))
                start_background_job(_job_id, _run_full_calc)
                with calc_progress_ph.container():
                    st.caption(_("calc_bg_running_caption", rows=f"{total_rows_sum:,}".replace(",", " ")))
                    render_job_progress(_job_id, label=_("calc_progress_vies"))
                _job_state = get_job_state(_job_id)
                with _job_state.lock:
                    _job_done, _job_error = _job_state.done, _job_state.error
                if not _job_done:
                    # Le fragment ci-dessus continue de se rafraîchir tout
                    # seul (run_every=0.4s) sans ré-exécuter le reste du
                    # script : on s'arrête ici pour CE rerun, la sidebar et
                    # les widgets déjà rendus plus haut restent utilisables.
                    st.stop()
                if _job_error is not None:
                    clear_job(_job_id)
                    st.error(_("processing_error", error=_job_error))
                    raise _job_error
                results, vies_summary, oss_summary, refund_results, summary = _job_state.result
                clear_job(_job_id)
                calc_progress_ph.empty()
            else:
                with calc_progress_ph.container():
                    with st.status(_("calc_progress_vies"), expanded=True) as _calc_status:
                        def _status_progress_cb(p, t):
                            # `t` est vide lors du dernier appel (report(1.0, ""),
                            # voir _run_full_calc) : on garde alors le dernier
                            # libellé affiché plutôt que d'effacer le texte.
                            if t:
                                _calc_status.update(label=t)
                        results, vies_summary, oss_summary, refund_results, summary = _run_full_calc(
                            _status_progress_cb
                        )
                        _calc_status.update(state="complete", expanded=False)
                calc_progress_ph.empty()

            CalcCacheState.save_calc(_cache_key, results, refund_results, summary, vies_summary, oss_summary)
            # La sidebar (rendue AVANT le calcul, voir render_sidebar() plus
            # haut) affiche la période auto-détectée à partir de
            # `st.session_state["_results"]` : sans ce rerun, elle resterait
            # vide pendant tout le run où le fichier vient d'être analysé, et
            # n'afficherait la période qu'au prochain rerun fortuit.
            if CalcCacheState.load().period_sync_key != _cache_key:
                CalcCacheState.save_period_sync_key(_cache_key)
                preserve_upload_rerun()
        else:
            _calc_cache = CalcCacheState.load()
            results        = _calc_cache.results
            refund_results = _calc_cache.refund_results
            summary        = _calc_cache.summary
            vies_summary   = _calc_cache.vies_summary
            oss_summary    = _calc_cache.oss_summary

        if vies_summary and vies_summary.total_inconclusive > 0:
            st.error(_("vies_inconclusive_error", count=vies_summary.total_inconclusive))
            if st.button(_("vies_reverify_btn"), key="retry_vies_error_banner"):
                CalcCacheState.save_vies_retry_nonce(_vies_retry_nonce + 1)
                preserve_upload_rerun()

        # Segmentation écarts pour KPI
        _vies_ids_kpi     = getattr(vies_summary, 'vies_affected_sale_ids', set()) if vies_summary else set()
        _vies_rc_ids_kpi:  set[str] = set()
        _dom_rc_ids_kpi:   set[str] = set()
        if vies_summary and hasattr(vies_summary, "reclassifications"):
            for _rc_kpi in vies_summary.reclassifications:
                if getattr(_rc_kpi, "is_domestic_reverse_charge", False):
                    _dom_rc_ids_kpi.add(_rc_kpi.sale_id)
                else:
                    _vies_rc_ids_kpi.add(_rc_kpi.sale_id)
        from tva_intracom.rates import DOMESTIC_REVERSE_CHARGE_COUNTRIES as _DRC_KPI
        from tva_intracom.models import BuyerType as _BT_KPI
        ecarts_autres = []
        for _r in results:
            _tva_amz = float(getattr(_r.sale, 'amazon_vat_amount', Decimal('0')))
            _tva_mot = float(_r.vat_amount)
            _ecart_kpi = _tva_amz - _tva_mot
            if abs(_ecart_kpi) <= 0.05: continue
            if _r.sale.stock_country == 'GB' or _r.sale.buyer_country == 'GB': continue
            _sid_kpi = str(_r.sale.sale_id)
            if _sid_kpi in _vies_rc_ids_kpi or (_sid_kpi, _r.sale.amount_ht) in _vies_ids_kpi: continue
            if _sid_kpi in _dom_rc_ids_kpi or (_r.sale.buyer_type == _BT_KPI.B2B and _r.sale.buyer_country in _DRC_KPI and _tva_mot == 0 and _tva_amz > 0): continue
            if _tva_amz == 0 and _tva_mot > 0: continue
            ecarts_autres.append((_r, _ecart_kpi))
        total_ecarts_autres = sum(d for _, d in ecarts_autres)

        # =====================================================================
        # ALERTES — toujours en haut, conditionnelles
        # =====================================================================
        if _is_detailed:
            render_historical_rates_alert(results)

        # On calcule pay_eu nécessaire pour le billing gate et les immatriculations
        pay_eu = {r.vat_country for r in results if r.channel.value == "LOCAL" and r.vat_country}

        # =====================================================================
        # GATING BILLING
        # =====================================================================
        from tva_intracom.ui.billing_gate import build_billing_gate, render_account_link_panel

        _gate = build_billing_gate(
            results=results, oss_period=oss_period, cache_key=_cache_key,
            current_user=_current_user, siren_entreprise=siren_entreprise,
            siren_quota_status=_siren_quota_status,
            all_stock_countries=all_stock_countries, pay_eu=pay_eu,
            seller_is_importer=seller_is_importer,
            local_vat_numbers=local_vat_numbers, ioss_number=ioss_number,
            vies_summary=vies_summary,
            stripe_success_url=_stripe_success_url,
            stripe_cancel_url=_stripe_cancel_url,
            vies_scope_id=_vies_scope_id,
            all_account_identifiers=all_account_identifiers,
            nom_entreprise=nom_entreprise,
            home_country=home_country,
        )
        render_account_link_panel(_gate)
        period_label = _gate.period_label
        _period_detected_range = _gate.period_detected_range
        _can_export = _gate.can_export
        _quota_status = _gate.quota_status
        _compliance_blocked = _gate.compliance_blocked
        _missing_vats = _gate.missing_vats
        _ioss_missing = _gate.ioss_missing
        _unlock_label_suffix = _gate.unlock_label_suffix
        _gated_download = _gate.gated_download
        _get_payg_checkout_url = _gate.get_payg_checkout_url

        # Taux BCE de clôture de période réellement utilisés pour la
        # conversion OSS (Règl. UE 2020/194, art. 5 bis) — affiche les taux
        # par devise et par date de clôture si la période est multiple.
        if convert_fx and _fx_currencies_used and _is_detailed:
            from tva_intracom.ecb_rates import get_oss_rate_date
            _used_rates_info = set()
            for _r in results:
                if _r.sale.original_currency and _r.sale.original_currency != "EUR":
                    try:
                        _tx_date = datetime.fromisoformat((_r.sale.transaction_date or "")[:10])
                        # On utilise la date de clôture OSS correspondante à la transaction
                        _rate_date = get_oss_rate_date(period_label, _tx_date)
                        _used_rates_info.add((_r.sale.original_currency.upper(), _rate_date))
                    except Exception:
                        pass

            if _used_rates_info:
                _all_dates = sorted({d for c, d in _used_rates_info})
                with st.expander(_("bce_rates_title", count=len(_used_rates_info))):
                    if len(_all_dates) == 1:
                        st.caption(_("bce_rates_oss_disclaimer", date=_all_dates[0].isoformat()))
                    elif "_" in period_label:
                        _p_parts = period_label.split("_")
                        _p_start = _p_parts[0]
                        _p_end = _p_parts[-1]
                        if not "-" in _p_end and "-" in _p_start:
                            # Cas "2026-Q1_Q3" -> "2026-Q1" à "2026-Q3"
                            _p_year = _p_start.split("-")[0]
                            _p_end = f"{_p_year}-{_p_end}"
                        st.caption(_("bce_rates_oss_disclaimer_range", start=_p_start, end=_p_end))
                    else:
                        st.caption(_("bce_rates_oss_disclaimer", date=f"{_all_dates[0].isoformat()} → {_all_dates[-1].isoformat()}"))

                    for _ccy, _d in sorted(_used_rates_info):
                        try:
                            _oss_rate = _ecb_get_rate(_ccy, _d)
                        except Exception:
                            _oss_rate = None

                        _date_suffix = f" ({_d.strftime('%d/%m/%Y')})" if len(_all_dates) > 1 else ""
                        if _oss_rate is not None:
                            st.caption(f"**{_ccy}** : 1 EUR = {float(_oss_rate):.4f} {_ccy}{_date_suffix}")
                        else:
                            st.caption(f"**{_ccy}** : {_('bce_rates_oss_unavailable')}{_date_suffix}")

        # Immatriculations requises
        # BUGFIX : un stock situé hors UE (US, GB post-Brexit, CH, CN, un
        # entrepôt 3PL non-UE...) ne crée aucune obligation d'immatriculation
        # TVA intracommunautaire — seul un stock dans un AUTRE État membre UE
        # que le pays d'origine du compte le fait. `all_stock_countries` était
        # utilisé tel quel, sans filtre UE, ce qui réclamait à tort un numéro
        # de TVA local (et bloquait le téléchargement) pour du stock hors UE.
        unregistered = {
                           c for c in all_stock_countries if c and is_eu(c) and c != home_country
                       } - set(countries_with_vat)
        unregistered_local = pay_eu - set(countries_with_vat)

        registration_needed = {}
        for c in unregistered:
            if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["stock"] = True
        for c in unregistered_local:
            if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["sales"] = True
        if seller_is_importer:
            _ddp_unrg = {r.vat_country for r in results
                         if r.scenario.value == "IMPORT_SELLER_AS_IMPORTER"
                         and r.vat_country != "FR" and r.vat_country not in countries_with_vat}
            for c in _ddp_unrg:
                if c: registration_needed.setdefault(c, {"stock": False, "sales": False, "ddp": False})["ddp"] = True

        if registration_needed:
            _reg_list = ", ".join(sorted(registration_needed.keys()))
            with st.expander(_("action_plan_title", countries=_reg_list), expanded=True):
                st.write(_("action_plan_intro"))
                for c in sorted(registration_needed.keys()):
                    reasons = []
                    icons = ""
                    data = registration_needed[c]
                    if data["stock"]:
                        icons += "📦 "
                        reasons.append(_("action_reason_stock"))
                    if data["sales"]:
                        icons += "💰 "
                        reasons.append(_("action_reason_sales"))
                    if data["ddp"]:
                        icons += "🛃 "
                        reasons.append(_("action_reason_ddp"))

                    st.markdown(f"- **{country_label(c)} ({c})** : {icons} — *Raison : {' + '.join(reasons)}*")

                critical_blocking = [c for c in registration_needed if c in ["DE", home_country]]
                if critical_blocking:
                    _c_list = " et ".join(f"**{country_label(c)} ({c})**" for c in sorted(critical_blocking))
                    st.warning(_("amazon_blocking_warning", countries=_c_list))

        # =====================================================================
        # KPIs — toujours visibles
        # =====================================================================
        # CSS des .kpi-card / .kpi-label / .kpi-value / .badge-alert : voir
        # tva_intracom/ui/theme.py (_CSS), injecté une seule fois par
        # apply_theme() en tête de script — extrait de app.py, aucune
        # modification de comportement.

        def _kpi_card(label: str, value: str, accent: str, help_text: str = "") -> str:
            title_attr = f' title="{help_text}"' if help_text else ""
            return f"""
            <div class="kpi-card" style="--kpi-accent:{accent}"{title_attr}>
                <div class="kpi-label">{label}</div>
                <div class="kpi-value">{value}</div>
            </div>
            """

        # =====================================================================
        # TABLEAU DE BORD
        # =====================================================================
        with st.container():
            # Toggle Simple/Détaillé déplacé dans la barre de statut
            # persistante (voir render_mode_toggle(), tête de app.py).
            st.header(_("recapitulatif_header"))
            c1, c2, c3, c4 = st.columns(4)

            ca_brut = float(summary.total_ht)
            ca_remb = float(getattr(summary, "refund_total_ht", 0))
            ca_net  = ca_brut + ca_remb

            with c1:
                st.markdown(_kpi_card(_("kpi_ca_ht"), _fmt(ca_net), "#1f4e79",
                                      _("kpi_ca_ht_help", gross=_fmt(ca_brut), refunds=_fmt(ca_remb))), unsafe_allow_html=True)
            with c2:
                st.markdown(_kpi_card(_("kpi_vat_you_owe"), _fmt(float(summary.total_you_owe)), "#d97706",
                                      _("kpi_vat_you_owe_help")), unsafe_allow_html=True)
            with c3:
                st.markdown(_kpi_card(_("kpi_vat_amazon", platform=platform_name), _fmt(float(summary.amazon_vat)), "#2ca02c",
                                      _("kpi_vat_amazon_help", platform=platform_name)), unsafe_allow_html=True)
            with c4:
                if abs(total_ecarts_autres) > 0.05:
                    _sign = "+" if total_ecarts_autres >= 0 else ""
                    st.markdown(_kpi_card(_("amazon_config_error", platform=platform_name), f"{_sign}{_fmt(total_ecarts_autres)}", "#d62728"),
                                unsafe_allow_html=True)
                    st.markdown(f'<span class="badge-alert">{_("config_error_badge")}</span>', unsafe_allow_html=True)
                else:
                    st.markdown(_kpi_card(_("amazon_config_success", platform=platform_name), _fmt(0), "#2ca02c"), unsafe_allow_html=True)

        # =====================================================================
        # ONGLETS PRINCIPAUX
        # =====================================================================
        # Mode simple : l'onglet Audit Amazon n'apporte rien tant qu'aucun
        # écart TVA Amazon significatif n'existe (KPI "Config Amazon
        # conforme") ET qu'aucun pays de stock FBA local n'est à risque
        # d'immatriculation manquante -- ce second point est calculé via
        # `_aggregate_fba_local_sales`, la même fonction mise en cache
        # (heavy_cache_data) qu'utilise déjà l'onglet Audit lui-même : aucun
        # nouveau calcul, uniquement une lecture (potentiellement déjà en
        # cache) réutilisée ici pour décider de l'affichage de l'onglet.
        from tva_intracom.ui.tabs.audit import _aggregate_fba_local_sales
        _fba_by_country = _aggregate_fba_local_sales(all_sales, _cache_key)
        _fba_at_risk = any(c not in countries_with_vat for c in _fba_by_country)
        _show_audit_tab = _is_detailed or abs(total_ecarts_autres) > 0.05 or _fba_at_risk

        _tab_labels = [
            _("tab_declarations"),
            _("tab_sales_detail"),
            _("tab_vies"),
        ]
        if _show_audit_tab:
            _tab_labels.append(_("tab_amazon_audit", platform=platform_name))
        _tab_labels += [_("tab_downloads"), _("tab_visualizations")]

        _tabs = st.tabs(_tab_labels)
        if _show_audit_tab:
            tab_decl, tab_detail, tab_vies, tab_audit, tab_dl, tab_viz = _tabs
        else:
            tab_decl, tab_detail, tab_vies, tab_dl, tab_viz = _tabs
            tab_audit = None

        # =====================================================================
        # CONSTRUCTION DU CONTEXTE PARTAGÉ + RENDU DES ONGLETS
        # =====================================================================
        from tva_intracom.ui.tabs.context import TabContext
        from tva_intracom.ui.tabs.declarations import render_declarations
        from tva_intracom.ui.tabs.detail_ventes import render_detail_ventes
        from tva_intracom.ui.tabs.vies_ui import render_vies
        from tva_intracom.ui.tabs.audit import render_audit
        from tva_intracom.ui.tabs.telechargements import render_telechargements
        from tva_intracom.ui.tabs.visualisations import render_visualisations

        _tab_ctx = TabContext(
            results=results,
            refund_results=refund_results,
            summary=summary,
            vies_summary=vies_summary,
            oss_summary=oss_summary,
            period_label=period_label,
            period_detected_range=_period_detected_range,
            can_export=_can_export,
            gated_download=_gated_download,
            unlock_label_suffix=_unlock_label_suffix,
            vies_scope_id=_vies_scope_id,
            vies_retry_nonce=_vies_retry_nonce,
            enable_vies=enable_vies,
            is_admin=tva_auth.is_admin(_current_user),
            current_user_id=_current_user.id,
            nom_entreprise=nom_entreprise,
            siren_entreprise=siren_entreprise,
            tva_fr=tva_fr,
            countries_with_vat=countries_with_vat,
            local_vat_numbers=local_vat_numbers,
            all_fc_transfers=all_fc_transfers,
            all_invoice_credit_notes=all_invoice_credit_notes,
            all_sales=all_sales,
            platform_name=platform_name,
            home_country=home_country,
            target_currency=target_currency,
            calc_key=_cache_key,
        )

        # Stocké dans session_state (et non plus seulement passé en argument)
        # pour render_detail_ventes()/render_audit()/render_telechargements()/
        # render_visualisations() -- ces quatre onglets sont décorés
        # `@st.fragment`, et Streamlit retient sinon les arguments du dernier
        # appel d'un fragment au niveau de la session interne, INDÉPENDAMMENT
        # de session_state : passer directement `_tab_ctx` (qui porte
        # all_sales/results, donc potentiellement des milliers d'objets
        # Sale/VatResult) les gardait vivants même après un
        # `st.session_state.clear()` au logout.
        # Voir la docstring de render_detail_ventes() pour le détail complet.
        st.session_state["_tab_ctx"] = _tab_ctx

        with tab_decl: render_declarations(_tab_ctx)
        with tab_detail: render_detail_ventes()
        with tab_vies: render_vies(_tab_ctx)
        if tab_audit is not None:
            with tab_audit: render_audit()
        with tab_dl: render_telechargements()
        with tab_viz: render_visualisations()

        # BUGFIX : la sidebar a été dessinée en tout début de run avec
        # `_period_label_shown_by_sidebar` (voir plus haut), potentiellement
        # obsolète. À ce stade, tout le contenu principal (KPIs, onglets) est
        # déjà rendu — un rerun ici ne fait donc plus disparaître les onglets
        # (contrairement à l'ancien correctif, retiré, qui rerun-ait AVANT
        # leur rendu). On ne le déclenche qu'UNE fois, seulement si la valeur
        # a réellement changé, pour éviter toute boucle : au run suivant,
        # `_period_label_shown_by_sidebar` capturera la valeur déjà à jour.
        if st.session_state.get("_period_label", "") != _period_label_shown_by_sidebar:
            preserve_upload_rerun()

    except Exception as exc:
        st.error(_("processing_error", error=exc))
        raise
    finally:
        for _p in tmp_paths: _p.unlink(missing_ok=True)

else:
    st.session_state.pop("_period_label", None)
    # BUGFIX (même logique qu'au-dessus) : après un vrai retrait de fichier,
    # la sidebar avait déjà affiché l'ancienne période détectée avant que ce
    # pop() n'ait lieu. Sans resynchronisation, elle restait affichée
    # indéfiniment tant qu'aucune autre interaction serveur ne survenait.
    if _period_label_shown_by_sidebar:
        preserve_upload_rerun()