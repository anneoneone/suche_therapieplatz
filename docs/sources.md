# Data Sources for Berlin Healthcare Providers

The `crawl-berlin` command aggregates providers from several directories.
Given a street address, it geocodes it, queries each enabled source within
a radius, merges duplicates across sources, and returns the **N closest
providers** ranked by haversine distance.

## Quick reference

| Name          | Directory                               | Access       | Coverage gain                                  |
|---------------|-----------------------------------------|--------------|------------------------------------------------|
| `116117`      | KBV Arztsuche (`arztsuche.116117.de`)   | Public API   | All Kassensitz providers (baseline)            |
| `osm`         | OpenStreetMap Overpass                  | Free ODbL    | Crowdsourced, incl. some private-pay practices |
| `psych_info`  | `psych-info.de` (voluntary chamber reg.) | **Residential only** | Private-only Approbierte that 116117 misses |
| `therapie_de` | `therapie.de` Berlin listing            | **Residential only** | Heilpraktiker (HP-Psychotherapie) + private   |
| `psychotherapeutensuche` | `psychotherapeutensuche.de` (PsyOS GmbH) | Public HTML | Nationwide voluntary directory (~10.6k profiles), incl. private-pay |
| `ptk_bayern`  | PTK Bayern Suchdienst (`ptk-bayern.de`) | Public JSON | **Bavaria only** — best email coverage (~30% of entries) |

The first two work from anywhere and are the defaults. `psych_info` and
`therapie_de` require a residential IP — every test run from a cloud /
GitHub Actions runner was WAF-blocked (`"Sie haben leider keinen Zugriff
auf diese Seite"`).

`psychotherapeutensuche` is the friendliest scrape target so far: TYPO3 +
Codappix SearchCore (Elasticsearch) serving plain server-rendered HTML.
The radius search is a GET on `/therapeuten/?search[searchRequest][filter]
[distance][...]` (lat/lon + one of 1/2/5/10/25/50 km; page 2+ at
`/therapeuten/seite/{n}/`), results come back distance-sorted, and profile
pages carry schema.org `Person` microdata (name, jobTitle, gender, address,
telephone) plus a Kostenträger card (GKV / Privat / Kostenerstattung).
Profiles list **no email addresses** — contact is phone or the site's form.
robots.txt only disallows `/suche/?*` + `/typo3/*` and publishes a
`therapists.xml` sitemap; the URLs we fetch are allowed. Verified working
from a residential IP; cloud/CI behaviour is untested (no WAF observed).
It also backs the frontend's live search via `POST /api/therapists/crawl`.

`ptk_bayern` is even friendlier: the chamber's Lotus-Domino search form
(`/ptk/web.nsf/formular?openForm&formular=depsychotherapeutensuche`) is
backed by a JSON agent at
`/ptk/adressen.nsf/ptk_search_psychotherapeuten?OpenAgent&plz=…&umkreis=…`
that returns the **complete result set in one request** — name, academic
title, address, phone, **email** (~30% of entries; the only source besides
116117/OSM that exposes emails at all), homepage, lat/lon coordinates,
patient group (kin/erw) and GKV/PKV flags. Send `versicherung=pkvgkv` and
`abrechnung=pkvgkv` (the form defaults) — omitting them yields an empty
result set. The upstream search is PLZ/Ort-based, so `SearchParams.
postal_code` (or `city`) must be set. robots.txt has no Disallow rules.
Coverage is Bavaria only. The crawl endpoint supports it via
`{"source": "ptk_bayern", ...}` plus an optional `require_email` filter.

**Skipped on purpose:**

- **PTK Berlin** delegates its search to psych-info.de, so wrapping it was
  redundant.
- **Ärztekammer Berlin / KV Berlin** overlap 100% with 116117 for Kassensitz
  providers.
- **Doctolib** is DataDome-protected and ToS-hostile (residential IP alone
  isn't enough).
- **Jameda** AGB explicitly bans automated extraction with litigation history.

## Usage

Default (CI-safe, works from anywhere):

```bash
poetry run therapist-finder crawl-berlin \
  --address "Kastanienallee 12, 10435 Berlin" \
  --max 20 \
  --output clients/anna/
```

Full coverage (run from your laptop, not in CI):

```bash
poetry run therapist-finder crawl-berlin \
  --address "Kastanienallee 12, 10435 Berlin" \
  --max 20 \
  --sources 116117,osm,psych_info,therapie_de \
  --output clients/anna/
```

Output files (when `--output` is given):

- `therapists_nearest.json` — full merged records, sorted by `distance_km`.
- `therapists_nearest.md` — Markdown table preview.

## Behaviour

- Each source is queried in parallel.
- HTML scrapers honour `robots.txt` by default, use an identifying
  User-Agent, and pace requests (≥2 s/host for psych-info, ≥3 s/host for
  therapie.de).
- Geocoding uses Nominatim (1 rps, identifying UA) and is disk-cached.
- Deduplication matches by normalised last name + postcode + house number,
  falling back to email. Merged records union their non-null fields and
  record all contributing sources in `sources`.
- **Legal hygiene for therapie.de**: §87b UrhG (German DB-right) protects
  "wesentliche Teile" of a database. Store only fields you display, cap
  request rates, and never republish the raw corpus.

## Why the Kammer scrapers were removed

An earlier iteration shipped `PTKBerlinSource` + `ArztauskunftBerlinSource`.
The recon (see `scripts/recon_sources.py`) confirmed:

1. `psychotherapeutenkammer-berlin.de` WAF-blocks cloud IPs and its
   `Psychotherapeut:innensuche` is in fact a frontend for psych-info.de.
2. `arztauskunft-berlin.de` wasn't a real domain — the Ärztekammer's doctor
   search lives at `aekb.de` / `bundesaerztekammer.de/arztsuche` and
   substantially overlaps 116117.
3. `kvberlin.de` uses the same KBV feed as 116117.

So the useful additions for Berlin are psych-info.de (for private-pay
psychotherapists) and therapie.de (for Heilpraktiker). Both are residential
only.

## Finding hidden JSON APIs (recon)

If you suspect a site is really an SPA backed by JSON (like
`arztsuche.116117.de`), run the recon script. It launches headless
Chromium, enumerates forms/scripts/iframes, submits a `"Berlin"` search,
and captures every XHR/fetch call.

Locally (residential IP — the recommended path):

```bash
poetry install --with dev
poetry run playwright install chromium
poetry run python scripts/recon_sources.py
```

Or trigger the **Recon Berlin directories** GitHub Actions workflow — the
run summary and `recon/` artifact capture each site's form and script
structure. Be aware that the Kammer sites will likely return "Zugriff
verweigert" from CI IPs; residential runs give real data.

Optional flags:

```bash
# Only probe specific targets
poetry run python scripts/recon_sources.py --only psych_info,therapie_de

# Add a custom target
poetry run python scripts/recon_sources.py \
    --extra bak=https://www.aekb.de/patient-innen/suche-nach-aerztinnen
```

## Configuration

Override via environment variables (prefix `THERAPIST_FINDER_`) or `.env`:

```
THERAPIST_FINDER_ENABLED_SOURCES=["116117","osm"]
THERAPIST_FINDER_RESIDENTIAL_ONLY_SOURCES=["psych_info","therapie_de"]
THERAPIST_FINDER_SCRAPER_USER_AGENT=my-org-therapist-finder/0.2 (+https://...)
THERAPIST_FINDER_SCRAPER_MIN_DELAY_SECONDS=2.0
THERAPIST_FINDER_OVERPASS_ENDPOINT=https://overpass-api.de/api/interpreter
THERAPIST_FINDER_NOMINATIM_ENDPOINT=https://nominatim.openstreetmap.org/search
THERAPIST_FINDER_HTTP_CACHE_DIR=.cache/therapist-finder
```
