"""Diagnostic : réponses BRUTES de l'API VIES REST et leur classement par le moteur.

Pourquoi : `vies_engine.check_vat()` ne lit que les clés `valid`/`isValid`,
`name`, `address`, `error` et `errorWrappers`. Aucune fixture réelle du dépôt
ne montre le corps complet d'une réponse VIES. Ce script interroge le VRAI
service (aucun cache, aucune base, aucun secret requis) pour répondre à trois
questions :
  1. Quelles clés contient réellement la réponse (ex. `userError`) ?
  2. Un numéro invalide revient-il avec name/address vides ou avec un
     placeholder (ex. "---") ? Dans ce second cas `_is_empty_response()` ne
     le reconnaît pas comme "vide".
  3. Quand l'État membre est indisponible, comment la panne est-elle codée,
     et le moteur la classe-t-il bien comme transitoire (non concluante) ?

Usage (depuis la racine du dépôt) :
    python scripts/diag_vies_response.py
    python scripts/diag_vies_response.py DE811128135 FR40303265045 BE0123456789

Sans argument : 3 numéros de format volontairement invalide. Passez en argument
un de vos vrais clients B2B valides ET un numéro invalide pour voir les deux
formes de réponse.

Aucun thread, aucune connexion persistante : un appel HTTP par numéro, puis
fin du processus (compatible scale-to-zero).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tva_intracom import vies_engine as v  # noqa: E402

DEFAULT_SAMPLES = ["FR00000000000", "DE000000000", "BE0000000000"]


def raw_call(country: str, number: str) -> tuple[int | None, str]:
    payload = json.dumps({"countryCode": country, "vatNumber": number}).encode()
    req = urllib.request.Request(
        v.VIES_REST_URL, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=v.DEFAULT_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # réseau, DNS, timeout
        return None, f"<{type(exc).__name__}: {exc}>"


def main(argv: list[str]) -> int:
    samples = argv or DEFAULT_SAMPLES
    for raw in samples:
        try:
            cc, num = v._clean_vat_number(raw)
        except ValueError as exc:
            print(f"\n=== {raw} : ignoré ({exc})")
            continue
        status, body = raw_call(cc, num)
        print(f"\n=== {cc}{num}  (HTTP {status})")
        try:
            parsed = json.loads(body)
            print("clés :", sorted(parsed.keys()) if isinstance(parsed, dict) else type(parsed))
            print(json.dumps(parsed, indent=2, ensure_ascii=False)[:1500])
        except ValueError:
            print("corps non JSON :", body[:500])

        res = v.check_vat(cc, num)
        print(
            "classement moteur -> "
            f"valid={res.valid!r} error={res.error!r} name={res.name!r} address={res.address!r}"
        )
        print(
            f"   _is_unreliable={v._is_unreliable(res)}  "
            f"_is_empty_response={v._is_empty_response(res)}"
        )
    print(
        "\nÀ renvoyer tel quel : si un numéro INVALIDE affiche name='---' (ou toute "
        "valeur non vide) avec _is_empty_response=False, ou si une clé "
        "d'erreur absente du code (ex. userError) apparaît, il faut adapter "
        "check_vat() — pas avant d'avoir ces sorties réelles."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
