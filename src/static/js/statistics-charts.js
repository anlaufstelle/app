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

// Refs #1804: Der frühere Capture-Listener gegen den 400-Toast des
// Berichts-Selects ist ersatzlos entfallen. Er fing den Fall „Select zurück auf
// den Platzhalter" ab, weil dessen change-Trigger `DatasetReportPanelView` ohne
// `?backend=` aufrief und die fail-closed mit 400 antwortet. Seit die
// Berichtsauswahl serverseitiger URL-Zustand ist (`?bericht=<slug>`, Karten
// statt <select>), gibt es weder das Select noch den Platzhalter — und damit
// auch den Sonderfall nicht mehr.

/* statisticsCharts wird per Alpine.data() registriert (CSP-friendly, Refs #672). */
function buildStatisticsCharts() {
  return {
    charts: {},
    loading: true,
    error: null,
    sources: [],
    text(key, fallback) {
      const host = this.$el || document.getElementById("statistics-charts");
      return host?.dataset?.["i18n" + key[0].toUpperCase() + key.slice(1)] || fallback;
    },
    get errorText() {
      return this.text("error", "Error: ") + this.error;
    },
    // Refs #1804: KEIN docTypeFilter mehr im Client-State. Der Filter ist
    // serverseitiger Zustand (`?doktyp=`), das <select> laedt
    // `#statistics-content` per htmx neu, und diese Komponente wird dabei
    // komplett ersetzt -- ihr x-init holt die Daten danach ohnehin frisch.
    // Der frueher hier gepflegte Wert wirkte nur auf die Chart-API (die ihn
    // gar nicht las) und ging bei jedem Reload verloren.
    // CSP-konforme Wrapper
    get hasSources() {
      return this.sources.length > 0;
    },
    get hasError() {
      return this.error !== null;
    },

    async fetchAndRender() {
      // Refs #1804 (R3): Der Zustand kommt SERVERSEITIG gerendert aus
      // `data-chart-query`, nicht aus `window.location.search`. Die
      // Zeitraum-Pills und das Doktyp-Select tauschen `#statistics-content` per
      // htmx und schieben die URL erst danach nach -- zum Zeitpunkt von x-init
      // kann `window.location.search` also noch den vorherigen Stand tragen.
      // Der Fallback greift nur, falls das Attribut fehlt (fremder Einbau).
      const host = document.getElementById("statistics-charts");
      const query = (host && host.dataset.chartQuery) || window.location.search.replace(/^\?/, "");
      const url = "/statistics/chart-data/" + (query ? "?" + query : "");

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
        if (!resp.ok) {
          this.error = this.text("http", "HTTP error") + " " + resp.status;
          return;
        }
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
        this.error = this.text("requestFailed", "Request failed.");
      } finally {
        this.loading = false;
      }
    },

    renderContactsChart(data) {
      const canvas = document.getElementById("chart-contacts");
      const sourceLabel = this.text("source", "Source: ");
      const snapshotLabel = this.text("snapshot", "Snapshot");
      const liveLabel = this.text("live", "Live data");
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
              label: this.text("total", "Total"),
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
              label: this.text("anonymous", "Anonymous"),
              data: data.contacts.anonym,
              borderColor: "rgb(156, 163, 175)",
              borderDash: [5, 5],
              tension: 0.3,
              pointRadius: 3,
            },
            {
              label: this.text("pseudonym", "Pseudonym only"),
              data: data.contacts.identifiziert,
              borderColor: "rgb(59, 130, 246)",
              borderDash: [5, 5],
              tension: 0.3,
              pointRadius: 3,
            },
            {
              label: this.text("qualified", "With additional details"),
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
                  return src ? sourceLabel + (src === "snapshot" ? snapshotLabel : liveLabel) : "";
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
              label: this.text("contacts", "Contacts"),
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
