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
"""
import importlib.util
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

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


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(content_length)
        sig_header = self.headers.get("Stripe-Signature", "")

        try:
            handle_stripe_webhook_event(payload, sig_header)
        except Exception:
            # Le traceback complet est loggé côté serveur (logs Vercel) pour
            # diagnostic, mais JAMAIS renvoyé dans la réponse HTTP publique :
            # un webhook Stripe est un endpoint exposé sans authentification
            # applicative, un traceback y révélerait des chemins internes et
            # potentiellement des fragments de payload.
            print(traceback.format_exc(), file=sys.stderr)
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "webhook processing failed"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received": true}')