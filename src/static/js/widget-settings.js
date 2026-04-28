function widgetSettings() {
    return {
        config: JSON.parse(document.getElementById('widget-config-data').textContent),
        toggle(widget) {
            this.config[widget] = !this.config[widget];
            var el = document.getElementById('widget-' + widget.replace('_', '-'));
            if (el) {
                el.style.display = this.config[widget] ? '' : 'none';
            }
            var csrfToken = JSON.parse(document.body.getAttribute('hx-headers'))['X-CSRFToken'];
            var url = this.$root.getAttribute('data-preferences-url');
            fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': csrfToken
                },
                body: 'widget=' + widget + '&enabled=' + this.config[widget]
            });
        }
    };
}
