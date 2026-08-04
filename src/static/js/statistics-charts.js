/**
 * Alpine.js component for statistics charts (Chart.js v4).
 *
 * Usage in template:
 *   <div x-data="statisticsCharts()" x-init="fetchAndRender()">
 */

// Destroy any Chart.js instances inside an HTMX swap target BEFORE the swap
// happens. Without this, Chart.js holds references to canvases that get
// removed by HTMX, then complains "Canvas is already in use" when the new
// canvas with the same id is mounted. The destroyCharts() call inside
// fetchAndRender() runs on the *new* component instance after the swap, when
// the orphaned instances are no longer reachable via getElementById.
document.addEventListener("htmx:beforeSwap", function (evt) {
  if (typeof Chart === "undefined") return;
  var target = evt && evt.detail && evt.detail.target;
  if (!target || typeof target.querySelectorAll !== "function") return;
  target.querySelectorAll("canvas").forEach(function (canvas) {
    var existing = Chart.getChart(canvas);
    if (existing) existing.destroy();
  });
});

// Refs #1776: Zurückstellen des Berichts-Selects auf den Platzhalter
// ("Bericht wählen …", Wert "") feuert weiterhin dessen hx-trigger="change"
// (report_panel.html) -- DatasetReportPanelView antwortet fail-closed mit
// 400 "backend erforderlich", htmx-errors.js zeigt das als roten
// Fehler-Toast. Ein hx-trigger-Filter (z.B. "change[target.value]") würde
// das verhindern, braucht unter htmx aber `unsafe-eval` -- unter der CSP
// verboten. Capture-Phase-Listener (Muster confirm-action.js) läuft VOR
// htmx's eigenem (Bubble-Phase-)Listener auf demselben <select> und stoppt
// die Propagation, bevor htmx den Request überhaupt baut -- kein Request,
// kein Toast. Nicht-leere Auswahl ist unberührt (Event läuft normal weiter).
document.addEventListener(
  "change",
  function (evt) {
    var el = evt.target;
    if (el && el.matches && el.matches("select[name='backend']") && el.value === "") {
      evt.stopPropagation();
    }
  },
  true
);

/* statisticsCharts wird per Alpine.data() registriert (CSP-friendly, Refs #672). */
function buildStatisticsCharts() {
  return {
    charts: {},
    loading: true,
    error: null,
    sources: [],
    docTypeFilter: "",
    // Refs #1764: ersetzt die vorherigen zwei Handler `setDocTypeFilter` +
    // `fetchAndRender`, die im Template als zwei `@change`-Attribute auf
    // demselben <select> gebunden waren. Der HTML-Parser verwirft ein
    // zweites gleichnamiges Attribut auf einem Element still -- nur
    // `setDocTypeFilter` (Wert merken, kein Refetch) lief, der Chart-Refetch
    // griff dadurch erst beim naechsten Filterwechsel.
    onDocTypeChange(event) {
      this.docTypeFilter = event.target.value;
      this.fetchAndRender();
    },
    // CSP-konforme Wrapper
    get hasSources() {
      return this.sources.length > 0;
    },
    get hasError() {
      return this.error !== null;
    },

    async fetchAndRender() {
      const params = new URLSearchParams(window.location.search);
      if (this.docTypeFilter) {
        params.set("document_type", this.docTypeFilter);
      }
      const url = "/statistics/chart-data/?" + params.toString();

      this.loading = true;
      this.error = null;
      try {
        const resp = await fetch(url, {
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "",
          },
          credentials: "same-origin",
        });
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const data = await resp.json();
        this.sources = data.sources || [];
        this.destroyCharts();
        this.renderContactsChart(data);
        this.renderDocTypeChart(data);
        this.renderAgeClusterChart(data);
        // Refs #1594: plain data-attribute marker (kein Alpine-x-Binding auf
        // <body>, kollisionsfrei) — Doku-Screenshot-Command wartet darauf,
        // damit die Charts nicht leer/halb gezeichnet im Screenshot landen.
        document.body.setAttribute("data-charts-ready", "true");
      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },

    renderContactsChart(data) {
      const canvas = document.getElementById("chart-contacts");
      if (!canvas) return;

      const pointStyles = (data.sources || []).map(function (s) {
        return s === "snapshot" ? "rectRounded" : "circle";
      });
      const pointColors = (data.sources || []).map(function (s) {
        return s === "snapshot" ? "rgba(99, 102, 241, 0.7)" : "rgba(99, 102, 241, 1)";
      });

      this.charts.contacts = new Chart(canvas, {
        type: "line",
        data: {
          labels: data.labels,
          datasets: [
            {
              label: "Gesamt",
              data: data.contacts.total,
              borderColor: "rgb(99, 102, 241)",
              backgroundColor: "rgba(99, 102, 241, 0.1)",
              fill: true,
              tension: 0.3,
              pointStyle: pointStyles,
              pointBackgroundColor: pointColors,
              pointRadius: 5,
            },
            {
              label: "Anonym",
              data: data.contacts.anonym,
              borderColor: "rgb(156, 163, 175)",
              borderDash: [5, 5],
              tension: 0.3,
              pointRadius: 3,
            },
            {
              label: "Identifiziert",
              data: data.contacts.identifiziert,
              borderColor: "rgb(59, 130, 246)",
              borderDash: [5, 5],
              tension: 0.3,
              pointRadius: 3,
            },
            {
              label: "Qualifiziert",
              data: data.contacts.qualifiziert,
              borderColor: "rgb(16, 185, 129)",
              borderDash: [5, 5],
              tension: 0.3,
              pointRadius: 3,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: {
                afterLabel: function (ctx) {
                  var src = (data.sources || [])[ctx.dataIndex];
                  return src ? "Quelle: " + (src === "snapshot" ? "Snapshot" : "Live-Daten") : "";
                },
              },
            },
          },
          scales: {
            y: { beginAtZero: true, ticks: { precision: 0 } },
          },
        },
      });
    },

    renderDocTypeChart(data) {
      const canvas = document.getElementById("chart-doc-types");
      if (!canvas) return;

      const colors = [
        "rgb(99, 102, 241)",
        "rgb(59, 130, 246)",
        "rgb(16, 185, 129)",
        "rgb(245, 158, 11)",
        "rgb(239, 68, 68)",
        "rgb(139, 92, 246)",
        "rgb(236, 72, 153)",
        "rgb(20, 184, 166)",
      ];

      var items = data.document_types || [];
      this.charts.docTypes = new Chart(canvas, {
        type: "bar",
        data: {
          labels: items.map(function (d) { return d.name; }),
          datasets: [
            {
              label: "Kontakte",
              data: items.map(function (d) { return d.count; }),
              backgroundColor: items.map(function (_, i) {
                return colors[i % colors.length];
              }),
              borderRadius: 4,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: items.length > 5 ? "y" : "x",
          plugins: {
            legend: { display: false },
          },
          scales: {
            x: { ticks: { precision: 0 } },
            y: { ticks: { precision: 0 } },
          },
        },
      });
    },

    renderAgeClusterChart(data) {
      const canvas = document.getElementById("chart-age-clusters");
      if (!canvas) return;

      const colors = [
        "rgb(99, 102, 241)",
        "rgb(59, 130, 246)",
        "rgb(16, 185, 129)",
        "rgb(245, 158, 11)",
        "rgb(239, 68, 68)",
      ];

      var items = data.age_clusters || [];
      this.charts.ageClusters = new Chart(canvas, {
        type: "doughnut",
        data: {
          labels: items.map(function (d) { return d.label; }),
          datasets: [
            {
              data: items.map(function (d) { return d.count; }),
              backgroundColor: items.map(function (_, i) {
                return colors[i % colors.length];
              }),
              borderWidth: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "bottom" },
          },
        },
      });
    },

    destroyCharts() {
      Object.values(this.charts).forEach(function (c) {
        if (c && typeof c.destroy === "function") c.destroy();
      });
      this.charts = {};
      // Also destroy orphaned Chart.js instances left after HTMX swap
      ["chart-contacts", "chart-doc-types", "chart-age-clusters"].forEach(function (id) {
        var canvas = document.getElementById(id);
        if (canvas) {
          var existing = Chart.getChart(canvas);
          if (existing) existing.destroy();
        }
      });
    },
  };
}

document.addEventListener("alpine:init", function () {
  Alpine.data("statisticsCharts", buildStatisticsCharts);
});
