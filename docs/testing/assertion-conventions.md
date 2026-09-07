# Assertion-Konventionen

Tests sollen fachliche Verträge so genau beschreiben, dass eine inhaltlich
veränderte Fehlermeldung, Log-Zeile oder Schreibmenge sichtbar fehlschlägt.

## Exceptions

- Bei `ValidationError` die vollständige Liste `exc_info.value.messages`
  vergleichen. Wenn nur `match=` praktikabel ist, den vollständigen relevanten
  Wortlaut einschließlich Satzzeichen prüfen und Regex-Sonderzeichen escapen.
- Ein nacktes `pytest.raises(...)` genügt nur, wenn ausschließlich der
  Exception-Typ Vertragsbestandteil ist. Die Abweichung gehört in einen
  Testkommentar.

## Logs

- `caplog.records` zuerst gezielt nach Loggername und Level filtern.
- Die vollständig gerenderte Meldung über `record.getMessage()` vergleichen;
  bei strukturierten Logs zusätzlich alle vertraglichen `extra`-Felder exakt
  prüfen.
- Globale Annahmen wie `len(caplog.records) == 1` vermeiden: andere Logger oder
  Handler dürfen zusätzliche Records erzeugen.

## Eingeschränkte Saves und Query-Budgets

- Für `save(update_fields=...)` das vom `post_save`-Signal gelieferte
  `update_fields` als exakte Feldmenge festhalten. So werden versehentliche
  Voll-Saves und verlorene Paralleländerungen sichtbar.
- Query-Budgets sind begründete Obergrenzen, keine Momentaufnahme. Vor der
  Messung gemeinsame Caches wie den `ContentType`-Cache aufwärmen und innerhalb
  der Messklammer QuerySets tatsächlich auswerten.
