console.log("Moteur TVA Intracommunautaire — Design System Activé.");

document.addEventListener("DOMContentLoaded", () => {
    // Highlighting active menu link
    const currentPath = window.location.pathname.split("/").pop() || "index.html";
    const menuLinks = document.querySelectorAll(".menu a");
    menuLinks.forEach(link => {
        const href = link.getAttribute("href");
        if (href === currentPath) {
            link.classList.add("active");
        }
    });

    // Search functionality
    const searchInput = document.getElementById("site-search");
    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            const term = e.target.value.toLowerCase();
            const allElements = document.querySelectorAll(".card, .security-shoutout, .simulator-section");
            allElements.forEach(el => {
                const text = el.innerText.toLowerCase();
                if (text.includes(term)) {
                    el.style.display = "";
                } else {
                    el.style.display = "none";
                }
            });
        });
    }

    // Tutorial cards toggle
    const cards = document.querySelectorAll(".card.interactive");

    cards.forEach(card => {
        const button = card.querySelector(".toggle-button");
        const details = card.querySelector(".details");

        if (!button || !details) return;

        // Configuration initiale propre
        details.style.display = "none";

        button.addEventListener("click", () => {
            const isOpen = card.getAttribute("data-open") === "true";
            
            // Toggle de l'état
            card.setAttribute("data-open", String(!isOpen));
            
            // Animation ou affichage
            if (isOpen) {
                details.style.display = "none";
                button.textContent = "Afficher le tutoriel";
                button.style.background = "";
            } else {
                details.style.display = "block";
                button.textContent = "Masquer le tutoriel";
                button.style.background = "#e2e8f0";
            }
        });
    });
});

// Base de données des résultats du moteur fiscal
const VAT_RULES_DATABASE = {
    "B2B_REVERSE_CHARGE": {
        code: "B2B_REVERSE_CHARGE",
        desc: "Exonération de TVA pour Livraison Intra-UE (Autoliquidation preneur)",
        rule: "Directive 2006/112/CE - Article 138. Exonération liée au transfert physique transfrontalier de biens entre assujettis.",
        action: "Génération automatique d'une facture HT. Mention légale obligatoire : 'Autoliquidation - Art. 194 / Art. 138 Directive TVA'. Contrôle et journalisation automatique du token de preuve VIES."
    },
    "DOMESTIC_OR_B2C": {
        code: "DOMESTIC (Reclassé)",
        desc: "Invalidation VIES - Reclassification automatique en Vente Locale / B2C",
        rule: "Article 259 du CGI / Directives VIES. Faute de preuve du statut d'assujetti du preneur, l'opération perd son bénéfice d'exonération.",
        action: "Le moteur applique le taux de TVA du pays de départ de la marchandise. L'opération est basculée sur la ligne CA3 nationale standard."
    },
    "DOMESTIC": {
        code: "DOMESTIC",
        desc: "Vente Locale Standard (B2C / B2B National)",
        rule: "Article 256 et suivants du Code Général des Impôts (CGI). Le stock et l'acheteur se trouvent dans le même pays.",
        action: "Application du taux normal ou réduit du pays de résidence. Intégration directe dans le rapport de déclaration CA3 national."
    },
    "OSS_B2C": {
        code: "OSS_B2C",
        desc: "Vente à Distance Intra-communautaire (Régime Guichet Unique OSS)",
        rule: "Paquet TVA sur le Commerce Électronique (01/07/2021). Seuil communautaire unique de 10 000 € dépassé.",
        action: "Calcul automatique du taux de TVA exact du pays de destination de l'acheteur (ex: 21% pour l'Espagne, 22% pour l'Italie). Agrégation des données dans le fichier d'export XML officiel pour le portail de télé-déclaration OSS."
    },
    "DOMESTIC_ORIGIN": {
        code: "DOMESTIC_ORIGIN",
        desc: "Vente B2C sous le seuil des 10 000 €",
        rule: "Dérogation micro-entreprise / Seuil de tolérance e-commerce transfrontalier.",
        action: "Maintien de la collecte au taux de TVA de votre pays d'établissement d'origine. Aucune déclaration OSS requise tant que le seuil global n'est pas franchi."
    },
    "DEEMED_SUPPLIER": {
        code: "DEEMED_SUPPLIER",
        desc: "Fournisseur Présumé — TVA collectée par la Marketplace",
        rule: "Article 14 bis de la directive TVA (Régime Marketplaces). Ventes transfrontalières ou imports de valeur ≤ 150 €.",
        action: "Le moteur isole cette transaction : la TVA est collectée et reversée directement par Amazon (ou la marketplace). L'opération est déclarée comme 'Exonérée pour le vendeur tiers' afin de ne pas payer deux fois."
    },
    "IMPORT_STANDARD": {
        code: "IMPORT_STANDARD",
        desc: "Importation Classique de Marchandises (Hors Union)",
        rule: "Régime douanier commun d'importation. Biens excédant 150 € ou hors guichet IOSS.",
        action: "Calcul de la TVA à l'importation lors du dédouanement. Liaison recommandée avec vos imports de fichiers FEC pour autoliquider la TVA à l'import sur la déclaration CA3 française (obligatoire)."
    }
};

document.addEventListener("DOMContentLoaded", () => {
    // Cibler les éléments du simulateur
    const steps = document.querySelectorAll(".sim-step");
    const resultBox = document.getElementById("sim-result-box");
    const resetBtn = document.getElementById("btn-reset-sim");

    steps.forEach(step => {
        const cards = step.querySelectorAll(".sim-card");

        cards.forEach(card => {
            card.addEventListener("click", () => {
                // 1. Marquer la carte cliquée comme active et désactiver les autres de la même étape
                cards.forEach(c => {
                    c.classList.remove("active");
                    c.classList.add("disabled");
                });
                card.classList.remove("disabled");
                card.classList.add("active");

                // 2. Déterminer la suite du chemin
                const nextStepId = card.getAttribute("data-next");
                const resultKey = card.getAttribute("data-result");

                // Masquer toutes les étapes ultérieures si l'utilisateur change d'avis au milieu
                hideSubsequentSteps(step);

                if (nextStepId) {
                    // Afficher l'étape suivante
                    const nextStep = document.getElementById(nextStepId);
                    if (nextStep) {
                        nextStep.style.display = "block";
                        nextStep.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                    resultBox.style.display = "none";
                } else if (resultKey) {
                    // Afficher le résultat de l'arborescence
                    displaySimulationResult(resultKey);
                }

                // Afficher le bouton de réinitialisation
                resetBtn.style.display = "inline-block";
            });
        });
    });

    // Fonction pour masquer les étapes enfants
    function hideSubsequentSteps(currentStep) {
        let current = currentStep.nextElementSibling;
        while (current && current.classList.contains("sim-step")) {
            current.style.display = "none";
            const currentCards = current.querySelectorAll(".sim-card");
            currentCards.forEach(c => c.classList.remove("active", "disabled"));
            current = current.nextElementSibling;
        }
    }

    // Fonction pour afficher le verdict fiscal
    function displaySimulationResult(key) {
        const data = VAT_RULES_DATABASE[key];
        if (!data) return;

        document.getElementById("res-code").textContent = data.code;
        document.getElementById("res-desc").textContent = data.desc;
        document.getElementById("res-rule").textContent = data.rule;
        document.getElementById("res-action").textContent = data.action;

        resultBox.style.display = "block";
        resultBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // Fonction Reset
    resetBtn.addEventListener("click", () => {
        steps.forEach((step, index) => {
            step.style.display = index === 0 ? "block" : "none";
            step.querySelectorAll(".sim-card").forEach(c => c.classList.remove("active", "disabled"));
        });
        resultBox.style.display = "none";
        resetBtn.style.display = "none";
        window.scrollTo({ top: steps[0].offsetTop - 100, behavior: 'smooth' });
    });
});