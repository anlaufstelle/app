"""Seed data for development and demo."""

import copy
import random
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    Activity,
    AuditLog,
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    EventHistory,
    Facility,
    FieldTemplate,
    Milestone,
    Organization,
    OutcomeGoal,
    Settings,
    TimeFilter,
    User,
    WorkItem,
)
from core.services.encryption import encrypt_event_data

# ---------------------------------------------------------------------------
# Scale configuration
# ---------------------------------------------------------------------------
SCALE_CONFIG = {
    "small": {
        "facilities": 1,
        "users_per_facility": 4,
        "clients_per_facility": 7,
        "events_per_facility": 25,
        "cases": 3,
        "episodes": 0,
        "goals": 0,
        "work_items": 5,
        "zeitraum_days": 80,
    },
    "medium": {
        "facilities": 2,
        "users_per_facility": 4,
        "clients_per_facility": 40,
        "events_per_facility": 750,
        "cases": 12,
        "episodes": 20,
        "goals": 15,
        "milestones_per_goal": 3,
        "work_items": 25,
        "deletion_requests": 5,
        "zeitraum_days": 365,
    },
    "large": {
        "facilities": 5,
        "users_per_facility": 4,
        "clients_per_facility": 500,
        "events_per_facility": 10000,
        "cases": 50,
        "episodes": 80,
        "goals": 60,
        "milestones_per_goal": 4,
        "work_items": 100,
        "deletion_requests": 15,
        "zeitraum_days": 3 * 365,
    },
    "solo": {
        "facilities": 1,
        "users_per_facility": 4,
        "clients_per_facility": 30,
        "events_per_facility": 500,
        "cases": 10,
        "episodes": 15,
        "goals": 10,
        "milestones_per_goal": 3,
        "work_items": 20,
        "deletion_requests": 4,
        "zeitraum_days": 1000,
    },
}

# Facility names used when creating more than one facility.
FACILITY_NAMES = [
    "Hauptstelle",
    "Zweigstelle Nord",
    "Zweigstelle Süd",
    "Außenstelle Ost",
    "Außenstelle West",
]

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
_SPITZNAMEN = [
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
_WORK_ITEM_TITLES = [
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

_WORK_ITEM_DESCRIPTIONS = {
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

_CASE_TITLES = [
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

_CASE_DESCRIPTIONS = {
    "Wohnungssuche": "Klientel ist seit mehreren Monaten obdachlos. Ziel: stabile Wohnsituation finden.",
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

_EPISODE_TITLES = [
    "Erstgespräch und Bedarfsanalyse",
    "Stabilisierungsphase",
    "Aktive Vermittlung",
    "Nachbetreuung",
    "Krisenintervention",
    "Orientierungsphase",
    "Begleitphase",
    "Abschlussphase",
]

_EPISODE_DESCRIPTIONS = {
    "Erstgespräch und Bedarfsanalyse": "Erste Kontaktaufnahme, Bedarfe erfassen, Vertrauensaufbau.",
    "Stabilisierungsphase": "Grundversorgung sicherstellen, regelmäßige Kontakte etablieren.",
    "Aktive Vermittlung": "Termine wahrnehmen, Anträge stellen, Vermittlung an Fachdienste.",
    "Nachbetreuung": "Erreichte Ziele sichern, Rückfallprophylaxe, Kontakthalten.",
    "Krisenintervention": "Akute Krise hat Priorität, Stabilisierung vor weiterer Planung.",
    "Orientierungsphase": "Möglichkeiten ausloten, Ziele konkretisieren.",
    "Begleitphase": "Regelmäßige Begleitung zu Terminen und Behördengängen.",
    "Abschlussphase": "Verselbständigung, Abschlussgespräch, Dokumentation.",
}

_GOAL_TITLES = [
    "Stabile Wohnsituation",
    "Regelmäßige Einkünfte",
    "Gesundheitliche Versorgung",
    "Soziale Anbindung",
    "Suchtmittelreduktion",
    "Schuldenfreiheit",
    "Berufliche Integration",
    "Familiäre Stabilität",
]

_GOAL_DESCRIPTIONS = {
    "Stabile Wohnsituation": "Eigenen Wohnraum oder betreutes Wohnen finden und halten können.",
    "Regelmäßige Einkünfte": "Zugang zu Sozialleistungen oder Erwerbseinkommen sicherstellen.",
    "Gesundheitliche Versorgung": "Regelmäßige ärztliche Behandlung und Krankenversicherungsschutz.",
    "Soziale Anbindung": "Tragfähige Kontakte außerhalb der Szene aufbauen.",
    "Suchtmittelreduktion": "Konsum reduzieren oder Substitutionsbehandlung aufnehmen.",
    "Schuldenfreiheit": "Schulden regulieren, Insolvenzverfahren oder Vergleiche einleiten.",
    "Berufliche Integration": "Maßnahme, Praktikum oder Arbeitsstelle finden.",
    "Familiäre Stabilität": "Kontakt zur Familie wieder herstellen oder klären.",
}

_MILESTONE_TITLES = [
    "Erstgespräch geführt",
    "Antrag gestellt",
    "Termin vereinbart",
    "Dokumente zusammengestellt",
    "Begleitung durchgeführt",
    "Rückmeldung erhalten",
    "Folgetermin vereinbart",
    "Abschlussgespräch geführt",
]

# ---------------------------------------------------------------------------
# Typ-spezifische Event-Daten-Pools für Bulk-Generierung
# ---------------------------------------------------------------------------
_EVENT_DATA_POOLS = {
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


class Command(BaseCommand):
    help = "Create demo data (idempotent)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--scale",
            choices=["small", "medium", "large", "solo"],
            default="small",
            help="Data volume: small (default), medium, large",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            default=False,
            help="Delete existing seed data before generating",
        )

    def handle(self, *args, **options):
        scale = options["scale"]
        cfg = SCALE_CONFIG[scale]
        random.seed(42)

        if options["flush"]:
            self._flush()

        org = self._create_org()

        for idx in range(cfg["facilities"]):
            facility_name = FACILITY_NAMES[idx]
            facility = self._create_facility(org, facility_name)
            self._create_document_types(facility)
            self._create_settings(facility)
            self._create_time_filters(facility)
            users = self._create_users(facility, idx)

            if scale == "small":
                self._create_clients_small(facility, users)
                self._create_events_small(facility)
                clients = list(Client.objects.filter(facility=facility))
                self._create_cases(facility, users, clients, cfg)
                self._create_work_items(facility, users, clients, cfg)
            else:
                clients = self._create_clients_bulk(facility, users, cfg)
                self._create_events_bulk(facility, users, clients, cfg)
                self._create_cases(facility, users, clients, cfg)
                self._create_work_items(facility, users, clients, cfg)
                self._create_deletion_requests(facility, users, cfg)

            cases = list(Case.objects.filter(facility=facility))
            self._create_episodes(facility, users, cases, cfg)
            self._create_goals(facility, users, cases, cfg)
            self._assign_events_to_cases(facility, cases, cfg)

            self._create_activities(facility, users, cfg)

        self.stdout.write(self.style.SUCCESS(f"Seed-Daten erfolgreich erstellt. (scale={scale})"))

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------
    def _flush(self):
        """Delete all seed-able data."""
        from django.db import connection

        self.stdout.write("Deleting existing data...")
        Activity.objects.all().delete()
        DeletionRequest.objects.all().delete()
        WorkItem.objects.all().delete()
        Milestone.objects.all().delete()
        OutcomeGoal.objects.all().delete()
        Episode.objects.all().delete()
        Case.objects.all().delete()
        # EventHistory has append-only DB trigger -> temporarily disable
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_delete")
        EventHistory.objects.all().delete()
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_eventhistory ENABLE TRIGGER eventhistory_no_delete")
        # AuditLog has immutable DB trigger -> temporarily disable
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
        AuditLog.objects.all().delete()
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable")
        Event.objects.all().delete()
        Client.objects.all().delete()
        DocumentTypeField.objects.all().delete()
        DocumentType.objects.all().delete()
        FieldTemplate.objects.all().delete()
        TimeFilter.objects.all().delete()
        Settings.objects.all().delete()
        User.objects.all().delete()
        Facility.objects.all().delete()
        Organization.objects.all().delete()

    # ------------------------------------------------------------------
    # Organisation & Facility
    # ------------------------------------------------------------------
    def _create_org(self):
        org, _ = Organization.objects.get_or_create(name="Anlaufstelle")
        return org

    def _create_facility(self, org, name):
        facility, _ = Facility.objects.get_or_create(
            organization=org,
            name=name,
        )
        return facility

    # ------------------------------------------------------------------
    # Settings / TimeFilters
    # ------------------------------------------------------------------
    def _create_settings(self, facility):
        default_dt = DocumentType.objects.filter(facility=facility, system_type="contact").first()
        Settings.objects.get_or_create(
            facility=facility,
            defaults={
                "facility_full_name": f"Anlaufstelle {facility.name}",
                "session_timeout_minutes": 30,
                "retention_anonymous_days": 90,
                "retention_identified_days": 365,
                "retention_qualified_days": 3650,
                "retention_activities_days": 365,
                "default_document_type": default_dt,
            },
        )

    def _create_time_filters(self, facility):
        filters = [
            ("Frühdienst", time(8, 0), time(16, 0), True, 0),
            ("Spätdienst", time(16, 0), time(22, 0), False, 1),
            ("Nachtdienst", time(22, 0), time(8, 0), False, 2),
        ]
        for label, start, end, is_default, sort in filters:
            TimeFilter.objects.get_or_create(
                facility=facility,
                label=label,
                defaults={
                    "start_time": start,
                    "end_time": end,
                    "is_default": is_default,
                    "sort_order": sort,
                },
            )

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def _create_users(self, facility, facility_idx):
        """Create users for a facility. Returns list of User objects."""
        created_users = []
        for username_base, first, last, role, is_superuser in USER_TEMPLATES:
            # For facility_idx > 0 add suffix to avoid username collision.
            username = username_base if facility_idx == 0 else f"{username_base}_{facility_idx}"
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "role": role,
                    "facility": facility,
                    "is_staff": True,
                    "is_superuser": is_superuser,
                    "display_name": f"{first} {last}",
                },
            )
            if created:
                user.set_password("anlaufstelle2026")
                user.save()
            created_users.append(user)
        return created_users

    # ------------------------------------------------------------------
    # Document types (shared logic, unchanged)
    # ------------------------------------------------------------------
    def _create_document_types(self, facility):
        doc_types = self._get_document_type_definitions()
        for dt_def in doc_types:
            # get_or_create uses (facility, name) as lookup.
            # On name collision the existing object is reused,
            # even if attributes differ (defaults only apply on create).
            defaults = {
                "category": dt_def["category"],
                "sensitivity": dt_def.get("sensitivity", DocumentType.Sensitivity.NORMAL),
                "icon": dt_def.get("icon", ""),
                "color": dt_def.get("color", ""),
                "sort_order": dt_def.get("sort_order", 0),
                "min_contact_stage": dt_def.get("min_contact_stage"),
            }
            if "system_type" in dt_def:
                defaults["system_type"] = dt_def["system_type"]
            dt, _ = DocumentType.objects.get_or_create(
                facility=facility,
                name=dt_def["name"],
                defaults=defaults,
            )
            for idx, field_def in enumerate(dt_def.get("fields", [])):
                # update_or_create: For the same (facility, slug) the existing
                # FieldTemplate is reused — defaults only apply on create.
                ft, _ = FieldTemplate.objects.update_or_create(
                    facility=facility,
                    slug=field_def["slug"],
                    defaults={
                        "name": field_def["name"],
                        "field_type": field_def.get("type", FieldTemplate.FieldType.TEXT),
                        "is_required": field_def.get("required", False),
                        "is_encrypted": field_def.get("encrypted", False),
                        "options_json": field_def.get("options", []),
                        "help_text": field_def.get("help_text", ""),
                    },
                )
                DocumentTypeField.objects.get_or_create(
                    document_type=dt,
                    field_template=ft,
                    defaults={"sort_order": idx},
                )

    def _get_document_type_definitions(self):
        return [
            {
                "name": "Kontakt",
                "category": DocumentType.Category.CONTACT,
                "system_type": "contact",
                "icon": "users",
                "color": "indigo",
                "sort_order": 0,
                "fields": [
                    {"name": "Dauer", "slug": "dauer", "type": "number", "help_text": "Dauer in Minuten"},
                    {
                        "name": "Leistungen",
                        "slug": "leistungen",
                        "type": "multi_select",
                        "options": [
                            {"slug": "beratung", "label": "Beratung", "is_active": True},
                            {"slug": "essen", "label": "Essen", "is_active": True},
                            {"slug": "kleidung", "label": "Kleidung", "is_active": True},
                            {"slug": "duschen", "label": "Duschen", "is_active": True},
                            {"slug": "waesche", "label": "Wäsche", "is_active": True},
                            {"slug": "telefon", "label": "Telefon", "is_active": True},
                            {"slug": "post", "label": "Post", "is_active": True},
                            {"slug": "sonstiges", "label": "Sonstiges", "is_active": True},
                            {"slug": "sachspenden", "label": "Sachspenden", "is_active": False},
                        ],
                    },
                    {
                        "name": "Alterscluster",
                        "slug": "alterscluster",
                        "type": "select",
                        "options": [
                            {"slug": "u18", "label": "U18", "is_active": True},
                            {"slug": "18-26", "label": "18-26", "is_active": True},
                            {"slug": "27-plus", "label": "27+", "is_active": True},
                            {"slug": "unbekannt", "label": "Unbekannt", "is_active": True},
                        ],
                        "help_text": "Geschätztes Alter",
                    },
                    {"name": "Notiz", "slug": "notiz", "type": "textarea"},
                    {"name": "Straßenkontakt", "slug": "strassenkontakt", "type": "boolean"},
                ],
            },
            {
                "name": "Krisengespräch",
                "category": DocumentType.Category.SERVICE,
                "system_type": "crisis",
                "sensitivity": DocumentType.Sensitivity.ELEVATED,
                "icon": "alert-triangle",
                "color": "amber",
                "sort_order": 1,
                "fields": [
                    {
                        "name": "Art der Krise",
                        "slug": "art-der-krise",
                        "type": "select",
                        "options": [
                            {"slug": "suizidal", "label": "Suizidal", "is_active": True},
                            {"slug": "psychische-krise", "label": "Psychische Krise", "is_active": True},
                            {"slug": "substanzkrise", "label": "Substanzkrise", "is_active": True},
                            {"slug": "gewalt", "label": "Gewalt", "is_active": True},
                            {"slug": "obdachlosigkeit", "label": "Obdachlosigkeit", "is_active": True},
                            {"slug": "sonstiges", "label": "Sonstiges", "is_active": True},
                        ],
                    },
                    {"name": "Dauer", "slug": "dauer", "type": "number", "help_text": "Dauer in Minuten"},
                    {"name": "Notiz (Krise)", "slug": "notiz-krise", "type": "textarea", "encrypted": True},
                    {"name": "Weitervermittlung", "slug": "weitervermittlung", "type": "text"},
                ],
            },
            {
                "name": "Medizinische Versorgung",
                "category": DocumentType.Category.SERVICE,
                "system_type": "medical",
                "sensitivity": DocumentType.Sensitivity.HIGH,
                "icon": "heart",
                "color": "rose",
                "sort_order": 2,
                "fields": [
                    {
                        "name": "Art der Versorgung",
                        "slug": "art-der-versorgung",
                        "type": "select",
                        "options": [
                            {"slug": "wundversorgung", "label": "Wundversorgung", "is_active": True},
                            {"slug": "medikamentenausgabe", "label": "Medikamentenausgabe", "is_active": True},
                            {"slug": "beratung", "label": "Beratung", "is_active": True},
                            {"slug": "sonstiges", "label": "Sonstiges", "is_active": True},
                        ],
                    },
                    {"name": "Notiz (Medizin)", "slug": "notiz-medizin", "type": "textarea", "encrypted": True},
                    {"name": "Krankenhaus", "slug": "krankenhaus", "type": "boolean"},
                ],
            },
            {
                "name": "Spritzentausch",
                "category": DocumentType.Category.SERVICE,
                "system_type": "needle_exchange",
                "icon": "repeat",
                "color": "teal",
                "sort_order": 3,
                "fields": [
                    {"name": "Ausgabe", "slug": "ausgabe", "type": "number", "required": True},
                    {"name": "Rückgabe", "slug": "rueckgabe", "type": "number", "required": True},
                ],
            },
            {
                "name": "Begleitung",
                "category": DocumentType.Category.SERVICE,
                "system_type": "accompaniment",
                "icon": "map-pin",
                "color": "green",
                "sort_order": 4,
                "fields": [
                    {"name": "Ziel", "slug": "ziel", "type": "text", "required": True},
                    {"name": "Datum", "slug": "datum", "type": "date"},
                    {"name": "Uhrzeit", "slug": "uhrzeit", "type": "time"},
                    {"name": "Notiz (Begleitung)", "slug": "notiz-begleitung", "type": "textarea", "encrypted": True},
                ],
            },
            {
                "name": "Beratungsgespräch",
                "category": DocumentType.Category.SERVICE,
                "system_type": "counseling",
                "sensitivity": DocumentType.Sensitivity.ELEVATED,
                "min_contact_stage": "qualified",
                "icon": "message-circle",
                "color": "purple",
                "sort_order": 5,
                "fields": [
                    {"name": "Thema", "slug": "thema", "type": "text", "encrypted": True},
                    {"name": "Dauer", "slug": "dauer", "type": "number", "help_text": "Dauer in Minuten"},
                    {"name": "Vereinbarungen", "slug": "vereinbarungen", "type": "textarea", "encrypted": True},
                    {"name": "Nächster Termin", "slug": "naechster-termin", "type": "date"},
                ],
            },
            {
                "name": "Vermittlung",
                "category": DocumentType.Category.SERVICE,
                "system_type": "referral",
                "icon": "share-2",
                "color": "blue",
                "sort_order": 6,
                "fields": [],
            },
            {
                "name": "Notiz",
                "category": DocumentType.Category.NOTE,
                "system_type": "note",
                "icon": "file-text",
                "color": "gray",
                "sort_order": 7,
                "fields": [
                    {"name": "Notiz", "slug": "notiz", "type": "textarea"},
                ],
            },
            {
                "name": "Hausverbot",
                "category": DocumentType.Category.ADMIN,
                "system_type": "ban",
                "sensitivity": DocumentType.Sensitivity.ELEVATED,
                "icon": "slash",
                "color": "red",
                "sort_order": 8,
                "fields": [
                    {"name": "Grund", "slug": "grund", "type": "textarea", "required": True},
                    {"name": "Bis", "slug": "bis", "type": "date"},
                    {"name": "Aktiv", "slug": "aktiv", "type": "boolean"},
                ],
            },
        ]

    # ------------------------------------------------------------------
    # Clients – small (backward-compatible, identical to original)
    # ------------------------------------------------------------------
    def _create_clients_small(self, facility, users):
        admin = users[0]
        for pseudonym, stage, age in SMALL_CLIENTS:
            Client.objects.get_or_create(
                facility=facility,
                pseudonym=pseudonym,
                defaults={
                    "contact_stage": stage,
                    "age_cluster": age,
                    "created_by": admin,
                },
            )

    # ------------------------------------------------------------------
    # Clients – bulk (medium / large)
    # ------------------------------------------------------------------
    def _create_clients_bulk(self, facility, users, cfg):
        """Create clients via bulk_create. Returns list of Client objects."""
        count = cfg["clients_per_facility"]
        existing = set(Client.objects.filter(facility=facility).values_list("pseudonym", flat=True))
        admin = users[0]
        stages = list(Client.ContactStage.values)
        ages = list(Client.AgeCluster.values)

        # Pure nicknames for the first len(_SPITZNAMEN) clients, suffix for overflow
        available = list(_SPITZNAMEN)
        random.shuffle(available)
        pseudonyms = available[:count]
        if count > len(available):
            for i in range(count - len(available)):
                pseudonyms.append(f"{random.choice(available)}-{i + 1}")

        to_create = []
        for pseudonym in pseudonyms:
            if pseudonym in existing:
                continue
            to_create.append(
                Client(
                    facility=facility,
                    pseudonym=pseudonym,
                    contact_stage=random.choice(stages),
                    age_cluster=random.choice(ages),
                    created_by=admin,
                )
            )

        if to_create:
            Client.objects.bulk_create(to_create, batch_size=1000)

        return list(Client.objects.filter(facility=facility))

    # ------------------------------------------------------------------
    # Events – small (backward-compatible, identical to original)
    # ------------------------------------------------------------------
    def _create_events_small(self, facility):
        """Create 25 demo events over the last 80 days."""
        if Event.objects.filter(facility=facility).exists():
            return

        facility_users = list(User.objects.filter(facility=facility))
        staff_users = [u for u in facility_users if u.role in (User.Role.STAFF, User.Role.LEAD)]
        if not staff_users:
            staff_users = facility_users
        clients = list(Client.objects.filter(facility=facility))
        doc_types = {dt.name: dt for dt in DocumentType.objects.filter(facility=facility)}

        today = date.today()
        event_defs = [
            ("Kontakt", 0, False, 2, {"dauer": 15, "leistungen": ["beratung", "essen"], "notiz": "Erstbesuch"}),
            (
                "Kontakt",
                1,
                False,
                5,
                {"dauer": 30, "leistungen": ["kleidung", "sachspenden"], "notiz": "Winterjacke ausgegeben"},
            ),
            ("Kontakt", None, True, 8, {"dauer": 10, "leistungen": ["essen"], "notiz": "Kurzer Besuch"}),
            ("Kontakt", 2, False, 12, {"dauer": 20, "leistungen": ["duschen", "waesche"]}),
            ("Kontakt", 3, False, 18, {"dauer": 45, "leistungen": ["beratung", "telefon"]}),
            ("Kontakt", None, True, 25, {"dauer": 5, "leistungen": ["sonstiges"]}),
            ("Kontakt", 4, False, 35, {"dauer": 25, "leistungen": ["essen", "beratung"]}),
            ("Kontakt", 0, False, 50, {"dauer": 20, "leistungen": ["post", "sachspenden"]}),
            ("Kontakt", 5, False, 65, {"dauer": 15, "leistungen": ["essen"]}),
            ("Kontakt", None, True, 80, {"dauer": 10}),
            ("Krisengespräch", 0, False, 3, {"art-der-krise": "psychische-krise", "dauer": 60}),
            ("Krisengespräch", 1, False, 30, {"art-der-krise": "substanzkrise", "dauer": 45}),
            ("Medizinische Versorgung", 2, False, 7, {"art-der-versorgung": "wundversorgung"}),
            ("Medizinische Versorgung", 3, False, 40, {"art-der-versorgung": "medikamentenausgabe"}),
            ("Spritzentausch", None, True, 4, {"ausgabe": 10, "rueckgabe": 8}),
            ("Spritzentausch", 4, False, 20, {"ausgabe": 5, "rueckgabe": 5}),
            ("Spritzentausch", None, True, 55, {"ausgabe": 15, "rueckgabe": 12}),
            ("Begleitung", 0, False, 10, {"ziel": "Jobcenter"}),
            ("Begleitung", 1, False, 45, {"ziel": "Krankenhaus"}),
            ("Beratungsgespräch", 4, False, 6, {"dauer": 30}),
            ("Beratungsgespräch", 6, False, 22, {"dauer": 45}),
            ("Vermittlung", 3, False, 15, {}),
            ("Vermittlung", 0, False, 60, {}),
            ("Notiz", 1, False, 9, {"notiz": "Termin beim Arzt vereinbart"}),
            (
                "Hausverbot",
                2,
                False,
                1,
                {
                    "grund": "Wiederholte Verstöße gegen die Hausordnung",
                    "bis": (today + timedelta(days=30)).isoformat(),
                    "aktiv": True,
                },
            ),
        ]

        for idx, (dt_name, client_idx, is_anonymous, days_ago, data_json) in enumerate(event_defs):
            doc_type = doc_types.get(dt_name)
            if doc_type is None:
                continue
            client = clients[client_idx] if client_idx is not None else None
            hour, minute = Command._random_time_of_day()
            base_date = timezone.now() - timedelta(days=days_ago)
            occurred = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
            Event.objects.create(
                facility=facility,
                client=client,
                document_type=doc_type,
                occurred_at=occurred,
                data_json=data_json,
                is_anonymous=is_anonymous,
                created_by=staff_users[idx % len(staff_users)],
            )

    # ------------------------------------------------------------------
    # Events – bulk (medium / large)
    # ------------------------------------------------------------------
    def _create_events_bulk(self, facility, users, clients, cfg):
        """Generate events with bulk_create for medium/large scales."""
        target = cfg["events_per_facility"]
        zeitraum = cfg["zeitraum_days"]

        existing_count = Event.objects.filter(facility=facility).count()
        if existing_count >= target:
            return

        to_create_count = target - existing_count
        doc_types = list(DocumentType.objects.filter(facility=facility))
        if not doc_types or not clients:
            return

        # Pre-build data templates per document type (skip encrypted fields).
        dt_data_templates = {}
        for dt in doc_types:
            dt_data_templates[dt.system_type or dt.name] = self._build_data_template(dt)

        # Realistic weighting by system_type: bans extremely rare
        dt_weights = {
            "contact": 40,
            "crisis": 12,
            "medical": 10,
            "needle_exchange": 10,
            "accompaniment": 8,
            "counseling": 8,
            "referral": 5,
            "note": 6,
            "ban": 0.2,
        }
        weights = [dt_weights.get(dt.system_type, 5) for dt in doc_types]
        today = timezone.localdate()

        now = timezone.now()
        # Fachkräfte (STAFF/LEAD) für gleichmäßige Verteilung
        staff_users = [u for u in users if u.role in (User.Role.STAFF, User.Role.LEAD)]
        if not staff_users:
            staff_users = users
        batch = []
        hausverbot_active_count = 0
        for i in range(to_create_count):
            doc_type = random.choices(doc_types, weights=weights, k=1)[0]
            is_anonymous = random.random() < 0.15
            client = None if is_anonymous else random.choice(clients)
            days_ago = self._weighted_days_ago(zeitraum)
            if days_ago == 0:
                hour, minute = self._random_time_of_day(max_hour=now.hour, max_minute=now.minute)
            else:
                hour, minute = self._random_time_of_day()
            base_date = now - timedelta(days=days_ago)
            occurred = min(
                base_date.replace(hour=hour, minute=minute, second=0, microsecond=0),
                now,
            )
            data_json = self._random_data(doc_type.system_type or doc_type.name, dt_data_templates)

            # Bans realistic: most expired, max 2 active (grund from pool)
            if doc_type.system_type == "ban":
                if hausverbot_active_count < 2 and random.random() < 0.15:
                    data_json["aktiv"] = True
                    data_json["bis"] = (today + timedelta(days=random.randint(7, 90))).isoformat()
                    hausverbot_active_count += 1
                else:
                    data_json["aktiv"] = False
                    data_json["bis"] = (today - timedelta(days=random.randint(30, 365))).isoformat()

            batch.append(
                Event(
                    facility=facility,
                    client=client,
                    document_type=doc_type,
                    occurred_at=occurred,
                    data_json=data_json,
                    is_anonymous=is_anonymous,
                    created_by=staff_users[i % len(staff_users)],
                )
            )

        if batch:
            for event in batch:
                event.data_json = encrypt_event_data(event.document_type, event.data_json)
            Event.objects.bulk_create(batch, batch_size=1000)
            self.stdout.write(f"  {len(batch)} Events für {facility.name} erstellt.")

    @staticmethod
    def _random_time_of_day(max_hour=None, max_minute=None):
        """Weighted hour distribution matching typical opening hours (8-19h).

        Morgen-Anlauf → Vormittag-Peak → Nachmittag → Abend ausklingend.
        If max_hour is set, limits generated times (for today's events).
        """
        hours = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
        weights = [5, 8, 15, 15, 10, 8, 12, 12, 10, 8, 5, 3]
        if max_hour is not None:
            filtered = [(h, w) for h, w in zip(hours, weights) if h <= max_hour]
            if not filtered:
                return 8, 0
            hours, weights = zip(*filtered)
            hours, weights = list(hours), list(weights)
        hour = random.choices(hours, weights=weights, k=1)[0]
        if max_hour is not None and hour == max_hour and max_minute is not None:
            minute = random.randint(0, max(0, max_minute // 5)) * 5
        else:
            minute = random.randint(0, 11) * 5  # 0, 5, 10, ..., 55
        return hour, minute

    @staticmethod
    def _weighted_days_ago(zeitraum):
        """Weight towards recent: 40% last 30d, 30% 31-90d, 30% older."""
        r = random.random()
        if r < 0.40:
            return random.randint(0, min(30, zeitraum))
        elif r < 0.70:
            return random.randint(min(31, zeitraum), min(90, zeitraum))
        else:
            return random.randint(min(91, zeitraum), zeitraum)

    def _build_data_template(self, doc_type):
        """Return field metadata for a doc type, excluding encrypted fields."""
        fields = []
        for dtf in doc_type.fields.select_related("field_template").all():
            ft = dtf.field_template
            if ft.is_encrypted:
                continue
            fields.append(
                {
                    "key": ft.slug,
                    "type": ft.field_type,
                    "options": ft.options_json or [],
                    "required": ft.is_required,
                }
            )
        return fields

    def _random_data(self, dt_name, dt_data_templates):
        """Generate plausible random data_json for a given document type."""
        pool = _EVENT_DATA_POOLS.get(dt_name)
        if pool:
            template = copy.deepcopy(random.choice(pool))
            # Minor variation on numeric fields
            if "dauer" in template:
                template["dauer"] = max(5, template["dauer"] + random.randint(-5, 10))
            if "ausgabe" in template:
                template["ausgabe"] = max(1, template["ausgabe"] + random.randint(-2, 5))
                template["rueckgabe"] = min(template["rueckgabe"], template["ausgabe"])
            # Dynamic dates for accompaniment
            if dt_name == "accompaniment":
                offset = random.randint(-7, 14)
                template["datum"] = (date.today() + timedelta(days=offset)).isoformat()
                template["uhrzeit"] = f"{random.randint(8, 16):02d}:{random.choice(['00', '30'])}"
            # Dynamic next appointment for counseling
            if dt_name == "counseling":
                template["naechster-termin"] = (date.today() + timedelta(days=random.randint(7, 28))).isoformat()
            return template
        # Fallback: generic generation for unknown document types
        fields = dt_data_templates.get(dt_name, [])
        data = {}
        for f in fields:
            key = f["key"]
            ftype = f["type"]
            options = f["options"]
            if ftype == "number":
                data[key] = random.randint(1, 120)
            elif ftype == "select" and options:
                slugs = [o["slug"] if isinstance(o, dict) else o for o in options]
                data[key] = random.choice(slugs)
            elif ftype == "multi_select" and options:
                slugs = [o["slug"] if isinstance(o, dict) else o for o in options]
                k = random.randint(1, min(3, len(slugs)))
                data[key] = random.sample(slugs, k)
            elif ftype == "boolean":
                data[key] = random.choice([True, False])
            elif ftype == "textarea":
                data[key] = f"Seed-Notiz {random.randint(1, 9999)}"
            elif ftype == "text":
                data[key] = f"Seed-Text {random.randint(1, 9999)}"
            elif ftype == "date":
                offset = random.randint(1, 60)
                data[key] = (date.today() - timedelta(days=offset)).isoformat()
            elif ftype == "time":
                data[key] = f"{random.randint(8, 20):02d}:{random.choice(['00', '15', '30', '45'])}"
        return data

    # ------------------------------------------------------------------
    # Cases (medium / large only)
    # ------------------------------------------------------------------
    def _create_cases(self, facility, users, clients, cfg):
        count = cfg["cases"]
        if count == 0:
            return
        existing = Case.objects.filter(facility=facility).count()
        if existing >= count:
            return

        qualified_clients = [c for c in clients if c.contact_stage == Client.ContactStage.QUALIFIED]
        if not qualified_clients:
            qualified_clients = clients

        to_create = []
        for i in range(count - existing):
            client = qualified_clients[i % len(qualified_clients)]
            title = _CASE_TITLES[i % len(_CASE_TITLES)]
            status = Case.Status.OPEN if random.random() < 0.7 else Case.Status.CLOSED
            closed_at = timezone.now() - timedelta(days=random.randint(1, 60)) if status == Case.Status.CLOSED else None
            to_create.append(
                Case(
                    facility=facility,
                    client=client,
                    title=f"{title} ({facility.name})" if count > len(_CASE_TITLES) else title,
                    description=_CASE_DESCRIPTIONS.get(title, f"Fallarbeit: {title}"),
                    status=status,
                    closed_at=closed_at,
                    created_by=random.choice(users),
                    lead_user=random.choice(users),
                )
            )

        if to_create:
            Case.objects.bulk_create(to_create, batch_size=1000)
            self.stdout.write(f"  {len(to_create)} Cases für {facility.name} erstellt.")

    # ------------------------------------------------------------------
    # Episodes
    # ------------------------------------------------------------------
    def _create_episodes(self, facility, users, cases, cfg):
        count = cfg.get("episodes", 0)
        if count == 0:
            return

        open_cases = [c for c in cases if c.status == Case.Status.OPEN]
        if not open_cases:
            return

        to_create = []
        for _ in range(count):
            case = random.choice(open_cases)
            title = random.choice(_EPISODE_TITLES)
            days_ago = random.randint(1, 180)
            started_at = date.today() - timedelta(days=days_ago)
            ended_at = None
            if random.random() < 0.3:
                max_duration = max(7, (date.today() - started_at).days)
                ended_at = started_at + timedelta(days=random.randint(7, max_duration))
            to_create.append(
                Episode(
                    case=case,
                    title=title,
                    description=_EPISODE_DESCRIPTIONS.get(title, ""),
                    started_at=started_at,
                    ended_at=ended_at,
                    created_by=random.choice(users),
                )
            )

        if to_create:
            Episode.objects.bulk_create(to_create, batch_size=1000)
            self.stdout.write(f"  {len(to_create)} Episoden für {facility.name} erstellt.")

    # ------------------------------------------------------------------
    # OutcomeGoals & Milestones
    # ------------------------------------------------------------------
    def _create_goals(self, facility, users, cases, cfg):
        count = cfg.get("goals", 0)
        if count == 0:
            return
        if not cases:
            return

        milestones_per_goal = cfg.get("milestones_per_goal", 3)
        goals_to_create = []
        milestones_to_create = []

        for _ in range(count):
            case = random.choice(cases)
            title = random.choice(_GOAL_TITLES)
            is_achieved = random.random() < 0.3
            achieved_at = date.today() - timedelta(days=random.randint(1, 90)) if is_achieved else None
            goal = OutcomeGoal(
                case=case,
                title=title,
                description=_GOAL_DESCRIPTIONS.get(title, ""),
                is_achieved=is_achieved,
                achieved_at=achieved_at,
                created_by=random.choice(users),
            )
            goals_to_create.append(goal)

        if goals_to_create:
            OutcomeGoal.objects.bulk_create(goals_to_create, batch_size=1000)
            # Refresh from DB to get IDs assigned by bulk_create
            created_goals = list(
                OutcomeGoal.objects.filter(
                    case__facility=facility,
                ).order_by("-created_at")[:count]
            )
            for goal in created_goals:
                for i in range(milestones_per_goal):
                    title = random.choice(_MILESTONE_TITLES)
                    is_completed = random.random() < 0.5
                    completed_at = date.today() - timedelta(days=random.randint(1, 60)) if is_completed else None
                    milestones_to_create.append(
                        Milestone(
                            goal=goal,
                            title=title,
                            is_completed=is_completed,
                            completed_at=completed_at,
                            sort_order=i,
                        )
                    )

            if milestones_to_create:
                Milestone.objects.bulk_create(milestones_to_create, batch_size=1000)

            self.stdout.write(
                f"  {len(created_goals)} Wirkungsziele und "
                f"{len(milestones_to_create)} Meilensteine für {facility.name} erstellt."
            )

    # ------------------------------------------------------------------
    # Assign events to cases (medium / large only)
    # ------------------------------------------------------------------
    def _assign_events_to_cases(self, facility, cases, cfg):
        if cfg["cases"] <= 3:
            return
        if not cases:
            return

        cases_with_clients = [c for c in cases if c.client_id is not None]
        assigned_count = 0
        for case in cases_with_clients:
            unassigned = list(
                Event.objects.filter(
                    facility=facility,
                    client_id=case.client_id,
                    case__isnull=True,
                )[:5]
            )
            if not unassigned:
                continue
            k = min(random.randint(3, 5), len(unassigned))
            for event in unassigned[:k]:
                event.case = case
            Event.objects.bulk_update(unassigned[:k], ["case"], batch_size=500)
            assigned_count += k

        if assigned_count:
            self.stdout.write(f"  {assigned_count} Events Cases zugeordnet ({facility.name}).")

    # ------------------------------------------------------------------
    # WorkItems (medium / large only)
    # ------------------------------------------------------------------
    def _create_work_items(self, facility, users, clients, cfg):
        count = cfg["work_items"]
        if count == 0:
            return
        existing = WorkItem.objects.filter(facility=facility).count()
        if existing >= count:
            return

        statuses = list(WorkItem.Status.values)
        priorities = list(WorkItem.Priority.values)
        item_types = list(WorkItem.ItemType.values)

        today = date.today()
        now = timezone.now()
        zeitraum = cfg["zeitraum_days"]
        to_create = []
        timestamps = []
        for i in range(count - existing):
            title = _WORK_ITEM_TITLES[i % len(_WORK_ITEM_TITLES)]
            client = random.choice(clients) if random.random() < 0.7 else None
            priority = random.choice(priorities)
            status = random.choice(statuses)

            # Realistic created_at spread over the seed timeframe
            days_ago = self._weighted_days_ago(zeitraum)
            hour, minute = self._random_time_of_day()
            created_ts = now - timedelta(days=days_ago, hours=now.hour - hour, minutes=now.minute - minute)
            created_ts = min(created_ts, now)
            timestamps.append(created_ts)

            completed_at = (
                min(created_ts + timedelta(days=random.randint(1, 30)), now)
                if status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED)
                else None
            )

            # Due-date distribution matching priority
            due_date = self._random_due_date(today, priority, status)

            to_create.append(
                WorkItem(
                    facility=facility,
                    client=client,
                    created_by=random.choice(users),
                    assigned_to=random.choice(users) if random.random() < 0.6 else None,
                    item_type=random.choice(item_types),
                    status=status,
                    priority=priority,
                    title=title,
                    description=_WORK_ITEM_DESCRIPTIONS.get(title, ""),
                    due_date=due_date,
                    completed_at=completed_at,
                )
            )

        if to_create:
            WorkItem.objects.bulk_create(to_create, batch_size=1000)
            # Fix auto_now_add: set realistic created_at timestamps
            created_items = list(WorkItem.objects.filter(facility=facility).order_by("pk")[existing:])
            for wi, ts in zip(created_items, timestamps):
                WorkItem.objects.filter(pk=wi.pk).update(created_at=ts)
            self.stdout.write(f"  {len(to_create)} WorkItems für {facility.name} erstellt.")

    @staticmethod
    def _random_due_date(today, priority, status):
        """Realistic due_date distribution matching priority and status."""
        # ~30% without deadline
        if random.random() < 0.30:
            return None

        is_active = status in ("open", "in_progress")

        if priority == "urgent":
            # Urgent: rather today/tomorrow, rarely far in the future
            if is_active and random.random() < 0.15:
                return today - timedelta(days=random.randint(1, 7))
            return today + timedelta(days=random.randint(0, 3))
        elif priority == "important":
            # Important: rather this/next week
            if is_active and random.random() < 0.10:
                return today - timedelta(days=random.randint(1, 14))
            return today + timedelta(days=random.randint(0, 14))
        else:
            # Normal: mixed
            if is_active and random.random() < 0.10:
                return today - timedelta(days=random.randint(1, 14))
            return today + timedelta(days=random.randint(1, 60))

    def _create_activities(self, facility, users, cfg):
        """Create Activity entries retroactively for seeded data.

        Activities are spread evenly over the seed timeframe (zeitraum_days)
        instead of clustering on the seed run date.
        """
        from django.contrib.contenttypes.models import ContentType

        if Activity.objects.filter(facility=facility).exists():
            return

        ct_client = ContentType.objects.get_for_model(Client)
        ct_event = ContentType.objects.get_for_model(Event)
        ct_workitem = ContentType.objects.get_for_model(WorkItem)
        ct_case = ContentType.objects.get_for_model(Case)

        zeitraum = cfg["zeitraum_days"]
        now = timezone.now()

        def _random_past_ts():
            """Return a random timestamp weighted towards the recent past."""
            # 60% within last 30 days, 25% within 31-90 days, 15% older
            r = random.random()
            if r < 0.60:
                days = random.randint(0, min(30, zeitraum))
            elif r < 0.85:
                days = random.randint(min(31, zeitraum), min(90, zeitraum))
            else:
                days = random.randint(min(91, zeitraum), zeitraum)
            hour, minute = Command._random_time_of_day()
            ts = now - timedelta(days=days, hours=now.hour - hour, minutes=now.minute - minute)
            return min(ts, now)

        activities = []

        # Activities for clients
        for client in Client.objects.filter(facility=facility):
            created_ts = _random_past_ts()
            activities.append(
                Activity(
                    facility=facility,
                    actor=random.choice(users),
                    verb=Activity.Verb.CREATED,
                    target_type=ct_client,
                    target_id=client.pk,
                    summary=f"Klientel {client.pseudonym} angelegt",
                    occurred_at=created_ts,
                )
            )
            if client.contact_stage == Client.ContactStage.QUALIFIED:
                activities.append(
                    Activity(
                        facility=facility,
                        actor=random.choice(users),
                        verb=Activity.Verb.QUALIFIED,
                        target_type=ct_client,
                        target_id=client.pk,
                        summary=f"{client.pseudonym} qualifiziert",
                        occurred_at=min(created_ts + timedelta(hours=random.randint(1, 48)), now),
                    )
                )

        # Activities for events: ALL recent (90d), 30% of older events
        cutoff_90d = now - timedelta(days=90)
        all_events = list(
            Event.objects.filter(facility=facility, is_deleted=False).select_related("document_type", "client")
        )
        recent_events = [e for e in all_events if e.occurred_at >= cutoff_90d]
        older_events = [e for e in all_events if e.occurred_at < cutoff_90d]
        older_sample = random.sample(older_events, min(len(older_events), len(older_events) * 3 // 10))
        sampled_events = recent_events + older_sample
        for event in sampled_events:
            summary = event.document_type.name
            if event.client:
                summary += f" für {event.client.pseudonym}"
            activities.append(
                Activity(
                    facility=facility,
                    actor=event.created_by or random.choice(users),
                    verb=Activity.Verb.CREATED,
                    target_type=ct_event,
                    target_id=event.pk,
                    summary=summary,
                    occurred_at=event.occurred_at,
                )
            )

        # Activities for work items
        for wi in WorkItem.objects.filter(facility=facility):
            wi_created_ts = _random_past_ts()
            activities.append(
                Activity(
                    facility=facility,
                    actor=wi.created_by or random.choice(users),
                    verb=Activity.Verb.CREATED,
                    target_type=ct_workitem,
                    target_id=wi.pk,
                    summary=f"Aufgabe: {wi.title}",
                    occurred_at=wi_created_ts,
                )
            )
            if wi.status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED):
                activities.append(
                    Activity(
                        facility=facility,
                        actor=wi.assigned_to or wi.created_by or random.choice(users),
                        verb=Activity.Verb.COMPLETED,
                        target_type=ct_workitem,
                        target_id=wi.pk,
                        summary=f"Aufgabe erledigt: {wi.title}",
                        occurred_at=min(wi_created_ts + timedelta(days=random.randint(1, 14)), now),
                    )
                )

        # Activities for cases
        for case in Case.objects.filter(facility=facility):
            activities.append(
                Activity(
                    facility=facility,
                    actor=case.created_by or random.choice(users),
                    verb=Activity.Verb.CREATED,
                    target_type=ct_case,
                    target_id=case.pk,
                    summary=f"Fall eröffnet: {case.title}",
                    occurred_at=_random_past_ts(),
                )
            )

        if activities:
            Activity.objects.bulk_create(activities, batch_size=1000)
            self.stdout.write(f"  {len(activities)} Activities für {facility.name} erstellt.")

    # ------------------------------------------------------------------
    # DeletionRequests (medium / large only)
    # ------------------------------------------------------------------
    def _create_deletion_requests(self, facility, users, cfg):
        count = cfg.get("deletion_requests", 0)
        if count == 0:
            return
        existing = DeletionRequest.objects.filter(facility=facility).count()
        if existing >= count:
            return

        events = list(Event.objects.filter(facility=facility, is_deleted=False)[: count * 2])
        if not events:
            return

        reasons = [
            "Klientel hat Löschung gemäß Art. 17 DSGVO beantragt.",
            "Fehlerhafter Eintrag — falscher Klientel zugeordnet.",
            "Doppelter Eintrag — bereits unter anderem Datum erfasst.",
            "Aufbewahrungsfrist abgelaufen.",
        ]

        to_create = []
        for i in range(min(count - existing, len(events))):
            event = events[i]
            requester = random.choice(users)
            status = random.choices(
                [
                    DeletionRequest.Status.PENDING,
                    DeletionRequest.Status.APPROVED,
                    DeletionRequest.Status.REJECTED,
                ],
                weights=[0.5, 0.3, 0.2],
            )[0]

            reviewer = None
            reviewed_at = None
            if status != DeletionRequest.Status.PENDING:
                # Constraint: requested_by != reviewed_by
                other_users = [u for u in users if u != requester]
                if other_users:
                    reviewer = random.choice(other_users)
                    reviewed_at = timezone.now() - timedelta(days=random.randint(1, 30))
                else:
                    status = DeletionRequest.Status.PENDING

            to_create.append(
                DeletionRequest(
                    facility=facility,
                    target_type=DeletionRequest.TargetType.EVENT,
                    target_id=event.id,
                    reason=random.choice(reasons),
                    status=status,
                    requested_by=requester,
                    reviewed_by=reviewer,
                    reviewed_at=reviewed_at,
                )
            )

        if to_create:
            DeletionRequest.objects.bulk_create(to_create, batch_size=500)
            self.stdout.write(f"  {len(to_create)} DeletionRequests für {facility.name} erstellt.")
