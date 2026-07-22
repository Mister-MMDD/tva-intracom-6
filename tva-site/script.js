console.log("Moteur TVA Intracommunautaire — Design System Activé.");

document.addEventListener("DOMContentLoaded", () => {
    // Bascule de thème clair / sombre
    const themeToggle = document.getElementById("theme-toggle");
    const root = document.documentElement;

    const applyThemeIcon = () => {
        if (!themeToggle) return;
        const isDark = root.getAttribute("data-theme") === "dark";
        themeToggle.textContent = isDark ? "☀️" : "🌙";
        themeToggle.setAttribute("aria-label", isDark ? "Passer en mode clair" : "Passer en mode sombre");

        // Mise à jour du thème Stripe si présent
        const pricingTable = document.querySelector('stripe-pricing-table');
        if (pricingTable) {
            pricingTable.setAttribute('theme', isDark ? 'dark' : 'light');
        }
    };
    applyThemeIcon();

    if (themeToggle) {
        themeToggle.addEventListener("click", () => {
            const isDark = root.getAttribute("data-theme") === "dark";
            if (isDark) {
                root.removeAttribute("data-theme");
                localStorage.setItem("theme", "light");
            } else {
                root.setAttribute("data-theme", "dark");
                localStorage.setItem("theme", "dark");
            }
            applyThemeIcon();
        });
    }

    // Highlighting active menu link
    const currentPath = window.location.pathname.split("/").pop() || "index.html";
    const menuLinks = document.querySelectorAll(".menu a");
    menuLinks.forEach(link => {
        const href = link.getAttribute("href");
        if (href === currentPath) {
            link.classList.add("active");
        }
    });

    // Search functionality (avec état "aucun résultat")
    const searchInput = document.getElementById("site-search");
    if (searchInput) {
        const resultsContainer = document.querySelector(".container") || document.body;
        let emptyStateEl = document.getElementById("search-empty-state");
        if (!emptyStateEl) {
            emptyStateEl = document.createElement("p");
            emptyStateEl.id = "search-empty-state";
            emptyStateEl.textContent = "Aucun résultat trouvé pour cette recherche.";
            emptyStateEl.style.display = "none";
            emptyStateEl.style.textAlign = "center";
            emptyStateEl.style.color = "var(--text-muted)";
            emptyStateEl.style.margin = "40px 0";
            resultsContainer.appendChild(emptyStateEl);
        }

        searchInput.addEventListener("input", (e) => {
            const term = e.target.value.toLowerCase();
            const allElements = document.querySelectorAll(".card, .security-shoutout, .simulator-section");
            let visibleCount = 0;
            allElements.forEach(el => {
                const text = el.innerText.toLowerCase();
                const matches = text.includes(term);
                el.style.display = matches ? "" : "none";
                if (matches) visibleCount++;
            });
            emptyStateEl.style.display = (term && visibleCount === 0) ? "block" : "none";
        });
    }

    // Tutorial cards toggle (réutilisé aussi par l'accordéon FAQ)
    const cards = document.querySelectorAll(".card.interactive");

    cards.forEach(card => {
        const button = card.querySelector(".toggle-button");
        const details = card.querySelector(".details");

        if (!button || !details) return;

        // Configuration initiale propre : on mémorise le libellé "fermé" propre à ce bouton
        details.style.display = "none";
        const closedLabel = button.textContent;
        const openLabel = closedLabel.replace(/^Afficher/, "Masquer");

        button.addEventListener("click", () => {
            const isOpen = card.getAttribute("data-open") === "true";

            // Toggle de l'état
            card.setAttribute("data-open", String(!isOpen));

            // Animation ou affichage
            if (isOpen) {
                details.style.display = "none";
                button.textContent = closedLabel;
                button.style.background = "";
            } else {
                details.style.display = "block";
                button.textContent = openLabel;
                button.style.background = "#e2e8f0";
            }
        });
    });
});
