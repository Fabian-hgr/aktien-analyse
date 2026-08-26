# Der Vermittler

Ein Cloudflare Worker, der zwischen der Seite und dem Sprachmodell steht.

Er existiert aus einem einzigen Grund: Die Seite liegt statisch auf GitHub
Pages, das Repo ist öffentlich. Ein API-Schlüssel in ihrem Quelltext wäre ein
veröffentlichter Schlüssel. Der Worker hält die Verbindung stattdessen über
eine Bindung — es gibt hier gar kein Geheimnis, das auslaufen könnte, und
deshalb steht in diesem Ordner auch keines.

## Was er tut, und was er nicht tut

Er bekommt von der Seite eine Frage **und** die Zahlen, um die es geht, und
lässt das Modell nur formulieren. Er sucht selbst keine Daten heraus — das tut
die Seite, weil die Daten dort ohnehin liegen. Dieselbe Suchlogik ein zweites
Mal hier zu bauen hiesse, sie zweimal pflegen zu müssen.

## Schnittstelle

```
POST /frage    { "frage": "…", "fakten": "…", "modell": "mistral" }
               → text/event-stream, Antwort Stück für Stück
GET  /gesund   → { ok, modell, begrenzer }   ohne Herkunftsprüfung
```

Angenommen werden nur Anfragen von `https://fabian-hgr.github.io` sowie von
`localhost`/`127.0.0.1` für den Test vor dem Hochladen. Alles andere: 403.

Grenzen: Frage 500 Zeichen, Fakten 24'000 Zeichen, Rumpf 32'768 Zeichen,
Antwort 1'200 Token, 20 Anfragen je Minute und IP-Adresse.

## Neu hochladen

Ohne Node und ohne Wrangler, nur mit `curl`. Nötig sind ein API-Token mit der
Vorlage **Edit Cloudflare Workers** und die Account-ID — beides **nicht** in
diesem Repo ablegen.

```bash
KONTO=<account-id>
cat > /tmp/kopf.txt <<'X'
Authorization: Bearer <token>
X
cat > /tmp/metadata.json <<'X'
{ "main_module": "worker.js",
  "compatibility_date": "2026-08-25",
  "bindings": [
    { "type": "ai", "name": "AI" },
    { "type": "plain_text", "name": "MODELL", "text": "mistral" },
    { "type": "ratelimit", "name": "BEGRENZER", "namespace_id": "1001",
      "simple": { "limit": 20, "period": 60 } } ] }
X
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$KONTO/workers/scripts/aktien-frage" \
  -H @/tmp/kopf.txt \
  -F "metadata=</tmp/metadata.json;type=application/json" \
  -F "worker.js=@src/worker.js;type=application/javascript+module"
```

`wrangler.toml` beschreibt dieselbe Einrichtung für den Fall, dass später doch
Node vorhanden ist. Das Modell lässt sich ohne Änderung am Code umstellen:
`MODELL` in der Bindung auf `llama`, `mistral`, `qwen`, `klein` oder `gemma`
setzen (siehe `MODELLE` in `src/worker.js`) und neu hochladen.

## Warum Mistral

Gemessen, nicht geraten — die Zahlen stehen im Haupt-README unter „Die
Fragezeile". Kurz: Gemma lieferte bei zwei von drei Fragen gar keine Antwort,
weil das Token-Budget für englisches Nachdenken draufging; Llama zählte Zahlen
auf, statt zu antworten; Qwen braucht jedes Mal 600–2'000 Zeichen Vorlauf.
Mistral antwortet nach 0.2 Sekunden, rechnet richtig aus den Fakten und
verweigert Anlageempfehlungen.
