/* Menú hamburguesa del sidebar (solo visible en pantallas <= 1024px, ver css/responsive.css). */
(function () {
    var CLASE = 'sidebar-abierto';
    var boton = document.querySelector('.menu-toggle');
    var fondo = document.querySelector('.sidebar-overlay');
    var sidebar = document.querySelector('.sidebar');
    if (!boton || !sidebar) return;

    function abrir(abierto) {
        document.body.classList.toggle(CLASE, abierto);
        boton.setAttribute('aria-expanded', abierto ? 'true' : 'false');
    }

    boton.addEventListener('click', function () {
        abrir(!document.body.classList.contains(CLASE));
    });
    if (fondo) fondo.addEventListener('click', function () { abrir(false); });

    // Al elegir una opción del menú, se cierra.
    sidebar.addEventListener('click', function (e) {
        if (e.target.closest('a')) abrir(false);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') abrir(false);
    });

    // Si se agranda la ventana a tamaño escritorio, se limpia el estado.
    window.addEventListener('resize', function () {
        if (window.innerWidth > 1024) abrir(false);
    });
})();
