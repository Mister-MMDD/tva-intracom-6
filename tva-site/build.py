#!/usr/bin/env python3
"""
Build script — tva-site
========================
Assemble les pages finales à partir de fragments partagés pour éviter
la duplication du nav/head/footer sur les 13 pages du site.

Structure source :
  _includes/head.html     -> squelette <head> paramétrable ({{TITLE}}, {{DESCRIPTION}}, {{EXTRA_HEAD}})
  _includes/footer.html   -> footer identique sur toutes les pages
  src/meta/pages.json     -> métadonnées par page (title, description, extra_head, extra_foot)
  src/pages/<page>.html   -> contenu unique de chaque page (header + main)

Usage :
  python3 build.py

Régénère les fichiers .html à la racine de tva-site/. Le nav est généré
directement ici (pas un fichier séparé) afin de marquer proprement le
lien actif (class="active" + aria-current="page") sans templating fragile.

Pour ajouter une page : créer src/pages/nouvelle-page.html, ajouter une
entrée dans NAV_LINKS et dans src/meta/pages.json, puis relancer le build.
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

NAV_LINKS = [
    ("index.html", "Accueil"),
    ("documentation.html", "Documentation"),
    ("cas-pratiques.html", "Cas pratiques"),
    ("tarifs.html", "Tarifs"),
    ("securite.html", "Sécurité"),
    ("glossaire.html", "Glossaire"),
    ("faq.html", "FAQ"),
    ("regimes.html", "Régimes spéciaux"),
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

    built = []
    for fname, meta in meta_all.items():
        page_id = fname.replace(".html", "")
        content_path = os.path.join(ROOT, "src", "pages", f"{page_id}.html")
        content = open(content_path, encoding="utf-8").read().strip()

        head = (
            head_tpl.replace("{{TITLE}}", meta["title"])
            .replace("{{DESCRIPTION}}", meta["description"])
            .replace("{{EXTRA_HEAD}}", meta["extra_head"])
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


if __name__ == "__main__":
    main()
