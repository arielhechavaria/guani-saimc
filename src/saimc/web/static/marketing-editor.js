/* Editor de /marketing: escala la vista previa, cuenta el texto del post y
   lleva la imagen y el texto al teléfono (copiar, compartir o descargar).
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

  function share(button) {
    var label = button.querySelector("[data-mk-label]");
    var original = label.textContent;
    var name = button.getAttribute("data-mk-name");
    button.disabled = true;
    label.textContent = "Preparando…";
    say("");
    fetch(button.getAttribute("data-mk-url"), { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) return res.text().then(function (t) { throw new Error(t || res.statusText); });
        return res.blob();
      })
      .then(function (blob) {
        var file = new File([blob], name, { type: "image/jpeg" });
        if (navigator.canShare && navigator.canShare({ files: [file] })) {
          return navigator.share({ files: [file] });
        }
        download(blob, name);
        say("Este navegador no puede compartir archivos: se descargó el JPEG.");
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return; // cerró el menú sin elegir
        say("No se pudo preparar la imagen: " + (err && err.message ? err.message : "error"));
      })
      .then(function () {
        button.disabled = false;
        label.textContent = original;
      });
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
