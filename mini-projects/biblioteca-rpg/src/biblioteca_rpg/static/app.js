// Pequeñas comodidades; la web funciona igual sin JavaScript.
document.addEventListener("click", (e) => {
  const verMas = e.target.closest("[data-ver-mas]");
  if (verMas) {
    const sec = verMas.closest("[data-faceta]");
    const abierta = sec.classList.toggle("abierta");
    verMas.textContent = abierta ? "Ver menos" : verMas.dataset.textoOriginal;
    return;
  }
  if (e.target.closest("[data-alternar-lateral]")) {
    document.body.classList.toggle("lateral-abierto");
    return;
  }
  const copiar = e.target.closest("[data-copiar]");
  if (copiar) {
    const texto = copiar.dataset.copiar;
    const ok = () => { const t = copiar.textContent; copiar.textContent = "Copiada ✓"; setTimeout(() => (copiar.textContent = t), 1500); };
    if (navigator.clipboard) navigator.clipboard.writeText(texto).then(ok, () => prompt("Ruta:", texto));
    else prompt("Ruta:", texto);
  }
});

document.querySelectorAll("[data-ver-mas]").forEach((b) => (b.dataset.textoOriginal = b.textContent));
document.querySelectorAll("[data-autoenviar]").forEach((s) => s.addEventListener("change", () => s.form.submit()));

document.querySelectorAll("[data-filtrar-faceta]").forEach((input) => {
  input.addEventListener("input", () => {
    const sec = input.closest("[data-faceta]");
    const t = input.value.trim().toLowerCase();
    sec.classList.toggle("filtrando", t.length > 0);
    sec.querySelectorAll("li").forEach((li) => {
      li.hidden = t.length > 0 && !li.dataset.valor.includes(t);
    });
  });
});

// Destino del clasificador: buscar carpetas por palabras y elegir de la lista.
const sinAcentos = (t) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function resaltar(texto, palabras) {
  // Marca en negrita las palabras buscadas (sin distinguir mayúsculas ni acentos).
  const base = sinAcentos(texto);
  const marcas = new Array(texto.length).fill(false);
  palabras.forEach((p) => {
    let i = base.indexOf(p);
    while (p && i >= 0) { for (let k = i; k < i + p.length; k++) marcas[k] = true; i = base.indexOf(p, i + 1); }
  });
  const frag = document.createDocumentFragment();
  let i = 0;
  while (i < texto.length) {
    let j = i;
    while (j < texto.length && marcas[j] === marcas[i]) j++;
    const trozo = texto.slice(i, j);
    frag.append(marcas[i] ? Object.assign(document.createElement("b"), { textContent: trozo }) : trozo);
    i = j;
  }
  return frag;
}

document.querySelectorAll("[data-buscar-carpeta]").forEach((input) => {
  const caja = input.parentElement.querySelector(".sugerencias");
  let temporizador, activa = -1, peticion = 0;
  const opciones = () => [...caja.querySelectorAll("button")];
  const cerrar = () => { caja.hidden = true; activa = -1; };
  const marcar = (i) => {
    const ops = opciones();
    if (!ops.length) return;
    activa = (i + ops.length) % ops.length;
    ops.forEach((o, k) => o.classList.toggle("activa", k === activa));
    ops[activa].scrollIntoView({ block: "nearest" });
  };
  const elegir = (carpeta) => { input.value = carpeta; cerrar(); };

  input.addEventListener("input", () => {
    const radio = input.closest(".opcion")?.querySelector("input[type=radio]");
    if (radio) radio.checked = true;
    clearTimeout(temporizador);
    temporizador = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) return cerrar();
      const yo = ++peticion;
      let res = [];
      try { res = await (await fetch("/api/carpetas?q=" + encodeURIComponent(q))).json(); } catch (_) { /* nada */ }
      if (yo !== peticion) return;  // llegó tarde: ya se escribió otra cosa
      const palabras = sinAcentos(q).split(/[^a-z0-9]+/).filter(Boolean);
      caja.replaceChildren();
      // "RPG/Gimmick-zero": una ruta que aún no existe se puede crear al mover.
      const ruta = q.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
      if (ruta.includes("/") && !res.some(({ carpeta }) => carpeta.toLowerCase() === ruta.toLowerCase())) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "crear";
        b.dataset.carpeta = ruta;
        b.append(Object.assign(document.createElement("span"), { textContent: "➕ Crear carpeta nueva: " + ruta }));
        b.addEventListener("mousedown", (e) => { e.preventDefault(); elegir(ruta); });
        caja.append(b);
      } else if (!res.length) {  // ni carpetas ni ficheros con esas palabras
        caja.append(Object.assign(document.createElement("span"), { className: "sin-resultados", textContent: "Ninguna carpeta con esas palabras. Para una nueva, escribe la ruta entera (p. ej. RPG/Gimmick-zero)." }));
      }
      let separador = false;
      res.forEach(({ carpeta, n, coinciden, ejemplo }) => {
        if (coinciden && !separador) {
          separador = true;
          caja.append(Object.assign(document.createElement("span"), { className: "sin-resultados", textContent: "Donde hay ficheros que se llaman así:" }));
        }
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.carpeta = carpeta;
        const ruta = document.createElement("span");
        ruta.append(resaltar(carpeta, coinciden ? [] : palabras));
        if (coinciden) {
          ruta.append(Object.assign(document.createElement("small"), { className: "ejemplo", textContent: ` ${coinciden} así, p. ej. «${ejemplo}»` }));
        }
        b.append(ruta, Object.assign(document.createElement("span"), { className: "n", textContent: n }));
        b.addEventListener("mousedown", (e) => { e.preventDefault(); elegir(carpeta); });
        caja.append(b);
      });
      caja.hidden = false;
      activa = -1;
    }, 150);
  });
  input.addEventListener("keydown", (e) => {
    if (caja.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); marcar(activa + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); marcar(activa - 1); }
    else if (e.key === "Enter" && activa >= 0) { e.preventDefault(); elegir(opciones()[activa].dataset.carpeta); }
    else if (e.key === "Escape") cerrar();
  });
  input.addEventListener("blur", () => setTimeout(cerrar, 100));
});

// Entrada: al decidir sobre un lote, la página vuelve a cargarse; se queda donde estaba
// (el lote decidido desaparece y el siguiente ocupa su sitio) en vez de saltar arriba.
if (location.pathname === "/entrada") {
  const clave = "entrada-scroll";
  try {
    const y = sessionStorage.getItem(clave);
    if (y !== null) {
      sessionStorage.removeItem(clave);
      history.scrollRestoration = "manual";
      window.scrollTo(0, Number(y));
      window.addEventListener("load", () => window.scrollTo(0, Number(y)), { once: true });
    }
  } catch (_) { /* sin almacenamiento: se queda arriba */ }
  document.querySelectorAll("form.decision, details.grupo form, details.corregir form").forEach((f) =>
    f.addEventListener("submit", () => {
      try { sessionStorage.setItem(clave, String(window.scrollY)); } catch (_) { /* nada */ }
    }));
}

// Buscador de la cabecera: escritura predictiva con editoriales, sistemas y títulos que tenemos.
document.querySelectorAll("[data-sugerir]").forEach((input) => {
  const caja = input.form.querySelector(".sugerencias");
  let temporizador, activa = -1, peticion = 0;
  const opciones = () => [...caja.querySelectorAll("a")];
  const cerrar = () => { caja.hidden = true; activa = -1; input.setAttribute("aria-expanded", "false"); };
  const marcar = (i) => {
    const ops = opciones();
    if (!ops.length) return;
    activa = (i + ops.length) % ops.length;
    ops.forEach((o, k) => o.classList.toggle("activa", k === activa));
    ops[activa].scrollIntoView({ block: "nearest" });
  };
  const opcion = (url, texto, palabras, extra, claseExtra) => {
    const a = Object.assign(document.createElement("a"), { href: url });
    a.setAttribute("role", "option");
    const t = document.createElement("span");
    t.className = "texto";
    t.append(resaltar(texto, palabras));
    a.append(t);
    if (extra) a.append(Object.assign(document.createElement("span"), { className: claseExtra, textContent: extra }));
    return a;
  };
  const grupo = (texto) => Object.assign(document.createElement("div"), { className: "grupo", textContent: texto });

  input.addEventListener("input", () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) return cerrar();
      const yo = ++peticion;
      let res = { valores: [], libros: [] };
      try { res = await (await fetch("/api/sugerencias?q=" + encodeURIComponent(q))).json(); } catch (_) { /* nada */ }
      if (yo !== peticion) return;
      if (!res.valores.length && !res.libros.length) return cerrar();
      const palabras = sinAcentos(q).split(/[^a-z0-9]+/).filter(Boolean);
      caja.replaceChildren();
      if (res.valores.length) {
        caja.append(grupo("Filtrar por"));
        res.valores.forEach((v) => {
          const a = opcion(v.url, v.valor, palabras, v.n.toLocaleString("es"), "n");
          a.prepend(Object.assign(document.createElement("span"), { className: "faceta-etq", textContent: v.etiqueta }));
          caja.append(a);
        });
      }
      if (res.libros.length) {
        caja.append(grupo("Libros"));
        res.libros.forEach((l) => caja.append(opcion(l.url, l.titulo, palabras, l.detalle, "detalle")));
      }
      caja.hidden = false;
      input.setAttribute("aria-expanded", "true");
      activa = -1;
    }, 120);
  });
  input.addEventListener("keydown", (e) => {
    if (caja.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); marcar(activa + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); marcar(activa - 1); }
    else if (e.key === "Enter" && activa >= 0) { e.preventDefault(); location.href = opciones()[activa].href; }
    else if (e.key === "Escape") cerrar();
  });
  // mousedown para que el clic llegue antes de que el blur cierre la lista
  caja.addEventListener("mousedown", (e) => e.preventDefault());
  input.addEventListener("blur", () => setTimeout(cerrar, 150));
});

// Entrada por grupos: marcar o desmarcar todos los ficheros de un grupo.
document.querySelectorAll("[data-marcar-todos]").forEach((b) =>
  b.addEventListener("click", () => {
    const casillas = [...b.closest("form").querySelectorAll("input[name=ids]")];
    const marcar = casillas.some((c) => !c.checked);
    casillas.forEach((c) => { c.checked = marcar; });
  }));

// Entrada: grupos plegables. Los que se pliegan siguen plegados al volver a la página.
(() => {
  const grupos = [...document.querySelectorAll("details.grupo[data-clave]")];
  if (!grupos.length) return;
  const clave = "entrada-plegados";
  let plegados = new Set();
  try { plegados = new Set(JSON.parse(localStorage.getItem(clave) || "[]")); } catch (_) { /* sin almacenamiento */ }
  const guardar = () => {
    try { localStorage.setItem(clave, JSON.stringify([...plegados].slice(-500))); } catch (_) { /* nada */ }
  };
  grupos.forEach((d) => {
    if (plegados.has(d.dataset.clave)) d.open = false;
    d.addEventListener("toggle", () => {
      if (d.open) plegados.delete(d.dataset.clave); else plegados.add(d.dataset.clave);
      guardar();
    });
  });
  document.querySelectorAll("[data-plegar]").forEach((b) =>
    b.addEventListener("click", () => {
      const abrir = b.dataset.plegar === "abrir";
      grupos.forEach((d) => { d.open = abrir; });
    }));
})();
