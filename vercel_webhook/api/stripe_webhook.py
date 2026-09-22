"""Endpoint webhook Stripe — déployé sur Vercel (Python serverless function),
dans un monorepo partagé avec l'app Streamlit principale.

Structure attendue du dépôt (racine du repo Git) :
    tva_intracom/
        billing.py
        ...
    vercel_webhook/
        vercel.json          <- doit être à la RACINE du repo, pas ici, voir note plus bas
        api/
            stripe_webhook.py  <- ce fichier

IMPORTANT : ce fichier charge tva_intracom/billing.py directement par son
chemin sur disque (importlib), et NE FAIT PAS `import tva_intracom`. Un import
de package déclencherait tva_intracom/__init__.py, dont le contenu n'est pas
connu ici — il pourrait importer d'autres modules (engine.py, vies.py...) avec
des dépendances non installées côté serverless, ou des effets de bord non
désirés. Le chargement par chemin isole strictement billing.py.

Réglages Vercel nécessaires :
    - Dashboard > Settings > General > Root Directory : laisser VIDE (racine du
      repo), sinon includeFiles ne pourra pas remonter jusqu'à tva_intracom/.
    - Variables d'environnement : STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
      SUPABASE_DB_URL.
    - Variables d'environnement OPTIONNELLES pour la sécurité (audit 2026-09-22) :
      * WEBHOOK_API_KEY : Clé secrète pour l'authentification applicative
      * WEBHOOK_IP_WHITELIST : Liste d'IPs autorisées (séparées par virgules)
"""
import importlib.util
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# SÉCURITÉ (fuite d'info dans les logs) : par défaut on ne logge qu'un message
# générique + l'event id Stripe (identifiant non sensible, permet de
# retrouver l'événement dans le Dashboard Stripe pour diagnostiquer). Le
# traceback complet peut contenir l'exception str() elle-même, laquelle
# inclut parfois un extrait du payload (ex: erreur de parsing JSON citant le
# contenu fautif) — ce qui peut être des données client. On ne l'active que
# si STRIPE_WEBHOOK_DEBUG_LOGS=1 est explicitement défini (à réserver à un
# environnement de test/staging Vercel, jamais en production).
_DEBUG_LOGS = os.environ.get("STRIPE_WEBHOOK_DEBUG_LOGS") == "1"

# SÉCURITÉ (audit 2026-09-22, ÉLEVÉ #9) : Authentification applicative supplémentaire
# pour le webhook Stripe. En plus de la vérification de la signature Stripe,
# on vérifie une API key secrète pour ajouter une couche de protection.
_WEBHOOK_API_KEY = os.environ.get("WEBHOOK_API_KEY")
_WEBHOOK_IP_WHITELIST = os.environ.get("WEBHOOK_IP_WHITELIST", "").split(",") if os.environ.get("WEBHOOK_IP_WHITELIST") else []

# api/stripe_webhook.py -> vercel_webhook/ -> racine du repo -> tva_intracom/billing.py
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG_DIR = _REPO_ROOT / "tva_intracom"
_BILLING_PATH = _PKG_DIR / "billing.py"

# IMPORTANT (ajout suite au chiffrement dans billing.py) : billing.py fait
# `from .security import encrypt_data, decrypt_data` — un import RELATIF, qui
# suppose que billing.py appartient au package "tva_intracom". Or on le charge
# ici par chemin de fichier, hors de tout package réel, ce qui casse cet
# import relatif ("attempted relative import with no known parent package").
# On enregistre donc d'abord un package parent minimal "tva_intracom" dans
# sys.modules, avec __path__ pointant vers le vrai dossier tva_intracom/, afin
# que Python puisse résoudre .security via l'import normal (fichier trouvé sur
# disque, importé une seule fois, mis en cache dans sys.modules comme
# n'importe quel sous-module). On ne touche pas à billing.py, qui doit rester
# un import relatif propre pour continuer à fonctionner normalement importé
# depuis l'app Streamlit (là où il fait bien partie du package tva_intracom).
if "tva_intracom" not in sys.modules:
    import types
    _pkg = types.ModuleType("tva_intracom")
    _pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["tva_intracom"] = _pkg

_spec = importlib.util.spec_from_file_location("tva_intracom.billing", _BILLING_PATH)
_billing = importlib.util.module_from_spec(_spec)
_billing.__package__ = "tva_intracom"
# IMPORTANT : le module doit être enregistré dans sys.modules AVANT exec_module().
# Sans cette ligne, @dataclass (utilisé dans billing.py) ne retrouve pas son
# module via sys.modules[cls.__module__] et plante avec
# "AttributeError: 'NoneType' object has no attribute '__dict__'".
sys.modules[_spec.name] = _billing
_spec.loader.exec_module(_billing)

handle_stripe_webhook_event = _billing.handle_stripe_webhook_event


def _verify_webhook_auth(headers: dict) -> bool:
    """Vérifie l'authentification du webhook.
    
    Retourne True si l'authentification est valide, False sinon.
    
    Vérifie:
    1. API key secrète (WEBHOOK_API_KEY)
    2. IP whitelist (WEBHOOK_IP_WHITELIST) si configurée
    """
    # Vérification de l'API key
    if _WEBHOOK_API_KEY:
        api_key = headers.get("X-Webhook-API-Key", "")
        if api_key != _WEBHOOK_API_KEY:
            print("[stripe_webhook] Auth failed: Invalid API key", file=sys.stderr)
            return False
    
    # Vérification de l'IP whitelist
    if _WEBHOOK_IP_WHITELIST:
        # Récupérer l'IP réelle (peut être dans X-Forwarded-For derrière un proxy)
        client_ip = headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not client_ip:
            client_ip = headers.get("X-Real-IP", "")
        
        if client_ip not in _WEBHOOK_IP_WHITELIST:
            print(f"[stripe_webhook] Auth failed: IP not in whitelist: {client_ip}", file=sys.stderr)
            return False
    
    return True


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # SÉCURITÉ (audit 2026-09-22, ÉLEVÉ #9) : Vérification de l'authentification
        # avant de traiter le webhook Stripe
        if not _verify_webhook_auth(self.headers):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "unauthorized"}')
            return
        
        content_length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(content_length)
        sig_header = self.headers.get("Stripe-Signature", "")

        try:
            handle_stripe_webhook_event(payload, sig_header)
        except Exception as exc:
            # Le détail complet n'est JAMAIS renvoyé dans la réponse HTTP
            # publique : un webhook Stripe est un endpoint exposé sans
            # authentification applicative.
            #
            # SÉCURITÉ (fuite d'info dans les logs) : par défaut on logge un
            # message générique + le type d'exception + l'event id Stripe si
            # extractible (best-effort, échec silencieux sinon — le payload
            # n'est pas fiable tant que la signature n'est pas vérifiée, donc
            # on ne fait que lire un champ pour le corrélationner aux logs
            # Stripe, sans agir dessus). Le traceback complet (potentiellement
            # porteur de fragments du payload via str(exc)) n'est loggé que si
            # STRIPE_WEBHOOK_DEBUG_LOGS=1 est explicitement positionné
            # (test/staging uniquement, jamais en production).
            _event_id = None
            try:
                _event_id = json.loads(payload).get("id")
            except Exception:
                pass
            if _DEBUG_LOGS:
                print(traceback.format_exc(), file=sys.stderr)
            else:
                print(
                    f"[stripe_webhook] Échec de traitement — "
                    f"event_id={_event_id or 'inconnu'} "
                    f"exception={type(exc).__name__}",
                    file=sys.stderr,
                )
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "webhook processing failed"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received": true}')