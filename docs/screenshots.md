# Screenshots — Anlaufstelle

> **[English version](screenshots.en.md)** · zurück zur [README](../README.md)

Ein vollständiger Rundgang durch Anlaufstelle. Alle Bilder stammen aus der
mit `make docs-screens` vorbereiteten E2E-Umgebung und zeigen ausschließlich
pseudonymisierte Beispieldaten. Die Seed-Umgebung trägt zwei Einrichtungen mit
je einem eigenen Katalog: die Hauptstelle (Standardkatalog / Kontaktladen) und
die Zweigstelle Nord (Paket `beratungsstelle`, offene Sprechstunde und
Sozialberatung — [`docs/packages-beratungsstelle.md`](packages-beratungsstelle.md)).
`--scale=large` fügt drei weitere Einrichtungen mit den Paketen `awareness`,
`jugendarbeit` und `frauenhaus` hinzu.

Die Bilder neu erzeugen:

```bash
make docs-screens
```

Voraussetzungen: laufende Docker-Datenbank, installierte Python-Abhängigkeiten
und Playwright mit Chromium sowie freier Port 8844. Das Target hält den globalen
`CI_LOCK`, führt **`seed --flush --scale=medium` (leert die E2E-Datenbank)** aus
und schreibt DE/EN-Bilder für Desktop und Mobilgeräte nach `docs/screenshots/`.
Einen bereits laufenden E2E-Server abwarten oder den eigenen Lauf im zugehörigen
Terminal beenden; fremde Server werden vom Target nicht beendet.

## Alltag & Dokumentation

### Zeitstrom — das digitale Dienstbuch
![Zeitstrom](screenshots/zeitstrom.webp)

### Ereignis erfassen
![Ereignis erfassen](screenshots/ereignis-anlegen.webp)

### Ereignis erfassen — Paket Beratungsstelle
![Ereignis erfassen (Beratungsstelle)](screenshots/ereignis-anlegen-beratungsstelle.webp)

Typwahl Kurzberatung / Beratungsgespräch / Krisengespräch / Begleitung aus dem
Paket Beratungsstelle (Zweigstelle Nord).

### Personenliste
![Personenliste](screenshots/personenliste.webp)

### Personenverlauf
![Personenverlauf](screenshots/personendetail.webp)

### Suche — die vollständige Ergebnisseite
![Suche](screenshots/suche.webp)

## Fallarbeit

### Fallakte mit Episoden und Zielen
![Fallakte](screenshots/fallakte.webp)

### Schichtübergabe
![Übergabe](screenshots/uebergabe.webp)

### Arbeitszentrale — rollenbezogener Einstieg
![Arbeitszentrale](screenshots/arbeitszentrale.webp)

## Auswertung & Datenschutz

### Statistiken
![Statistiken](screenshots/statistiken.webp)

### Datensatz-Berichte — fertige Auswertungen als Karten
![Datensatz-Berichte](screenshots/statistik-berichte.webp)

### Datenschutzfreundlicher externer Bericht
![Externer Bericht](screenshots/statistik-extern.webp)

### DSGVO-Datenpaket
![DSGVO-Paket](screenshots/dsgvo-paket.webp)

## Bereichs-Übersichten

### Datenschutz — Betroffenenrechte, Fristen und Nachweise
![Datenschutz-Bereich](screenshots/datenschutz-uebersicht.webp)

### Verwaltung — Formulare, Felder, Datenübernahme
![Verwaltungs-Bereich](screenshots/verwaltung-uebersicht.webp)

## Zugang

### Anmeldung
![Anmeldung](screenshots/login.webp)

## Mobil

| Zeitstrom (Mobil) | Arbeitszentrale (Mobil) |
|:---:|:---:|
| ![Zeitstrom mobil](screenshots/zeitstrom-mobil.webp) | ![Arbeitszentrale mobil](screenshots/arbeitszentrale-mobil.webp) |
