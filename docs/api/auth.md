# Module `tva_intracom.auth`

## Description
Module d'authentification historique par magic link + jeton de session. Gère l'authentification des utilisateurs via email/magic link, le stockage des jetons de session dans PostgreSQL/Supabase, et l'envoi d'e-mails via l'API Resend. Héberge aussi le chiffrement Fernet des PII (Amazon DPP, y compris le refresh_token Amazon SP-API) et le stockage serveur des verifiers PKCE OAuth dans la table `tva_oauth_pkce`.

## Fonctions publiques

### `send_magic_link(email: str) -> str`
**Description courte** : Envoie un magic link par email et retourne le token de vérification.

**Description détaillée :**
- Génère un token de vérification aléatoire
- Stocke le token dans la base de données avec expiration
- Envoie un email contenant le magic link via l'API Resend
- Le token expire après 15 minutes par défaut

**Préconditions :**
- L'email doit être valide
- `RESEND_API_KEY` doit être configuré
- La connexion à la base de données doit être disponible

**Postconditions :**
- Un token est stocké dans la table des magic links
- Un email est envoyé à l'adresse fournie

**Effets de bord :**
- Insère une ligne dans la table des magic links
- Appelle l'API Resend (appel réseau)

**Paramètres :**
- `email` (str) : Adresse email de l'utilisateur

**Retour :** str - Token de vérification généré

**Exceptions :**
- `ValueError` : Email invalide
- `ConnectionError` : Impossible de contacter l'API Resend
- `DatabaseError` : Erreur lors du stockage du token

---

### `verify_magic_link(token: str) -> dict | None`
**Description courte** : Vérifie un magic link et retourne les informations de l'utilisateur si valide.

**Description détaillée :**
- Recherche le token dans la base de données
- Vérifie que le token n'est pas expiré
- Supprime le token après utilisation (single-use)
- Retourne les informations de l'utilisateur si valide

**Préconditions :**
- Le token doit exister dans la base de données
- Le token ne doit pas être expiré

**Postconditions :**
- Le token est supprimé de la base de données (single-use)

**Effets de bord :**
- Supprime la ligne correspondante dans la table des magic links

**Paramètres :**
- `token` (str) : Token de vérification reçu par email

**Retour :** dict | None - Informations de l'utilisateur si valide, None sinon

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données

---

### `create_session(user_id: str, email: str) -> str`
**Description courte** : Crée une session utilisateur et retourne le jeton de session.

**Description détaillée :**
- Génère un jeton de session sécurisé
- Stocke le jeton avec l'ID utilisateur et l'email
- Le jeton expire après 24 heures par défaut
- Utilise le chiffrement Fernet pour les données sensibles

**Préconditions :**
- `user_id` doit correspondre à un utilisateur existant
- `FERNET_KEY` doit être configuré

**Postconditions :**
- Une session est créée dans la base de données
- Le jeton peut être utilisé pour authentifier les requêtes

**Effets de bord :**
- Insère une ligne dans la table des sessions
- Appelle la base de données

**Paramètres :**
- `user_id` (str) : ID de l'utilisateur
- `email` (str) : Email de l'utilisateur

**Retour :** str - Jeton de session généré

**Exceptions :**
- `ValueError` : user_id invalide
- `DatabaseError` : Erreur lors de la création de la session

---

### `verify_session(token: str) -> dict | None`
**Description courte** : Vérifie un jeton de session et retourne les informations de l'utilisateur si valide.

**Description détaillée :**
- Déchiffre le jeton de session
- Vérifie que la session n'est pas expirée
- Retourne les informations de l'utilisateur si valide
- Met à jour le timestamp de dernière utilisation

**Préconditions :**
- Le jeton doit être un token Fernet valide
- La session correspondante doit exister et ne pas être expirée

**Postconditions :**
- Le timestamp de dernière utilisation est mis à jour

**Effets de bord :**
- Met à jour la ligne correspondante dans la table des sessions

**Paramètres :**
- `token` (str) : Jeton de session à vérifier

**Retour :** dict | None - Informations de l'utilisateur si valide, None sinon

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données
- `CryptoError` : Erreur lors du déchiffrement du token

---

### `encrypt_pii(data: str) -> str`
**Description courte** : Chiffre des données PII avec Fernet.

**Description détaillée :**
- Utilise Fernet pour chiffrer les données sensibles
- Le chiffrement est réversible avec `decrypt_pii`
- Utilise la clé Fernet configurée dans `FERNET_KEY`

**Préconditions :**
- `FERNET_KEY` doit être configuré
- Les données doivent être une chaîne UTF-8 valide

**Postconditions :**
- Les données sont chiffrées et ne peuvent être lues sans la clé

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `data` (str) : Données à chiffrer

**Retour :** str - Données chiffrées (base64)

**Exceptions :**
- `ValueError` : Données invalides
- `CryptoError` : Erreur lors du chiffrement

---

### `decrypt_pii(encrypted_data: str) -> str`
**Description courte** : Déchiffre des données PII chiffrées avec Fernet.

**Description détaillée :**
- Déchiffre les données chiffrées avec `encrypt_pii`
- Utilise la même clé Fernet
- Échoue si la clé est différente de celle utilisée pour le chiffrement

**Préconditions :**
- `FERNET_KEY` doit être configuré
- Les données doivent avoir été chiffrées avec la même clé

**Postconditions :**
- Les données originales sont restaurées

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `encrypted_data` (str) : Données chiffrées

**Retour :** str - Données déchiffrées

**Exceptions :**
- `ValueError` : Données invalides ou corrompues
- `CryptoError` : Erreur lors du déchiffrement

---

## Tables de base de données

### `magic_links`
- `token` (TEXT, PRIMARY KEY) : Token de vérification
- `email` (TEXT) : Email de l'utilisateur
- `created_at` (TIMESTAMPTZ) : Date de création
- `expires_at` (TIMESTAMPTZ) : Date d'expiration

### `sessions`
- `token` (TEXT, PRIMARY KEY) : Jeton de session
- `user_id` (TEXT) : ID de l'utilisateur
- `email` (TEXT) : Email de l'utilisateur
- `created_at` (TIMESTAMPTZ) : Date de création
- `expires_at` (TIMESTAMPTZ) : Date d'expiration
- `last_used_at` (TIMESTAMPTZ) : Dernière utilisation

### `tva_oauth_pkce`
- `code_verifier` (TEXT, PRIMARY KEY) : Verifier PKCE
- `state` (TEXT) : State OAuth
- `created_at` (TIMESTAMPTZ) : Date de création
- `expires_at` (TIMESTAMPTZ) : Date d'expiration

## Configuration requise

- `RESEND_API_KEY` : Clé API pour l'envoi d'emails
- `FERNET_KEY` : Clé de chiffrement Fernet pour les PII
- `SUPABASE_DB_URL` : URL de connexion à la base de données
