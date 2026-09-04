#!/usr/bin/env python3
"""
Build script — tva-site
========================
Assemble les pages finales à partir de fragments partagés pour éviter
la duplication du nav/head/footer sur les 12 pages du site.

Structure source :
  _includes/head.html     -> squelette <head> paramétrable ({{TITLE}}, {{DESCRIPTION}}, {{EXTRA_HEAD}}, {{CSS_HASH}}, {{JS_HASH}}, {{CANONICAL_URL}}, {{OG_IMAGE}})
  _includes/footer.html   -> footer identique sur toutes les pages
  src/meta/pages.json     -> métadonnées par page (title, description, extra_head, extra_foot)
  src/pages/<page>.html   -> contenu unique de chaque page (header + main)

Usage :
  python3 build.py

Régénère les fichiers .html à la racine de tva-site/, ainsi que
style.min.css (version minifiée de style.css, servie en prod).
Le nav est généré directement ici (pas un fichier séparé) afin de
marquer proprement le lien actif (class="active" + aria-current="page")
sans templating fragile.

Cache-busting : {{CSS_HASH}}/{{JS_HASH}} sont calculés automatiquement
(hash MD5 du contenu, 8 car.) -> plus besoin de bump manuel du ?v=,
le paramètre change dès que le fichier change.

Open Graph / Twitter Card : canonical + og:*/twitter:* générés pour
chaque page (SITE_URL + OG_IMAGE en constantes en tête de ce fichier,
à mettre à jour si le domaine ou le visuel changent).

Minification : uniquement le CSS (regex sûre : commentaires + espaces).
Le JS n'est PAS minifié (une minification par regex serait risquée sur
du JS — nécessiterait un vrai outil type esbuild/terser, non fait ici).

Pour ajouter une page : créer src/pages/nouvelle-page.html, ajouter une
entrée dans NAV_LINKS et dans src/meta/pages.json, puis relancer le build.
"""
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_URL = "https://www.tvacalculator.eu"
OG_IMAGE = "https://filedn.eu/lwpYsKy925D7JUdt4q7kB0L/tva-site/logo.svg"


def file_hash(path: str) -> str:
    """Hash court (8 car.) du contenu du fichier, pour cache-busting automatique.
    Change uniquement si le contenu change -> plus besoin de bump manuel du ?v=."""
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def minify_css(css: str) -> str:
    """Minification CSS sûre (commentaires + espaces superflus).
    N'affecte pas la version lisible en source (fichier séparé en sortie)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,])\s*", r"\1", css)
    css = re.sub(r";}", "}", css)
    return css.strip()

NAV_LINKS = [
    ("index.html", "Accueil"),
    ("documentation.html", "Documentation"),
    ("tarifs.html", "Tarifs"),
    ("securite.html", "Sécurité"),
    ("glossaire.html", "Glossaire"),
    ("faq.html", "FAQ"),
    ("regimes.html", "Liste des régimes"),
    ("arbre-decision.html", "Arbre de décision"),
    ("interface.html", "Interface moteur"),
    ("tutoriels.html", "Simulateur & Tutoriels"),
]


def build_nav(active_page: str) -> str:
    links = []
    for href, label in NAV_LINKS:
        if href == active_page:
            links.append(f'    <a href="{href}" class="active" aria-current="page">{label}</a>')
        else:
            links.append(f'    <a href="{href}">{label}</a>')
    links_html = "\n".join(links)
    return f'''<nav class="menu" aria-label="Navigation principale">
{links_html}
    <div class="search-container">
        <input type="text" id="site-search" placeholder="Rechercher..." aria-label="Rechercher dans le site">
        <button id="theme-toggle" class="theme-toggle-btn" type="button" aria-label="Passer en mode sombre">🌙</button>
    </div>
</nav>'''


def main():
    head_tpl = open(os.path.join(ROOT, "_includes", "head.html"), encoding="utf-8").read()
    footer_html = open(os.path.join(ROOT, "_includes", "footer.html"), encoding="utf-8").read().strip()
    meta_all = json.load(open(os.path.join(ROOT, "src", "meta", "pages.json"), encoding="utf-8"))

    # Minification CSS -> fichier séparé (style.css source reste lisible pour l'édition)
    css_src_path = os.path.join(ROOT, "style.css")
    css_src = open(css_src_path, encoding="utf-8").read()
    css_min_path = os.path.join(ROOT, "style.min.css")
    open(css_min_path, "w", encoding="utf-8").write(minify_css(css_src))

    # Cache-busting automatique par hash de contenu (plus de version manuelle à incrémenter)
    css_hash = file_hash(css_min_path)
    js_hash = file_hash(os.path.join(ROOT, "script.js"))

    built = []
    for fname, meta in meta_all.items():
        page_id = fname.replace(".html", "")
        content_path = os.path.join(ROOT, "src", "pages", f"{page_id}.html")
        content = open(content_path, encoding="utf-8").read().strip()

        canonical_url = SITE_URL + "/" if fname == "index.html" else f"{SITE_URL}/{fname}"

        head = (
            head_tpl.replace("{{TITLE}}", meta["title"])
            .replace("{{DESCRIPTION}}", meta["description"])
            .replace("{{EXTRA_HEAD}}", meta["extra_head"])
            .replace("{{CSS_HASH}}", css_hash)
            .replace("{{JS_HASH}}", js_hash)
            .replace("{{CANONICAL_URL}}", canonical_url)
            .replace("{{OG_IMAGE}}", OG_IMAGE)
        )
        # Nettoyage : ligne vide si pas d'extra_head
        head = head.replace("\n\n</head>", "\n</head>")

        nav = build_nav(fname)
        extra_foot = meta.get("extra_foot", "")
        extra_foot_block = f"\n{extra_foot}" if extra_foot else ""

        page = f'''{head}
<body>

<a href="#main-content" class="skip-link">Aller au contenu principal</a>

{nav}

<main id="main-content">
{content}
</main>

{footer_html}
{extra_foot_block}
</body>
</html>
'''
        out_path = os.path.join(ROOT, fname)
        open(out_path, "w", encoding="utf-8").write(page)
        built.append(fname)

    print(f"{len(built)} pages générées : {', '.join(built)}")
    print(f"style.min.css régénéré (hash v={css_hash}) — script.js non minifié (hash v={js_hash})")


if __name__ == "__main__":
    main()
