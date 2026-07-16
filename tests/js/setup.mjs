// Globales Test-Setup (Refs #1479, TST-01): stellt ein funktionierendes
// IndexedDB im jsdom-Kontext bereit, damit Module, die es beim Laden
// referenzieren, nicht crashen. Die Queue-/Replay-Tests mocken den
// offlineStore zwar direkt, aber ein reales IndexedDB haelt die Umgebung
// naeher an den Browser und deckt kuenftige store-nahe Tests ab.
import "fake-indexeddb/auto";
