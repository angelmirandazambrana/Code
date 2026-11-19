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

// Destino del clasificador: sugerencias de carpetas mientras se escribe.
document.querySelectorAll("[data-buscar-carpeta]").forEach((input) => {
  let temporizador;
  const lista = document.getElementById(input.getAttribute("list"));
  input.addEventListener("input", () => {
    const radio = input.closest(".opcion")?.querySelector("input[type=radio]");
    if (radio) radio.checked = true;
    clearTimeout(temporizador);
    temporizador = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2 || !lista) return;
      try {
        const r = await fetch("/api/carpetas?q=" + encodeURIComponent(q));
        const carpetas = await r.json();
        lista.replaceChildren(...carpetas.map((c) => Object.assign(document.createElement("option"), { value: c })));
      } catch (_) { /* sin sugerencias */ }
    }, 200);
  });
});
