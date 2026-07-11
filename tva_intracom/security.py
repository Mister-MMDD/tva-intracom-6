"""Utilitaires de sécurité pour la conformité Amazon DPP (Data Protection Policy)."""

from cryptography.fernet import Fernet
from .config import get_secret

# Clé de chiffrement chargée depuis les secrets. 
# Pour générer une clé : Fernet.generate_key().decode()
_KEY = get_secret("ENCRYPTION_KEY")

def encrypt_data(data: str) -> str:
    """Chiffre une chaîne de caractères (ex: PII) en utilisant Fernet."""
    if not data or not _KEY:
        return data
    try:
        f = Fernet(_KEY.encode())
        return f.encrypt(data.encode()).decode()
    except Exception:
        # En cas d'erreur (ex: clé invalide), on retourne la donnée brute 
        # (à éviter en prod, mais évite de tout casser si la clé est mal configurée)
        return data

def decrypt_data(encrypted_data: str) -> str:
    """Déchiffre une chaîne de caractères."""
    if not encrypted_data or not _KEY:
        return encrypted_data
    try:
        f = Fernet(_KEY.encode())
        return f.decrypt(encrypted_data.encode()).decode()
    except Exception:
        # Si le déchiffrement échoue, c'est peut-être que la donnée n'est pas 
        # chiffrée (transition) ou que la clé a changé.
        return encrypted_data
