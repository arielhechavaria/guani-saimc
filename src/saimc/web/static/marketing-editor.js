/* Editor de /marketing: escala la vista previa, cuenta el texto del post y
   lleva las imágenes y el texto al teléfono (copiar, compartir o descargar;
   en un carrusel, todas las diapositivas de una vez).
   Sin librerías. Los botones viven dentro de lo que HTMX reemplaza, así que
   los clics se atienden por delegación en `document`. */
(function () {
  "use strict";

  function fitFrames() {
    document.querySelectorAll(".mk-frame").forEach(function (frame) {
      var iframe = frame.querySelector("iframe");
      if (!iframe) return;
      var ancho = parseInt(iframe.getAttribute("data-mk-w"), 10);
      iframe.style.transform = "scale(" + frame.clientWidth / ancho + ")";
    });
  }

  function updateCount() {
    var box = document.getElementById("caption");
    var out = document.getElementById("caption-count");
    if (!box || !out) return;
    var max = parseInt(out.getAttribute("data-max"), 10);
    var maxTags = parseInt(out.getAttribute("data-max-tags"), 10);
    var tags = (box.value.match(/#/g) || []).length;
    out.textContent = box.value.length + " / " + max + " caracteres · " + tags + " / " + maxTags + " hashtags";
    out.classList.toggle("is-over", box.value.length > max || tags > maxTags);
  }

  function say(text) {
    var status = document.querySelector("[data-mk-status]");
    if (status) status.textContent = text;
  }

  function flash(button, text) {
    var label = button.querySelector("[data-mk-label]");
    if (!label) return;
    var original = label.getAttribute("data-mk-original") || label.textContent;
    label.setAttribute("data-mk-original", original);
    label.textContent = text;
    setTimeout(function () { label.textContent = original; }, 1800);
  }

  function copyText(button) {
    var box = document.getElementById("caption");
    if (!box) return;
    var done = function () { flash(button, "¡Copiado!"); };
    var fallback = function () {
      box.select();
      try { document.execCommand("copy"); done(); } catch (e) { say("No se pudo copiar: seleccioná el texto a mano."); }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(box.value).then(done, fallback);
    } else {
      fallback();
    }
  }

  function download(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  function filesOf(button) {
    try { return JSON.parse(button.getAttribute("data-mk-files") || "[]"); } catch (e) { return []; }
  }

  /* Baja los JPEG en orden y devuelve los `File` listos para compartir. */
  function fetchFiles(list) {
    return Promise.all(list.map(function (item) {
      return fetch(item.url, { credentials: "same-origin" }).then(function (res) {
        if (!res.ok) return res.text().then(function (t) { throw new Error(t || res.statusText); });
        return res.blob();
      }).then(function (blob) {
        return new File([blob], item.name, { type: "image/jpeg" });
      });
    }));
  }

  /* Varias descargas seguidas: con una pausa entre una y otra, porque algunos
     navegadores cortan las que llegan juntas. */
  function downloadAll(files) {
    files.forEach(function (file, i) {
      setTimeout(function () { download(file, file.name); }, i * 400);
    });
  }

  function busy(button, text) {
    var label = button.querySelector("[data-mk-label]");
    var original = label.textContent;
    button.disabled = true;
    label.textContent = text;
    say("");
    return function () {
      button.disabled = false;
      label.textContent = original;
    };
  }

  function fail(err) {
    if (err && err.name === "AbortError") return; // cerró el menú sin elegir
    say("No se pudo preparar la imagen: " + (err && err.message ? err.message : "error"));
  }

  /* Todas las imágenes en un solo `navigator.share`. Si el navegador no puede
     compartir esos archivos, se descargan. */
  function share(button) {
    var list = filesOf(button);
    var done = busy(button, "Preparando…");
    fetchFiles(list)
      .then(function (files) {
        if (navigator.canShare && navigator.canShare({ files: files })) {
          return navigator.share({ files: files });
        }
        downloadAll(files);
        say(files.length > 1
          ? "Este navegador no puede compartir varias imágenes: se descargaron las " + files.length + "."
          : "Este navegador no puede compartir archivos: se descargó el JPEG.");
      })
      .catch(fail)
      .then(done);
  }

  document.addEventListener("click", function (event) {
    var copy = event.target.closest("[data-mk-copy]");
    if (copy) return copyText(copy);
    var shareBtn = event.target.closest("[data-mk-share]");
    if (shareBtn) share(shareBtn);
  });
  document.addEventListener("input", function (event) {
    if (event.target.id === "caption") updateCount();
  });
  document.addEventListener("htmx:afterSwap", fitFrames);
  window.addEventListener("resize", fitFrames);
  document.addEventListener("DOMContentLoaded", function () { fitFrames(); updateCount(); });
})();
