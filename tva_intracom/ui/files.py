"""Cache et signature des fichiers uploadés (extrait de app.py, aucune
modification de comportement) — regroupe `_CachedUploadedFile` et
`_upload_sig`, utilisés par app.py pour survivre à un rerun Streamlit
"interne" (voir `tva_intracom.ui.rerun_utils.preserve_upload_rerun`) sans
re-décompresser ni re-hasher inutilement de gros fichiers Amazon (jusqu'à
100 Mo).
"""
from __future__ import annotations

import gzip
import hashlib


# ── Filet de sécurité : widget vide mais fichiers déjà chargés en session ───
# Un changement de pays d'origine (qui déclenche un rerun explicite en plein
# rendu de la sidebar, voir sidebar.py) peut faire ressortir `uploaded_files`
# vide, alors même que rien n'a été retiré côté utilisateur. On ne réutilise
# le cache d'octets QUE si ce rerun a été signalé comme "interne" (via
# `preserve_upload_rerun()`, voir rerun_utils.py) — sinon, un widget vide
# signifie un vrai retrait du fichier par l'utilisateur, et tout l'état
# dérivé (résultats calculés, période détectée, tableaux) doit être purgé
# pour ne pas rester affiché après suppression.
# Les octets bruts mis en cache (pour survivre à un rerun interne, voir
# rerun_utils.py) sont gardés compressés (gzip) plutôt qu'en clair : sur des
# rapports Amazon/Mirakl/Shopify réels (texte, colonnes très répétitives —
# codes pays, ASIN, dates), le ratio mesuré est de l'ordre de 6-6.5x
# (~15% de la taille d'origine), pour un coût CPU de l'ordre de quelques
# secondes même au pire cas (fichier de 100 Mo, la limite `maxUploadSize`).
# La décompression n'a lieu que dans `getvalue()`, c'est-à-dire seulement
# quand un re-parsing est réellement déclenché (changement d'encodage, de
# devise, de catalogue ASIN...) — jamais à chaque rerun. La taille d'origine
# (`size`) est gardée à côté du blob compressé pour que la clé de
# déduplication/cache (name, size) reste identique à celle d'un nouvel
# upload, sans avoir à décompresser juste pour connaître la taille.
class _CachedUploadedFile:
    # `_content_hash` porte le hash MD5 déjà connu (calculé une seule fois,
    # au moment de la compression initiale — voir plus bas) pour que
    # `_upload_sig()` puisse le renvoyer directement SANS décompresser tout
    # le blob gzip. Avant ce correctif, `_upload_sig()` appelait
    # `getvalue()` (qui décompresse l'intégralité du fichier, potentiellement
    # 100 Mo) simplement pour hasher les 128 premiers Ko — et ce, plusieurs
    # fois par rerun Streamlit (dédup, clé de cache de parsing...), donc à
    # chaque clic/filtre/changement de langue tant que les fichiers restent
    # servis depuis ce cache interne. Le hash étant invariant tant que le
    # contenu ne change pas (c'est justement ce qu'il sert à détecter), le
    # porter directement sur l'objet évite ce travail répété pour rien.
    __slots__ = ("name", "size", "_compressed", "_content_hash")

    def __init__(self, name: str, compressed: bytes, size: int, content_hash: str) -> None:
        self.name = name
        self.size = size
        self._compressed = compressed
        self._content_hash = content_hash

    def getvalue(self) -> bytes:
        return gzip.decompress(self._compressed)


def _upload_sig(f) -> tuple:
    """Signature de contenu d'un fichier uploadé, utilisée partout où on
    doit détecter un changement (cache de compression, dédup, cache de
    parsing, cache de calcul TVA).

    (name, size) seul ne suffit pas : deux versions d'un même fichier (ex.
    correction d'une erreur dans le CSV, sans changer le nom) peuvent
    partager la même taille en octets — plus fréquent qu'on ne le croit sur
    de gros volumes. Dans ce cas l'app ne détectait pas le changement et
    réutilisait silencieusement les anciennes données mises en cache
    (bytes compressés, résultat de parsing, résultat de calcul TVA).

    On ajoute un hash MD5 du DÉBUT du fichier seulement (128 Ko), pas du
    fichier entier : cette signature est recalculée à CHAQUE rerun
    Streamlit (chaque clic, changement de filtre...) pour détecter un
    changement — hasher le fichier entier à chaque rerun réintroduirait,
    pour les plus gros fichiers Amazon (jusqu'à 100 Mo), le coût CPU que le
    design (name, size) cherchait justement à éviter. Un hash partiel sur
    le début du fichier suffit à couvrir le cas réaliste (contenu modifié,
    même taille) sans ce coût.

    Cas `_CachedUploadedFile` (fichiers restaurés depuis le cache interne
    après un rerun "interne", voir `preserve_upload_rerun()`) : le hash a
    déjà été calculé une fois lors de la compression initiale et est porté
    par l'objet (`_content_hash`). On le réutilise tel quel plutôt que de
    rappeler `f.getvalue()`, qui décompresserait inutilement tout le blob
    gzip (jusqu'à 100 Mo) rien que pour en relire les 128 premiers Ko — ce
    correctif évite cette décompression répétée à chaque rerun (chaque
    clic, changement de langue, filtre...) tant que le fichier reste servi
    depuis ce cache.
    """
    if isinstance(f, _CachedUploadedFile):
        return (f.name, f.size, f._content_hash)
    # BUGFIX (fiabilité, voir README - évolution.md) : hasher uniquement les
    # 128 premiers Ko ne détecte pas une modification tombant plus loin dans
    # le fichier (ex. correction d'un montant sur la dernière ligne d'un CSV
    # de 100 Mo) — l'app pouvait alors réutiliser silencieusement d'anciens
    # résultats de parsing/calcul sur un fichier pourtant modifié. On ajoute
    # le hash des 128 derniers Ko (bornes qui se chevauchent sans problème
    # sur les petits fichiers, `getvalue()` n'étant appelé qu'une fois) —
    # coût toujours borné (256 Ko max, pas le fichier entier) donc pas de
    # régression sur le design (name, size) + hash partiel.
    _content = f.getvalue()
    _head = _content[:131072]
    _tail = _content[-131072:] if len(_content) > 131072 else b""
    return (f.name, f.size, hashlib.md5(_head + _tail).hexdigest())


# ── Contrôle de contenu des imports (audit sécurité 2026-09-13, MOYEN #4) ───
# Jusqu'ici, seule l'extension déclarée (`type=["csv","tsv","txt"]` du
# widget Streamlit) filtrait les imports — un fichier peut être renommé en
# `.csv` quel que soit son contenu réel. On n'a pas de lib magic dans
# requirements.txt (Streamlit Cloud ne garantit pas libmagic au niveau OS),
# donc pas de vraie détection MIME RFC — mais on bloque les cas concrets à
# risque : un binaire (exécutable, PDF, image, archive/Office) déguisé en
# texte par simple renommage, et tout contenu qui n'est même pas décodable
# comme texte dans un encodage plausible pour un export Amazon/Mirakl/
# Shopify (ce que le parsing CSV en aval exigerait de toute façon).
_BINARY_MAGIC_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",                   # exécutable Windows PE
    b"\x7fELF",               # exécutable Linux ELF
    b"PK\x03\x04",           # ZIP / Office OOXML (xlsx, docx...) / JAR
    b"%PDF",                 # PDF
    b"\x89PNG\r\n\x1a\n",     # PNG
    b"\xff\xd8\xff",          # JPEG
    b"GIF87a",
    b"GIF89a",
    b"\x1f\x8b",              # GZIP
    b"Rar!\x1a\x07",          # RAR
    b"7z\xbc\xaf\x27\x1c",    # 7-Zip
)

# Encodages plausibles pour un export e-commerce (Excel FR exporte souvent
# en cp1252/latin-1, pas seulement en UTF-8) — voir _file_encoding_choice
# dans app.py, qui gère déjà ce choix côté parsing.
_TEXT_DECODE_CANDIDATES: tuple[str, ...] = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


def sniff_upload_rejection_reason(file_head: bytes) -> str | None:
    """Contrôle de contenu minimal sur le DÉBUT d'un fichier déclaré
    csv/tsv/txt. Retourne None si accepté, sinon une clé i18n à afficher.

    `file_head` : les premiers ko du fichier suffisent (toutes les
    signatures binaires ci-dessus tiennent dans les 16 premiers octets, et
    un fichier réellement texte doit être décodable dès le début)."""
    if b"\x00" in file_head:
        return "upload_rejected_binary"

    for sig in _BINARY_MAGIC_SIGNATURES:
        if file_head.startswith(sig):
            return "upload_rejected_binary"

    for enc in _TEXT_DECODE_CANDIDATES:
        try:
            file_head.decode(enc)
            return None
        except UnicodeDecodeError:
            continue

    return "upload_rejected_binary"
