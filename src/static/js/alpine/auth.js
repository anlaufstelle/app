/**
 * Alpine-Komponenten fuer Login-/MFA-/PWA-Pfade.
 *
 * Alle Komponenten sind CSP-kompatibel (registriert via Alpine.data,
 * keine Inline-Objekte). Refs #669, #911.
 */

document.addEventListener("alpine:init", () => {
    /**
     * MFA-Login-Mode-Switch: ``totp`` vs. ``backup``.
     * Initial-Wert kommt aus ``data-initial-mode`` Attribut, sodass
     * Templates kein Inline-Objekt mehr brauchen.
     */
    Alpine.data("mfaModeSwitch", () => ({
        mode: "totp",
        init() {
            const initial = this.$el.dataset.initialMode;
            if (initial) {
                this.mode = initial;
            }
        },
        switchMode() {
            this.mode = this.mode === "totp" ? "backup" : "totp";
        },
        get isTotp() {
            return this.mode === "totp";
        },
        get isBackup() {
            return this.mode === "backup";
        },
    }));

    /** Regenerate-Backup-Codes Toggle (auth/mfa_settings.html). */
    Alpine.data("regenerateBackupCodes", () => ({
        regenOpen: false,
        toggle() {
            this.regenOpen = !this.regenOpen;
        },
    }));

    /**
     * Backup-Codes-Confirmation + Aktionen (auth/mfa_backup_codes.html).
     *
     * Refs #1118: Der CSP-Build (alpine-csp.min.js) kann keine Inline-
     * Ausdrücke auswerten — weder `!confirmed` noch die früheren Inline-
     * `@click`-Handler für Download/Drucken. Solche geworfenen Ausdrucks-
     * Fehler legten die ganze Komponente lahm, sodass `x-model="confirmed"`
     * nicht mehr synchronisierte und der Bestätigungs-Button nie korrekt
     * (de)aktiviert wurde. Daher: alles als registrierte Methoden/Getter.
     */
    Alpine.data("backupCodesAcknowledge", () => ({
        confirmed: false,
        get notConfirmed() {
            return !this.confirmed;
        },
        // `x-model` funktioniert im CSP-Build nicht (erzeugt eine nicht
        // auswertbare Zuweisung `confirmed = …`). Daher den Checkbox-Zustand
        // per @change-Handler aus dem Element selbst übernehmen (Refs #1118).
        syncConfirmed() {
            this.confirmed = this.$el.checked;
        },
        _collectCodes() {
            return Array.from(
                document.querySelectorAll("#backup-codes-list li")
            )
                .map((li) => li.textContent.trim())
                .join("\n");
        },
        copyCodes() {
            navigator.clipboard.writeText(this._collectCodes());
        },
        downloadCodes() {
            const blob = new Blob([this._collectCodes() + "\n"], {
                type: "text/plain",
            });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "anlaufstelle-backup-codes.txt";
            a.click();
            URL.revokeObjectURL(url);
        },
        printCodes() {
            window.print();
        },
    }));

    /** PWA Install-Prompt (auth/login.html). */
    Alpine.data("pwaInstallPrompt", () => ({
        installPrompt: null,
        showInstall: false,
        showIos: false,
        init() {
            window.addEventListener("beforeinstallprompt", (event) => {
                event.preventDefault();
                this.installPrompt = event;
                this.showInstall = true;
            });
            if (
                /iPhone|iPad|iPod/.test(navigator.userAgent) &&
                !navigator.standalone
            ) {
                this.showIos = true;
            }
        },
        triggerInstall() {
            if (!this.installPrompt) return;
            this.installPrompt.prompt();
            this.installPrompt.userChoice.then(() => {
                this.showInstall = false;
            });
        },
    }));

    /**
     * Klarsicht-Toggle fuer Passwortfelder (Refs #1049).
     * Default verborgen (NIST SP 800-63B); Reveal ist eine bewusste
     * Nutzeraktion. Die i18n-Labels kommen aus data-Attributen des
     * Komponenten-Elements (data-label-show / data-label-hide).
     */
    Alpine.data("passwordToggle", () => ({
        show: false,
        labelShow: "",
        labelHide: "",
        init() {
            this.labelShow = this.$root.dataset.labelShow || "";
            this.labelHide = this.$root.dataset.labelHide || "";
        },
        toggle() {
            this.show = !this.show;
        },
        get hidden() {
            return !this.show;
        },
        get inputType() {
            return this.show ? "text" : "password";
        },
        get pressed() {
            return this.show ? "true" : "false";
        },
        get toggleLabel() {
            return this.show ? this.labelHide : this.labelShow;
        },
    }));
});
