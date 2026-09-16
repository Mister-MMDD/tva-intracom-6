console.log("Moteur TVA Intracommunautaire — Design System Modernisé Activé.");

document.addEventListener("DOMContentLoaded", () => {
    // Initialize AOS (Animate On Scroll)
    if (typeof AOS !== 'undefined') {
        AOS.init({
            duration: 800,
            easing: 'ease-out-cubic',
            once: true,
            offset: 50,
            delay: 0
        });
    }
    // Menu hamburger (mobile) : ouverture / fermeture + fermeture au clic sur un lien
    const menuToggle = document.getElementById("menu-toggle");
    const menuLinksEl = document.getElementById("menu-links");
    if (menuToggle && menuLinksEl) {
        const closeMenu = () => {
            menuLinksEl.classList.remove("open");
            menuToggle.setAttribute("aria-expanded", "false");
            menuToggle.setAttribute("aria-label", "Ouvrir le menu");
        };
        const openMenu = () => {
            menuLinksEl.classList.add("open");
            menuToggle.setAttribute("aria-expanded", "true");
            menuToggle.setAttribute("aria-label", "Fermer le menu");
        };
        menuToggle.addEventListener("click", () => {
            const isOpen = menuLinksEl.classList.contains("open");
            if (isOpen) closeMenu(); else openMenu();
        });
        // Ferme le menu après un clic sur un lien (évite de rester ouvert après navigation)
        menuLinksEl.querySelectorAll("a").forEach(link => {
            link.addEventListener("click", closeMenu);
        });
        // Repasse en état "fermé" propre si on repasse en desktop après redimensionnement
        window.addEventListener("resize", () => {
            if (window.innerWidth > 768) closeMenu();
        });
    }

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
        const openLabel = closedLabel.replace(/^(Afficher|Voir)/, "Masquer");

        button.addEventListener("click", () => {
            const isOpen = card.getAttribute("data-open") === "true";

            // Toggle de l'état
            card.setAttribute("data-open", String(!isOpen));

            // Animation ou affichage
            if (isOpen) {
                details.style.display = "none";
                button.textContent = closedLabel;
                button.classList.remove("active");
            } else {
                details.style.display = "block";
                button.textContent = openLabel;
                button.classList.add("active");
            }
        });
    });

    // Counter Animation for statistics
    const counters = document.querySelectorAll('.counter');
    if (counters.length > 0) {
        const animateCounter = (counter) => {
            const target = parseInt(counter.getAttribute('data-target'));
            if (!target) return;
            
            const duration = 2000; // 2 seconds
            const step = target / (duration / 16); // 60fps
            let current = 0;

            const updateCounter = () => {
                current += step;
                if (current < target) {
                    counter.textContent = Math.floor(current).toLocaleString();
                    requestAnimationFrame(updateCounter);
                } else {
                    counter.textContent = target.toLocaleString();
                }
            };

            updateCounter();
        };

        // Intersection Observer for counters
        const counterObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    animateCounter(entry.target);
                    counterObserver.unobserve(entry.target);
                }
            });
        }, { threshold: 0.5 });

        counters.forEach(counter => {
            if (counter.getAttribute('data-target')) {
                counterObserver.observe(counter);
            }
        });
    }

    // Enhanced Card 3D Effect
    const cards3D = document.querySelectorAll('.card-3d');
    cards3D.forEach(card => {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            const centerX = rect.width / 2;
            const centerY = rect.height / 2;
            
            const rotateX = (y - centerY) / 10;
            const rotateY = (centerX - x) / 10;
            
            card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale(1.02)`;
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = 'perspective(1000px) rotateX(0) rotateY(0) scale(1)';
        });
    });

    // Enhanced Navigation with scroll effect
    const nav = document.querySelector('.menu');
    if (nav) {
        let lastScroll = 0;
        
        window.addEventListener('scroll', () => {
            const currentScroll = window.pageYOffset;
            
            if (currentScroll > 100) {
                nav.style.background = 'rgba(15, 23, 42, 0.95)';
                nav.style.backdropFilter = 'blur(20px)';
                nav.style.boxShadow = '0 4px 20px rgba(0, 0, 0, 0.1)';
            } else {
                nav.style.background = 'var(--nav-bg)';
                nav.style.backdropFilter = 'blur(8px)';
                nav.style.boxShadow = 'none';
            }
            
            lastScroll = currentScroll;
        });
    }

    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });

    // Reading progress indicator
    const progressBar = document.createElement('div');
    progressBar.className = 'reading-progress';
    progressBar.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        height: 3px;
        background: linear-gradient(90deg, #3b82f6, #8b5cf6);
        width: 0%;
        z-index: 9999;
        transition: width 0.1s;
    `;
    document.body.appendChild(progressBar);

    window.addEventListener('scroll', () => {
        const scrollTop = window.pageYOffset;
        const docHeight = document.body.scrollHeight - window.innerHeight;
        const progress = (scrollTop / docHeight) * 100;
        progressBar.style.width = progress + '%';
    });
});
