// JS-Unit-Test-Runner fuer die sicherheitskritischen Offline-PWA-Module
// (Refs #1479, TST-01). Umgebung jsdom liefert window/document/navigator,
// fake-indexeddb/auto (setupFiles) stellt ein IndexedDB bereit. Die Module
// sind klassische IIFEs, die sich an ``window`` haengen — die Tests laden sie
// per Seiteneffekt-Import und lesen ihre Export-Oberflaeche von ``window``.
import { defineConfig } from "vitest/config";

export default defineConfig({
    test: {
        environment: "jsdom",
        include: ["tests/js/**/*.test.mjs"],
        setupFiles: ["tests/js/setup.mjs"],
    },
});
