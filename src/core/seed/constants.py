"""Textual data pools and templates used when seeding demo data."""

from core.models import Client, User

# User templates (reused per facility).
USER_TEMPLATES = [
    ("admin", "Admin", "User", User.Role.ADMIN, True),
    ("thomas", "Thomas", "Müller", User.Role.LEAD, False),
    ("miriam", "Miriam", "Schmidt", User.Role.STAFF, False),
    ("lena", "Lena", "Weber", User.Role.ASSISTANT, False),
]

# Client definitions for small scale (backward-compatible).
SMALL_CLIENTS = [
    ("Stern-42", Client.ContactStage.QUALIFIED, Client.AgeCluster.AGE_18_26),
    ("Wolke-17", Client.ContactStage.QUALIFIED, Client.AgeCluster.AGE_27_PLUS),
    ("Blitz-08", Client.ContactStage.IDENTIFIED, Client.AgeCluster.U18),
    ("Regen-55", Client.ContactStage.IDENTIFIED, Client.AgeCluster.AGE_27_PLUS),
    ("Wind-33", Client.ContactStage.QUALIFIED, Client.AgeCluster.AGE_18_26),
    ("Nebel-71", Client.ContactStage.IDENTIFIED, Client.AgeCluster.UNKNOWN),
    ("Sonne-99", Client.ContactStage.QUALIFIED, Client.AgeCluster.AGE_27_PLUS),
]

# Word pools for pseudonym generation.
SPITZNAMEN = [
    # Tier-Spitznamen
    "Teddy",
    "Bärli",
    "Fuchs",
    "Motte",
    "Spatz",
    "Kater",
    "Maus",
    "Wolf",
    "Igel",
    "Dachs",
    "Fink",
    "Krähe",
    "Rabe",
    "Hase",
    "Storch",
    # Objekt- / Kose-Spitznamen
    "Keks",
    "Krümel",
    "Nuss",
    "Honig",
    "Zucker",
    "Brötchen",
    "Knopf",
    "Perle",
    "Dose",
    "Löffel",
    # Eigenschafts- / Charakter-Namen
    "Rocky",
    "Blitz",
    "Sunny",
    "Lucky",
    "Stille",
    "Schatten",
    "Flitzer",
    "Riese",
    "Zwerg",
    "Mucki",
    "Turbo",
    "Zorro",
    "Joker",
    "Flash",
    "Chief",
    # Szene- / Diminutiv-Namen
    "Shorty",
    "Eddy",
    "Matze",
    "Pieps",
    "Paule",
    "Jockel",
    "Mücke",
    "Pumpe",
    "Schnecke",
    "Socke",
    "Zipfel",
    "Floh",
    "Schrauber",
    "Nadel",
    "Zicke",
    # Natur-Anleihen
    "Stern",
    "Wolke",
    "Nebel",
    "Sturm",
    "Flamme",
    "Mond",
    "Feder",
    "Koralle",
    "Drift",
    "Quelle",
]

# Work-item templates.
WORK_ITEM_TITLES = [
    "Termin beim Jobcenter vereinbaren",
    "Medikamentenausgabe kontrollieren",
    "Arztbesuch begleiten",
    "Kleiderkammer auffüllen",
    "Krisengespräch Nachbereitung",
    "Sozialamt-Antrag unterstützen",
    "Angehörige kontaktieren",
    "Wohnungssuche recherchieren",
    "Tagesbericht schreiben",
    "Teammeeting vorbereiten",
    "Krankenversicherung klären",
    "Bewährungshelfer-Termin vorbereiten",
    "Dolmetscher für nächsten Termin organisieren",
    "Schuldenaufstellung erstellen",
    "Essensvorräte bestellen",
    "Hygieneartikel nachbestellen",
    "Hausverbot überprüfen (Fristablauf)",
    "Streetwork-Bericht dokumentieren",
    "Substitutionsbestätigung einholen",
    "Schlüssel für Schließfach ausgeben",
]

WORK_ITEM_DESCRIPTIONS = {
    "Termin beim Jobcenter vereinbaren": "Vorsprache wegen Leistungsklärung, Unterlagen zusammenstellen.",
    "Medikamentenausgabe kontrollieren": "Bestand prüfen, Ablaufdaten kontrollieren, Nachbestellung.",
    "Arztbesuch begleiten": "Begleitung zum Hausarzt, ggf. Übersetzungshilfe.",
    "Kleiderkammer auffüllen": "Gespendete Kleidung sortieren und einräumen.",
    "Krisengespräch Nachbereitung": "Dokumentation und kollegiale Nachbesprechung.",
    "Sozialamt-Antrag unterstützen": "Formulare gemeinsam ausfüllen, Kopien anfertigen.",
    "Angehörige kontaktieren": "Kontaktaufnahme mit Erlaubnis der/des Klientel.",
    "Wohnungssuche recherchieren": "Aktuelle Angebote durchsehen, Dringlichkeitsschein prüfen.",
    "Tagesbericht schreiben": "Tagesprotokoll für die Schichtübergabe.",
    "Teammeeting vorbereiten": "Agenda erstellen, aktuelle Fälle zusammenstellen.",
    "Krankenversicherung klären": "Status prüfen, ggf. Anspruch auf Notversorgung.",
    "Bewährungshelfer-Termin vorbereiten": "Unterlagen zusammenstellen, Klientel erinnern.",
    "Dolmetscher für nächsten Termin organisieren": "Sprachmittler:in über Vermittlungsstelle anfragen.",
    "Schuldenaufstellung erstellen": "Gläubiger und Forderungen erfassen für Schuldnerberatung.",
    "Essensvorräte bestellen": "Wochenbedarf für Frühstück und Mittagessen kalkulieren.",
    "Hygieneartikel nachbestellen": "Hygienebeutel, Zahnbürsten, Rasierer auffüllen.",
    "Hausverbot überprüfen (Fristablauf)": "Ablaufdatum prüfen, Teamgespräch über Aufhebung.",
    "Streetwork-Bericht dokumentieren": "Kontakte und Beobachtungen vom Straßengang festhalten.",
    "Substitutionsbestätigung einholen": "Nachweis von Substitutionsambulanz für Akte anfordern.",
    "Schlüssel für Schließfach ausgeben": "Schließfach zuweisen, Schlüssel-Nummer dokumentieren.",
}

CASE_TITLES = [
    "Wohnungssuche",
    "Gesundheitsversorgung",
    "Berufliche Integration",
    "Familienkonflikt",
    "Suchtberatung",
    "Schuldenregulierung",
    "Aufenthaltsstatus",
    "Psychologische Betreuung",
    "Ausbildungsplatz",
    "Reintegration",
]

CASE_DESCRIPTIONS = {
    "Wohnungssuche": "Person ist seit mehreren Monaten wohnungslos. Ziel: stabile Wohnsituation finden.",
    "Gesundheitsversorgung": ("Chronische Erkrankung ohne regelmäßige Behandlung. Zugang zum Gesundheitssystem."),
    "Berufliche Integration": "Wünscht sich Tagesstruktur und Einkommen. Qualifikation und Möglichkeiten klären.",
    "Familienkonflikt": "Kontaktabbruch zur Familie nach Eskalation. Vermittlung und Stabilisierung.",
    "Suchtberatung": "Langjähriger Substanzkonsum, Motivation zur Veränderung vorhanden.",
    "Schuldenregulierung": "Mehrere tausend Euro Schulden bei verschiedenen Gläubigern.",
    "Aufenthaltsstatus": "Ungeklärter Aufenthaltsstatus, drohendes Erlöschen der Duldung.",
    "Psychologische Betreuung": ("Wiederkehrende psychische Krisen, keine feste Anbindung an psychiatrischen Dienst."),
    "Ausbildungsplatz": "Junger Mensch ohne Schulabschluss, sucht Einstieg in Ausbildung oder Maßnahme.",
    "Reintegration": "Nach Haftentlassung: Wohnung, Arbeit und soziales Netz neu aufbauen.",
}

EPISODE_TITLES = [
    "Erstgespräch und Bedarfsanalyse",
    "Stabilisierungsphase",
    "Aktive Vermittlung",
    "Nachbetreuung",
    "Krisenintervention",
    "Orientierungsphase",
    "Begleitphase",
    "Abschlussphase",
]

EPISODE_DESCRIPTIONS = {
    "Erstgespräch und Bedarfsanalyse": "Erste Kontaktaufnahme, Bedarfe erfassen, Vertrauensaufbau.",
    "Stabilisierungsphase": "Grundversorgung sicherstellen, regelmäßige Kontakte etablieren.",
    "Aktive Vermittlung": "Termine wahrnehmen, Anträge stellen, Vermittlung an Fachdienste.",
    "Nachbetreuung": "Erreichte Ziele sichern, Rückfallprophylaxe, Kontakthalten.",
    "Krisenintervention": "Akute Krise hat Priorität, Stabilisierung vor weiterer Planung.",
    "Orientierungsphase": "Möglichkeiten ausloten, Ziele konkretisieren.",
    "Begleitphase": "Regelmäßige Begleitung zu Terminen und Behördengängen.",
    "Abschlussphase": "Verselbständigung, Abschlussgespräch, Dokumentation.",
}

GOAL_TITLES = [
    "Stabile Wohnsituation",
    "Regelmäßige Einkünfte",
    "Gesundheitliche Versorgung",
    "Soziale Anbindung",
    "Suchtmittelreduktion",
    "Schuldenfreiheit",
    "Berufliche Integration",
    "Familiäre Stabilität",
]

GOAL_DESCRIPTIONS = {
    "Stabile Wohnsituation": "Eigenen Wohnraum oder betreutes Wohnen finden und halten können.",
    "Regelmäßige Einkünfte": "Zugang zu Sozialleistungen oder Erwerbseinkommen sicherstellen.",
    "Gesundheitliche Versorgung": "Regelmäßige ärztliche Behandlung und Krankenversicherungsschutz.",
    "Soziale Anbindung": "Tragfähige Kontakte außerhalb der Szene aufbauen.",
    "Suchtmittelreduktion": "Konsum reduzieren oder Substitutionsbehandlung aufnehmen.",
    "Schuldenfreiheit": "Schulden regulieren, Insolvenzverfahren oder Vergleiche einleiten.",
    "Berufliche Integration": "Maßnahme, Praktikum oder Arbeitsstelle finden.",
    "Familiäre Stabilität": "Kontakt zur Familie wieder herstellen oder klären.",
}

MILESTONE_TITLES = [
    "Erstgespräch geführt",
    "Antrag gestellt",
    "Termin vereinbart",
    "Dokumente zusammengestellt",
    "Begleitung durchgeführt",
    "Rückmeldung erhalten",
    "Folgetermin vereinbart",
    "Abschlussgespräch geführt",
]

# Typ-spezifische Event-Daten-Pools für Bulk-Generierung.
EVENT_DATA_POOLS = {
    "contact": [
        {
            "dauer": 15,
            "leistungen": ["beratung", "essen"],
            "notiz": "Kam zum Frühstück, Beratungsbedarf wegen Wohnsituation.",
        },
        {
            "dauer": 30,
            "leistungen": ["kleidung", "duschen"],
            "notiz": "Winterjacke und Schlafsack ausgegeben, hat geduscht.",
        },
        {"dauer": 10, "leistungen": ["essen"], "notiz": "Kurzer Besuch, Mittagessen."},
        {
            "dauer": 45,
            "leistungen": ["beratung", "telefon"],
            "notiz": "Telefonat mit Vermieter geführt, Beratung zur Mietschuldenübernahme.",
        },
        {"dauer": 20, "leistungen": ["duschen", "waesche"], "notiz": "Wäsche gewaschen und geduscht."},
        {"dauer": 5, "leistungen": ["post"], "notiz": "Post abgeholt."},
        {
            "dauer": 25,
            "leistungen": ["essen", "beratung", "kleidung"],
            "notiz": "Frühstück, Kleidung ausgesucht, kurze Beratung zum Jobcenter-Termin.",
        },
        {
            "dauer": 35,
            "leistungen": ["beratung", "sonstiges"],
            "notiz": "Längeres Gespräch über aktuelle Lebenssituation, braucht Schlafsack.",
            "strassenkontakt": True,
        },
        {"dauer": 10, "leistungen": ["essen", "sachspenden"], "notiz": "Hygienebeutel mitgegeben."},
        {
            "dauer": 60,
            "leistungen": ["beratung", "telefon", "post"],
            "notiz": "ALG-II-Antrag gemeinsam ausgefüllt, beim Jobcenter angerufen.",
        },
        {"dauer": 15, "leistungen": ["essen"], "notiz": "Stammgast, Kaffee und Frühstück.", "strassenkontakt": False},
        {
            "dauer": 20,
            "leistungen": ["sonstiges"],
            "notiz": "Aufladen vom Handy, kurzer Aufenthalt im Tagesraum.",
            "strassenkontakt": True,
        },
    ],
    "crisis": [
        {
            "art-der-krise": "psychische-krise",
            "dauer": 60,
            "notiz-krise": "Starke Unruhe, Stimmen hören. Stabilisiert durch Gespräch und Tee.",
            "weitervermittlung": "",
        },
        {
            "art-der-krise": "substanzkrise",
            "dauer": 45,
            "notiz-krise": "Überdosierung abgewendet, Vitalzeichen stabil. Notarzt nicht nötig.",
            "weitervermittlung": "",
        },
        {
            "art-der-krise": "suizidal",
            "dauer": 90,
            "notiz-krise": "Akute Suizidalität, Non-Suizid-Vereinbarung getroffen.",
            "weitervermittlung": "Psychiatrische Notaufnahme",
        },
        {
            "art-der-krise": "gewalt",
            "dauer": 40,
            "notiz-krise": "Nach Schlägerei aufgelöst, Wunden versorgt.",
            "weitervermittlung": "",
        },
        {
            "art-der-krise": "obdachlosigkeit",
            "dauer": 50,
            "notiz-krise": "Schlafplatz verloren, akute Verzweiflung. Notschlafstelle vermittelt.",
            "weitervermittlung": "Notschlafstelle",
        },
        {
            "art-der-krise": "psychische-krise",
            "dauer": 75,
            "notiz-krise": "Panikattacke, Atemübungen durchgeführt. Langsam beruhigt.",
            "weitervermittlung": "",
        },
        {
            "art-der-krise": "substanzkrise",
            "dauer": 55,
            "notiz-krise": "Starke Entzugserscheinungen, zittert. Warmen Tee, Ruheraum.",
            "weitervermittlung": "Substitutionsambulanz",
        },
        {
            "art-der-krise": "suizidal",
            "dauer": 80,
            "notiz-krise": "Passive Suizidalität nach Räumung der Wohnung. Längeres Gespräch, etwas stabilisiert.",
            "weitervermittlung": "Krisendienst",
        },
        {
            "art-der-krise": "gewalt",
            "dauer": 35,
            "notiz-krise": "Wurde auf der Straße ausgeraubt. Polizei informiert, beruhigt.",
            "weitervermittlung": "Polizeiwache",
        },
        {
            "art-der-krise": "sonstiges",
            "dauer": 30,
            "notiz-krise": "Zusammenbruch nach Abschiebebescheid. Emotionale Stabilisierung.",
            "weitervermittlung": "Flüchtlingsberatung",
        },
    ],
    "medical": [
        {
            "art-der-versorgung": "wundversorgung",
            "notiz-medizin": "Schnittwunde am Unterarm gereinigt und verbunden.",
            "krankenhaus": False,
        },
        {
            "art-der-versorgung": "medikamentenausgabe",
            "notiz-medizin": "Ibuprofen 400 ausgegeben gegen Zahnschmerzen.",
            "krankenhaus": False,
        },
        {
            "art-der-versorgung": "beratung",
            "notiz-medizin": "Beratung zur Hepatitis-C-Behandlung, Termin in Ambulanz vermittelt.",
            "krankenhaus": False,
        },
        {
            "art-der-versorgung": "wundversorgung",
            "notiz-medizin": "Entzündete Einstichstelle, Desinfektion und Verband. Arztbesuch empfohlen.",
            "krankenhaus": False,
        },
        {
            "art-der-versorgung": "sonstiges",
            "notiz-medizin": "Blutdruckmessung, Werte im Normalbereich.",
            "krankenhaus": False,
        },
        {
            "art-der-versorgung": "wundversorgung",
            "notiz-medizin": "Tiefe Schnittwunde, muss genäht werden.",
            "krankenhaus": True,
        },
        {
            "art-der-versorgung": "medikamentenausgabe",
            "notiz-medizin": "Pflaster und Wundsalbe ausgegeben.",
            "krankenhaus": False,
        },
        {
            "art-der-versorgung": "sonstiges",
            "notiz-medizin": "Verdacht auf Unterkühlung, aufgewärmt, Vitalzeichen kontrolliert.",
            "krankenhaus": True,
        },
    ],
    "needle_exchange": [
        {"ausgabe": 10, "rueckgabe": 10},
        {"ausgabe": 5, "rueckgabe": 3},
        {"ausgabe": 20, "rueckgabe": 18},
        {"ausgabe": 10, "rueckgabe": 6},
        {"ausgabe": 30, "rueckgabe": 25},
        {"ausgabe": 15, "rueckgabe": 15},
        {"ausgabe": 8, "rueckgabe": 5},
        {"ausgabe": 50, "rueckgabe": 42},
        {"ausgabe": 10, "rueckgabe": 10},
        {"ausgabe": 25, "rueckgabe": 20},
    ],
    "accompaniment": [
        {"ziel": "Jobcenter", "notiz-begleitung": "Termin zur Leistungsklärung, Unterlagen dabei."},
        {"ziel": "Hausarzt Dr. Müller", "notiz-begleitung": "Blutabnahme und Befundbesprechung."},
        {"ziel": "Amtsgericht", "notiz-begleitung": "Anhörung wegen offener Geldstrafe."},
        {"ziel": "Notschlafstelle", "notiz-begleitung": "Platz für die Nacht gesichert."},
        {"ziel": "Sozialamt", "notiz-begleitung": "Antrag auf Grundsicherung abgegeben."},
        {"ziel": "Schuldnerberatung", "notiz-begleitung": "Erstgespräch, Schuldenaufstellung begonnen."},
        {"ziel": "Suchtberatungsstelle", "notiz-begleitung": "Erstgespräch zur Substitution."},
        {"ziel": "Krankenhaus Notaufnahme", "notiz-begleitung": "Abszess muss operiert werden, Einweisung."},
        {"ziel": "Ausländerbehörde", "notiz-begleitung": "Duldung verlängern, alle Dokumente dabei."},
        {"ziel": "Wohnungsamt", "notiz-begleitung": "Antrag auf Dringlichkeitsschein eingereicht."},
    ],
    "counseling": [
        {
            "thema": "Wohnsituation und Mietschulden",
            "dauer": 45,
            "vereinbarungen": "Unterlagen für Mietschuldenübernahme zusammenstellen.",
        },
        {
            "thema": "Substitutionsbehandlung",
            "dauer": 30,
            "vereinbarungen": "Termin bei Substitutionsambulanz nächste Woche.",
        },
        {
            "thema": "Arbeitssuche und Bewerbungen",
            "dauer": 40,
            "vereinbarungen": "Lebenslauf gemeinsam erstellen, Folgetermin in 2 Wochen.",
        },
        {
            "thema": "Schuldenregulierung",
            "dauer": 50,
            "vereinbarungen": "Schuldenaufstellung mitbringen, Schuldnerberatung kontaktieren.",
        },
        {
            "thema": "Familienkontakt",
            "dauer": 35,
            "vereinbarungen": "Brief an Schwester formulieren, nächste Woche besprechen.",
        },
        {
            "thema": "Gesundheitliche Beschwerden",
            "dauer": 30,
            "vereinbarungen": "Arzttermin vereinbaren, Krankenversicherungsstatus klären.",
        },
        {
            "thema": "Aufenthaltsgenehmigung",
            "dauer": 55,
            "vereinbarungen": "Anwalt für Asylrecht kontaktieren, Dokumente kopieren.",
        },
        {
            "thema": "Straffälligkeit und Bewährung",
            "dauer": 40,
            "vereinbarungen": "Nächsten Bewährungshelfer-Termin einhalten, Sozialstunden beginnen.",
        },
        {
            "thema": "Psychische Belastung",
            "dauer": 50,
            "vereinbarungen": "Psychiatrischen Dienst aufsuchen, Krisenplan besprechen.",
        },
        {
            "thema": "Alltagsstruktur aufbauen",
            "dauer": 35,
            "vereinbarungen": "Wochenplan erstellen, regelmäßig zum Frühstück kommen.",
        },
    ],
    "note": [
        {"notiz": "Team-Info: Klientel wirkte heute sehr aufgelöst, bitte im Blick behalten."},
        {"notiz": "Neue Telefonnummer hinterlegt."},
        {"notiz": "Hat sich länger nicht blicken lassen. Bei nächstem Kontakt nachfragen."},
        {"notiz": "Möchte an der Kochgruppe teilnehmen, auf Warteliste gesetzt."},
        {"notiz": "Konflikt mit anderem Besucher im Tagesraum, konnte deeskaliert werden."},
        {"notiz": "Rückmeldung von Jobcenter: Antrag bewilligt."},
        {"notiz": "Arztbrief liegt vor, muss noch besprochen werden."},
        {"notiz": "Hat Interesse an Deutschkurs geäußert, Infos rausgesucht."},
        {"notiz": "Wirkte heute stabil und gut gelaunt, positives Gespräch."},
        {"notiz": "Betreuer:in vom Sozialpsychiatrischen Dienst hat angerufen."},
        {"notiz": "Wurde von Streetwork-Team angetroffen, schläft unter der Brücke."},
        {"notiz": "Personalausweis ist abgelaufen, Termin beim Bürgeramt nötig."},
    ],
    "ban": [
        {"grund": "Wiederholte Verstöße gegen die Hausordnung trotz Abmahnung."},
        {"grund": "Bedrohung von Mitarbeitenden mit einem Gegenstand."},
        {"grund": "Drogenkonsum in den Räumlichkeiten, mehrfach ermahnt."},
        {"grund": "Sachbeschädigung: Stuhl geworfen, Fenster beschädigt."},
        {"grund": "Körperliche Auseinandersetzung mit anderem Besucher."},
        {"grund": "Diebstahl aus der Kleiderkammer."},
        {"grund": "Verbale Beleidigung und rassistische Äußerungen gegenüber Besucher:innen."},
        {"grund": "Handeln mit Betäubungsmitteln auf dem Gelände."},
    ],
}
