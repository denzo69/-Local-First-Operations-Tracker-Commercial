document.querySelectorAll("[data-bs-toggle='collapse']").forEach((button) => {
    button.addEventListener("click", () => {
        const targetSelector = button.getAttribute("data-bs-target");
        const target = document.querySelector(targetSelector);

        if (target) {
            target.classList.toggle("show");
        }
    });
});
