"""Utilitaires de sécurité pour la conformité Amazon DPP (Data Protection Policy)."""

import logging
from cryptography.fernet import Fernet
from .config import get_secret

logger = logging.getLogger(__name__)

# Clé de chiffrement chargée depuis les secrets.
_KEY = get_secret("ENCRYPTION_KEY")

def _get_fernet() -> Fernet:
    """Initialise Fernet et valide la clé de chiffrement."""
    if not _KEY:
        logger.critical("ENCRYPTION_KEY is missing in configuration!")
        raise RuntimeError("Security Error: Encryption key is not configured. Sensitive data cannot be processed.")
    try:
        return Fernet(_KEY.encode())
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
    
    Lève une exception si le déchiffrement échoue.
    """
    if not encrypted_data:
        return encrypted_data
    
    f = _get_fernet()
    try:
        return f.decrypt(encrypted_data.encode()).decode()
    except Exception as e:
        logger.error(f"Decryption failed: {str(e)}")
        # On ne retourne plus la donnée brute ici pour éviter toute fuite de PII
        raise ValueError("Failed to decrypt sensitive data. Check encryption key compatibility.") from e
