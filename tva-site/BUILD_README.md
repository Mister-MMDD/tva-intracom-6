# Système de build — tva-site

Pour éviter de dupliquer le nav/header/footer dans 13 fichiers HTML (source
d'incohérences à chaque modification), le site est maintenant généré par
`build.py` à partir de fragments partagés.

## Structure

```
_includes/head.html      -> squelette <head> (title/description/scripts paramétrables)
_includes/footer.html    -> footer identique partout
src/meta/pages.json      -> métadonnées par page (title, description, scripts spécifiques)
src/pages/<page>.html    -> contenu unique de chaque page (header + main)
build.py                 -> assemble tout et régénère les .html à la racine
```

Le nav (liens + bouton de thème + mise en avant du lien actif) est généré
directement dans `build.py` pour éviter tout risque de désynchronisation
entre pages.

## Utilisation

```bash
python3 build.py
```

Régénère les 13 fichiers `.html` à la racine de `tva-site/`.

## Pour modifier le site

- **Texte d'une page précise** -> éditer `src/pages/<page>.html`
- **Nav (ajouter/renommer un lien)** -> éditer `NAV_LINKS` dans `build.py`
- **Titre/meta description d'une page** -> éditer `src/meta/pages.json`
- **Footer** -> éditer `_includes/footer.html`
- Puis relancer `python3 build.py`

Les fichiers `.html` générés à la racine restent des fichiers statiques
classiques (aucun JS requis côté visiteur pour le rendu du nav/footer -
compatible SEO et sans-JS). Le build est une étape de développement, pas
une étape d'exécution en production.
