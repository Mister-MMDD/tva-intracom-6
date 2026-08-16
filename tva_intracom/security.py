"""Utilitaires de sécurité pour la conformité Amazon DPP (Data Protection Policy)."""

import logging

from cryptography.fernet import Fernet

from .config import get_secret

logger = logging.getLogger(__name__)

# Clé de chiffrement chargée depuis les secrets.
_KEY = get_secret("ENCRYPTION_KEY")

# PERF (voir README - évolution.md) : instance Fernet mise en cache
# (singleton module-level) au lieu d'être reconstruite (parsing/validation
# de la clé inclus) à CHAQUE appel de encrypt_data/decrypt_data — coûteux
# sur un batch VIES de plusieurs centaines/milliers de numéros où chaque
# élément est chiffré/déchiffré individuellement. `_KEY` est chargé une
# seule fois au niveau module et n'est pas censé changer en cours de
# process, donc aucun risque à ne construire l'objet qu'une fois.
_fernet_singleton: Fernet | None = None

def _get_fernet() -> Fernet:
    """Initialise Fernet (une seule fois) et valide la clé de chiffrement."""
    global _fernet_singleton
    if _fernet_singleton is not None:
        return _fernet_singleton
    if not _KEY:
        logger.critical("ENCRYPTION_KEY is missing in configuration!")
        raise RuntimeError("Security Error: Encryption key is not configured. Sensitive data cannot be processed.")
    try:
        _fernet_singleton = Fernet(_KEY.encode())
        return _fernet_singleton
    except Exception as e:
        logger.critical(f"Invalid ENCRYPTION_KEY format: {str(e)}")
        raise RuntimeError("Security Error: Encryption key is invalid.") from e

def encrypt_data(data: str) -> str:
    """Chiffre une chaîne de caractères (ex: PII).
    
    Lève une exception si le chiffrement échoue pour éviter de manipuler 
    des données en clair par erreur.
    """
    if not data:
        return data
    
    f = _get_fernet()
    try:
        return f.encrypt(data.encode()).decode()
    except Exception as e:
        logger.error(f"Encryption failed: {str(e)}")
        raise ValueError("Failed to protect sensitive data.") from e

def decrypt_data(encrypted_data: str) -> str:
    """Déchiffre une chaîne de caractères.

    Lève une exception si le déchiffrement échoue, ou si la donnée n'est
    pas un jeton Fernet valide (préfixe 'gAAAA').
    """
    if not encrypted_data:
        return encrypted_data

    # SÉCURITÉ (audit, voir README - évolution.md) : l'ancienne heuristique
    # "fail-open" (retourner tel quel toute valeur ne commençant pas par
    # 'gAAAA', en supposant qu'il s'agissait de texte en clair hérité
    # d'avant chiffrement) a été RETIRÉE le 2026-08-16, après confirmation
    # via backfill_encrypt_pii.py qu'il ne restait plus aucune ligne en
    # clair sur tva_number/ioss_number/vat_numbers_json (billing.py) ni
    # refresh_token (auth.py) — les seules colonnes qui en dépendaient.
    # Toute valeur non conforme au format Fernet est désormais traitée
    # comme une erreur plutôt qu'acceptée silencieusement.
    if not encrypted_data.startswith("gAAAA"):
        logger.error("decrypt_data: donnée non chiffrée (préfixe 'gAAAA' absent) — refusée.")
        raise ValueError("Failed to decrypt sensitive data: not a valid Fernet token.")

    f = _get_fernet()
    try:
        return f.decrypt(encrypted_data.encode()).decode()
    except Exception as e:
        logger.error(f"Decryption failed: {str(e)}")
        # On ne retourne plus la donnée brute ici si elle semble chiffrée 
        # mais que la clé est mauvaise, pour éviter toute fuite de PII.
        raise ValueError("Failed to decrypt sensitive data. Check encryption key compatibility.") from e
