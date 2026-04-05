/**
 * Alpine.js component for statistics charts (Chart.js v4).
 *
 * Usage in template:
 *   <div x-data="statisticsCharts()" x-init="fetchAndRender()">
 */

/* exported statisticsCharts */
function statisticsCharts() {
  return {
    charts: {},
    loading: true,
    error: null,
    sources: [],
    docTypeFilter: "",

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
