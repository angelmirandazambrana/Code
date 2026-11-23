// Gráficos de la página de estadísticas: barras (apiladas) y líneas en SVG, con tooltip al pasar.
(() => {
  const fuente = document.getElementById("datos-est");
  if (!fuente) return;
  const datos = JSON.parse(fuente.textContent);
  const NS = "http://www.w3.org/2000/svg";
  const fmt = (n) => (n == null ? "—" : n.toLocaleString("es-ES"));

  function el(tag, attrs, padre) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (padre) padre.appendChild(e);
    return e;
  }

  function maximoBonito(v) {
    if (v <= 4) return 4;
    const pot = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * pot >= v) return m * pot;
    return 10 * pot;
  }

  // Barra con las esquinas de arriba redondeadas (r) y la base recta.
  function barra(x, y, w, h, r) {
    r = Math.min(r, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  function dibujar(lienzo) {
    const claves = lienzo.dataset.series.split(",");
    const nombres = lienzo.dataset.nombres.split("|");
    const tipo = lienzo.dataset.grafico;
    const n = datos.etiquetas.length;
    lienzo.textContent = "";
    if (!n) { lienzo.textContent = "No hay datos en estas fechas."; return; }
    const series = claves.map((k) => datos[k] || []);
    const hayDatos = series.some((s) => s.some((v) => v != null));
    if (!hayDatos) { lienzo.innerHTML = '<p class="tenue sin-datos">Todavía no hay datos en estas fechas.</p>'; return; }

    if (claves.length > 1) {
      const ley = document.createElement("ul");
      ley.className = "leyenda";
      nombres.forEach((t, i) => {
        const li = document.createElement("li");
        li.innerHTML = `<span class="muestra s${i + 1}"></span>${t}`;
        ley.appendChild(li);
      });
      lienzo.appendChild(ley);
    }

    const W = Math.max(lienzo.clientWidth, 260), H = 220;
    const m = { izq: 44, der: 10, arr: 10, aba: 26 };
    const ancho = W - m.izq - m.der, alto = H - m.arr - m.aba;
    const totales = [...Array(n).keys()].map((i) =>
      tipo === "barras" ? series.reduce((a, s) => a + (s[i] || 0), 0) : Math.max(...series.map((s) => s[i] ?? 0)));
    const max = maximoBonito(Math.max(...totales, 1));
    const y = (v) => m.arr + alto - (v / max) * alto;
    const banda = ancho / n;
    const xc = (i) => m.izq + banda * (i + 0.5);

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
      "aria-label": lienzo.closest("figure").querySelector("h2").textContent });
    lienzo.appendChild(svg);

    // Rejilla y eje Y
    for (let k = 0; k <= 4; k++) {
      const v = (max / 4) * k, yy = y(v);
      el("line", { x1: m.izq, x2: W - m.der, y1: yy, y2: yy, class: k ? "rejilla" : "base" }, svg);
      el("text", { x: m.izq - 6, y: yy + 4, class: "eje", "text-anchor": "end" }, svg).textContent = fmt(Math.round(v));
    }
    // Eje X: como mucho ~8 etiquetas
    const paso = Math.ceil(n / Math.max(2, Math.floor(ancho / 80)));
    for (let i = 0; i < n; i += paso) {
      el("text", { x: xc(i), y: H - 8, class: "eje", "text-anchor": "middle" }, svg).textContent = datos.etiquetas[i];
    }

    if (tipo === "barras") {
      const w = Math.max(2, Math.min(28, banda * 0.7));
      for (let i = 0; i < n; i++) {
        let base = 0;
        const ultima = series.reduce((u, s, j) => (s[i] ? j : u), -1);
        series.forEach((s, j) => {
          const v = s[i] || 0;
          if (!v) return;
          const y0 = y(base), y1 = y(base + v);
          const hueco = base ? 2 : 0;  // 2 px de separación entre tramos
          const h = Math.max(1, y0 - y1 - hueco);
          el("path", { d: barra(xc(i) - w / 2, y1, w, h, j === ultima ? 4 : 0), class: `s${j + 1}` }, svg);
          base += v;
        });
      }
    } else {
      series.forEach((s, j) => {
        let d = "", abierto = false;
        s.forEach((v, i) => {
          if (v == null) { abierto = false; return; }
          d += `${abierto ? "L" : "M"}${xc(i)},${y(v)}`;
          abierto = true;
        });
        el("path", { d, class: `linea s${j + 1}` }, svg);
        if (n <= 45) s.forEach((v, i) => { if (v != null) el("circle", { cx: xc(i), cy: y(v), r: 4, class: `punto s${j + 1}` }, svg); });
      });
    }

    // Capa de interacción: una franja por periodo.
    const guia = el("line", { y1: m.arr, y2: m.arr + alto, class: "guia", visibility: "hidden" }, svg);
    const tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    lienzo.appendChild(tip);
    for (let i = 0; i < n; i++) {
      const r = el("rect", { x: m.izq + banda * i, y: m.arr, width: banda, height: alto, class: "franja" }, svg);
      const mostrar = () => {
        guia.setAttribute("x1", xc(i)); guia.setAttribute("x2", xc(i)); guia.setAttribute("visibility", "visible");
        let html = `<strong>${datos.etiquetas[i]}</strong>`;
        series.forEach((s, j) => {
          html += `<div><span class="muestra s${j + 1}"></span>${nombres[j]}: <b>${fmt(s[i])}</b></div>`;
        });
        if (tipo === "barras" && series.length > 1) html += `<div class="tenue">Total: ${fmt(totales[i])}</div>`;
        tip.innerHTML = html;
        tip.hidden = false;
        const izq = Math.min(xc(i) + 12, W - tip.offsetWidth - 4);
        tip.style.left = `${izq < xc(i) ? xc(i) - tip.offsetWidth - 12 : izq}px`;
        tip.style.top = `${(claves.length > 1 ? 30 : 0) + 8}px`;
      };
      r.addEventListener("mouseenter", mostrar);
      r.addEventListener("click", mostrar);
    }
    svg.addEventListener("mouseleave", () => { tip.hidden = true; guia.setAttribute("visibility", "hidden"); });
  }

  const lienzos = [...document.querySelectorAll("[data-grafico]")];
  const todo = () => lienzos.forEach(dibujar);
  todo();
  let espera;
  window.addEventListener("resize", () => { clearTimeout(espera); espera = setTimeout(todo, 150); });
})();
