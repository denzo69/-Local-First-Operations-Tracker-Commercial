document.querySelectorAll("[data-bs-toggle='collapse']").forEach((button) => {
    button.addEventListener("click", () => {
        const targetSelector = button.getAttribute("data-bs-target");
        const target = document.querySelector(targetSelector);

        if (target) {
            target.classList.toggle("show");
        }
    });
});

document.querySelectorAll("[data-mobile-nav-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
        const targetId = button.getAttribute("aria-controls");
        const target = targetId ? document.getElementById(targetId) : null;

        if (!target) {
            return;
        }

        const expanded = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!expanded));
        target.hidden = expanded;
    });
});

document.querySelectorAll("[data-live-filter-form]").forEach((form) => {
    const input = form.querySelector("[data-live-filter-input]");
    const target = document.querySelector(form.dataset.liveFilterTarget);
    const count = form.querySelector("[data-live-filter-count]");
    const empty = target?.querySelector("[data-live-filter-empty]");
    const items = Array.from(target?.querySelectorAll("[data-search-text]") || []);

    if (!input || !target || items.length === 0) {
        return;
    }

    const update = () => {
        const query = input.value.trim().toLowerCase();
        let visible = 0;

        items.forEach((item) => {
            const text = item.dataset.searchText.toLowerCase();
            const matches = !query || text.includes(query);
            item.classList.toggle("d-none", !matches);
            if (matches) {
                visible += 1;
            }
        });

        if (empty) {
            empty.classList.toggle("d-none", visible !== 0);
        }
        if (count) {
            count.textContent = query ? `${visible} / ${items.length}` : "";
        }
    };

    input.addEventListener("input", update);
    update();
});

document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) {
        return;
    }

    const activeTag = document.activeElement?.tagName;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(activeTag)) {
        return;
    }

    const searchInput = document.querySelector("[data-live-filter-input]");
    if (searchInput) {
        event.preventDefault();
        searchInput.focus();
    }
});
