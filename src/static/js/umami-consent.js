/*
 * Umami-Consent (Opt-in) für die öffentliche Demo. Refs #1604.
 *
 * DSGVO: kein stilles Tracking. Der Umami-Tracker (main.js) und der
 * Session-Recorder/die Heatmap (recorder.js) werden erst NACH expliziter
 * Zustimmung geladen. Der Consent-Zustand wird pro Gerät in localStorage
 * gehalten, sodass die Frage nur einmal gestellt wird.
 *
 * CSP-konform: kein Inline-Script, keine Inline-Handler — die Konfiguration
 * kommt aus data-Attributen des Banner-Elements (partials/_umami_consent.html),
 * die dynamisch injizierten <script>-Tags laden vom Umami-Host, den die
 * DemoUmamiCSPRelaxMiddleware nur Post-Login in script-src/connect-src freigibt.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "umami-consent";
  var GRANTED = "granted";
  var DENIED = "denied";

  var banner = document.getElementById("umami-consent");
  if (!banner) {
    return;
  }

  function readConsent() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }

  function storeConsent(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (e) {
      /* localStorage nicht verfügbar — Consent gilt dann nur für diese Seite. */
    }
  }

  function injectScript(src, websiteId) {
    if (!src) {
      return;
    }
    var s = document.createElement("script");
    s.defer = true;
    s.src = src;
    s.setAttribute("data-website-id", websiteId);
    document.head.appendChild(s);
  }

  function loadUmami() {
    var websiteId = banner.getAttribute("data-umami-website-id");
    if (!websiteId) {
      return;
    }
    injectScript(banner.getAttribute("data-umami-src"), websiteId);
    injectScript(banner.getAttribute("data-umami-recorder-src"), websiteId);
  }

  function hideBanner() {
    banner.classList.add("hidden");
  }

  function showBanner() {
    banner.classList.remove("hidden");
  }

  function accept() {
    storeConsent(GRANTED);
    hideBanner();
    loadUmami();
  }

  function decline() {
    storeConsent(DENIED);
    hideBanner();
  }

  var acceptBtn = banner.querySelector("[data-umami-consent-accept]");
  var declineBtn = banner.querySelector("[data-umami-consent-decline]");
  if (acceptBtn) {
    acceptBtn.addEventListener("click", accept);
  }
  if (declineBtn) {
    declineBtn.addEventListener("click", decline);
  }

  var current = readConsent();
  if (current === GRANTED) {
    // Bereits zugestimmt: Tracker still laden, Banner nicht zeigen.
    loadUmami();
  } else if (current === DENIED) {
    // Bereits abgelehnt: nichts laden, Banner nicht zeigen.
    hideBanner();
  } else {
    // Noch keine Entscheidung: Banner einblenden, bis geklickt wird.
    showBanner();
  }
})();
