import os, re, html, asyncio, datetime, pathlib, json, time
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import requests, feedparser
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Paris")
NOW = datetime.datetime.now(TZ)
ROOT = pathlib.Path(__file__).parent
SITE = ROOT / "site"
SITE.mkdir(exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BriefingMatinal/1.0; +https://github.com/)"}

RSS = {
    "world": [
        ("Le Monde — À la une", "https://www.lemonde.fr/rss/une.xml"),
        ("New Scientist", "https://www.newscientist.com/feed/home/"),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Guardian — World", "https://www.theguardian.com/world/rss"),
    ],
    "nature": [
        ("Nature", "https://www.nature.com/nature.rss"),
        ("Nature News", "https://www.nature.com/subjects/scientific-community/nature.rss"),
    ],
    "chemistry": [("Nature Chemistry", "https://www.nature.com/nchem.rss")],
    "building": [("Building and Environment", "https://rss.sciencedirect.com/publication/science/03601323")],
}
THERMAL_TERMS = ["indoor air", "air quality", "ventilation", "thermal comfort", "overheating",
                 "occupant comfort", "indoor environment", "co2 concentration", "particulate",
                 "volatile organic", "humidity", "heat stress", "building energy"]

def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def fetch_feed(name, url, category):
    try:
        resp = requests.get(url, timeout=20, headers=HEADERS)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        items = []
        for e in parsed.entries[:25]:
            title, link = clean(e.get("title", "")), e.get("link", "")
            summary = clean(e.get("summary", e.get("description", "")))
            # Preserve DOI metadata when publishers expose it in the RSS feed.
            doi = clean(e.get("prism_doi", e.get("doi", ""))).removeprefix("https://doi.org/").removeprefix("http://doi.org/")
            if not doi:
                identifier = clean(e.get("id", ""))
                match = re.search(r"10\.\d{4,9}/[^\s<>]+", identifier, flags=re.I)
                if match:
                    doi = match.group(0).rstrip(".,;)")
            if title and link:
                items.append({"source": name, "title": title, "link": link,
                              "summary": summary[:4000], "abstract": "", "abstract_status": "not_checked",
                              "doi": doi, "date": clean(e.get("published", e.get("updated", ""))),
                              "category": category})
        return items
    except Exception as ex:
        print(f"Feed error {name}: {type(ex).__name__}")
        return []

def unique(items):
    seen, out = set(), []
    for x in items:
        key = x["title"].lower()
        if key not in seen:
            seen.add(key); out.append(x)
    return out

def collect(group):
    all_items = []
    for name, url in RSS[group]:
        all_items.extend(fetch_feed(name, url, group))
    return unique(all_items)

def extract_abstract(page, fallback=""):
    """Extract an abstract from publisher HTML metadata/content; never invent missing text."""
    soup = BeautifulSoup(page, "html.parser")

    # Nature / Springer Nature, Elsevier / ScienceDirect, and common article layouts.
    selectors = [
        "#Abs1-content", "#Abs1", "section#Abs1", ".c-article-section__content",
        ".c-article-body .c-article-section", ".article__body .abstract",
        ".NPara", "#abstracts", "#abstract", "section#abstract",
        ".article-abstract", ".abstractInFull", "#eng-abstract",
        ".hlFld-Abstract", "#preview-section-abstract",
        "section[aria-label='Abstract']", "[data-testid='abstract']",
        ".abstract", ".abstract-content", ".abstractSection",
        ".article-section__abstract", ".article__abstract",
        "section.abstract", "div.abstract"
    ]
    for selector in selectors:
        try:
            for node in soup.select(selector)[:3]:
                # Avoid grabbing an entire article body if a broad selector matches it.
                text = clean(node.get_text(" ", strip=True))
                if len(text) >= 120 and len(text) <= 12000:
                    text = re.sub(r"^(Abstract|Summary|Résumé)\s*[:.]?\s*", "", text, flags=re.I)
                    if len(text) >= 100:
                        return text[:9000], "publisher_page"
        except Exception:
            continue

    # Read metadata independent of capitalization (publishers vary in naming/case).
    wanted = {"citation_abstract", "dc.description", "description", "og:description",
              "twitter:description", "prism:teaser"}
    for meta in soup.find_all("meta"):
        key = (meta.get("name") or meta.get("property") or meta.get("itemprop") or "").strip().lower()
        value = clean(meta.get("content", ""))
        if key in wanted and len(value) >= 120 and not value.lower().startswith(("read more", "sign in")):
            return value[:9000], "page_metadata"

    # JSON-LD may contain abstract or description.
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(node.string or node.get_text())
            stack = data if isinstance(data, list) else [data]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    for key in ("abstract", "description"):
                        val = obj.get(key)
                        if isinstance(val, str) and len(clean(val)) >= 120:
                            return clean(val)[:9000], "structured_metadata"
                    stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
                elif isinstance(obj, list):
                    stack.extend(obj)
        except Exception:
            pass

    return clean(fallback)[:4000], ("rss_fallback" if fallback else "unavailable")


def extract_doi(item, page=""):
    """Find a DOI in RSS metadata, the article URL, or publisher page metadata."""
    doi = (item.get("doi") or "").strip()
    if doi:
        return doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/").rstrip(".,;)")
    candidates = [item.get("link", "")]
    if page:
        soup = BeautifulSoup(page, "html.parser")
        for meta in soup.find_all("meta"):
            key = (meta.get("name") or meta.get("property") or "").strip().lower()
            if key in {"citation_doi", "dc.identifier", "prism.doi"}:
                candidates.insert(0, meta.get("content", ""))
        for link in soup.find_all("a", href=True):
            if "doi.org/" in link["href"].lower():
                candidates.append(link["href"])
    for candidate in candidates:
        match = re.search(r"10\.\d{4,9}/[^\s<>\"'?#]+", html.unescape(candidate or ""), flags=re.I)
        if match:
            return match.group(0).rstrip(".,;)")
    return ""


def crossref_abstract(item, page=""):
    """Try Crossref's public API using a DOI; return empty when no abstract is registered."""
    doi = extract_doi(item, page)
    if not doi:
        return ""
    try:
        url = "https://api.crossref.org/works/" + requests.utils.quote(doi, safe="/")
        r = requests.get(url, timeout=12, headers={**HEADERS, "User-Agent": "BriefingMatinal/1.0"})
        if r.status_code != 200:
            return ""
        work = r.json().get("message", {})
        abstract = clean(work.get("abstract", ""))
        return abstract[:9000] if len(abstract) >= 120 else ""
    except Exception as ex:
        print("Crossref abstract lookup failed:", type(ex).__name__)
        return ""


def openalex_abstract(item, page=""):
    """Rebuild an abstract from OpenAlex's public inverted index when available."""
    doi = extract_doi(item, page)
    if not doi:
        return ""
    try:
        api_url = "https://api.openalex.org/works/https://doi.org/" + requests.utils.quote(doi, safe="/")
        r = requests.get(api_url, timeout=12, headers=HEADERS)
        if r.status_code != 200:
            return ""
        inverted = r.json().get("abstract_inverted_index")
        if not isinstance(inverted, dict) or not inverted:
            return ""
        positions = {}
        for word, indexes in inverted.items():
            for index in indexes:
                positions[index] = word
        abstract = " ".join(positions[i] for i in sorted(positions))
        abstract = clean(abstract)
        return abstract[:9000] if len(abstract) >= 120 else ""
    except Exception as ex:
        print("OpenAlex abstract lookup failed:", type(ex).__name__)
        return ""

def enrich_abstracts(items):
    # Fetch abstracts only for selected scientific papers.
    for item in items:
        if item["category"] not in ("nature", "chemistry", "building"):
            continue
        page = ""
        try:
            response = requests.get(item["link"], timeout=20, headers=HEADERS)
            if response.status_code == 200 and "text/html" in response.headers.get("content-type", ""):
                page = response.text
                abstract, status = extract_abstract(page, item["summary"])
            else:
                abstract, status = clean(item["summary"]), "rss_fallback"
            if status in ("rss_fallback", "unavailable"):
                crossref = crossref_abstract(item, page)
                if crossref:
                    abstract, status = crossref, "crossref"
                else:
                    openalex = openalex_abstract(item, page)
                    if openalex:
                        abstract, status = openalex, "openalex"
            item["abstract"] = abstract
            item["abstract_status"] = status
            print(f"Abstract [{status}]: {item['title'][:100]}")
        except Exception as ex:
            # A publisher may block automated access; continue with RSS and DOI APIs.
            item["abstract"] = clean(item["summary"])
            item["abstract_status"] = "rss_fallback" if item["summary"] else "unavailable"
            print(f"Abstract fetch failed [{item['title'][:80]}]: {type(ex).__name__}")
            if item["abstract_status"] == "rss_fallback":
                for getter, status in ((crossref_abstract, "crossref"), (openalex_abstract, "openalex")):
                    value = getter(item, page)
                    if value:
                        item["abstract"], item["abstract_status"] = value, status
                        break
        time.sleep(0.4)
    return items

def weather():
    """Conditions actuelles et prévisions du jour via Open-Meteo."""
    parts = []
    try:
        params = {
            "latitude": 45.958,
            "longitude": 5.343,
            "timezone": "Europe/Paris",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m",
            "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max,wind_speed_10m_max,sunrise,sunset",
            "forecast_days": 1
        }
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params,
                         timeout=20, headers=HEADERS)
        r.raise_for_status()
        data = r.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        if not current:
            raise ValueError("aucune observation courante renvoyée")

        parts.append(
            "Conditions actuelles (Open-Meteo) : température " +
            str(current.get("temperature_2m", "?")) + "°C, ressenti " +
            str(current.get("apparent_temperature", "?")) + "°C, humidité " +
            str(current.get("relative_humidity_2m", "?")) + "%, vent " +
            str(current.get("wind_speed_10m", "?")) + " km/h."
        )

        def daily_value(key):
            values = daily.get(key, [])
            return values[0] if values else None

        tmin = daily_value("temperature_2m_min")
        tmax = daily_value("temperature_2m_max")
        rain = daily_value("precipitation_probability_max")
        wind = daily_value("wind_speed_10m_max")
        sunrise = daily_value("sunrise")
        sunset = daily_value("sunset")

        forecast = "Prévisions du jour (Open-Meteo)"
        if tmin is not None:
            forecast += " : minimum " + str(tmin) + "°C"
        if tmax is not None:
            forecast += (", maximum " if tmin is not None else " : maximum ") + str(tmax) + "°C"
        if rain is not None:
            forecast += " ; probabilité maximale de précipitations " + str(rain) + "%"
        if wind is not None:
            forecast += " ; vent maximal prévu " + str(wind) + " km/h"
        if sunrise:
            forecast += " ; lever du soleil " + str(sunrise)[-5:]
        if sunset:
            forecast += " ; coucher du soleil " + str(sunset)[-5:]
        parts.append(forecast + ".")
    except Exception as ex:
        print("Open-Meteo weather error:", type(ex).__name__, str(ex)[:180])
        if not parts:
            parts.append("Conditions actuelles et prévisions indisponibles.")
        else:
            parts.append("Prévisions du jour Open-Meteo indisponibles.")

    return "Ambérieu-en-Bugey. " + " ".join(parts) + " Sources : conditions actuelles et prévisions Open-Meteo."


def is_open_access(item):
    """Use OpenAlex metadata to retain only papers marked open access."""
    cache = getattr(is_open_access, "_cache", {})
    key = (item.get("doi") or item.get("link") or item.get("title", "")).lower()
    if key in cache:
        return cache[key]
    try:
        doi = extract_doi(item)
        if doi:
            api_url = "https://api.openalex.org/works/https://doi.org/" + requests.utils.quote(doi, safe="/")
        else:
            query = requests.utils.quote(item.get("title", ""))
            api_url = f"https://api.openalex.org/works?search={query}&per-page=1"
        response = requests.get(api_url, timeout=12, headers=HEADERS)
        if response.status_code != 200:
            cache[key] = False
            is_open_access._cache = cache
            print(f"Open access check unavailable: {item.get('title','')[:80]}")
            return False
        payload = response.json()
        work = payload.get("results", [{}])[0] if isinstance(payload.get("results"), list) else payload
        oa = work.get("open_access", {}) if isinstance(work, dict) else {}
        is_oa = bool(oa.get("is_oa")) or bool(oa.get("oa_url"))
        cache[key] = is_oa
        is_open_access._cache = cache
        print(f"Open access [{is_oa}]: {item.get('title','')[:100]}")
        return is_oa
    except Exception as ex:
        print("Open access check failed:", type(ex).__name__)
        return False


def select_items():
    world_all = collect("world")
    world = []
    for x in world_all:
        if x["source"] not in {y["source"] for y in world}:
            world.append(x)
        if len(world) == 3: break
    for x in world_all:
        if len(world) >= 3: break
        if x not in world: world.append(x)
    nature = [x for x in collect("nature") if is_open_access(x)]
    chemistry = [x for x in collect("chemistry") if is_open_access(x)]
    building = [x for x in collect("building") if is_open_access(x)]
    selected_nature = chemistry[:1]
    for x in nature + chemistry[1:]:
        if len(selected_nature) >= 3: break
        if x["title"].lower() not in {y["title"].lower() for y in selected_nature}:
            selected_nature.append(x)
    ranked = []
    for x in building:
        score = sum(term in (x["title"] + " " + x["summary"]).lower() for term in THERMAL_TERMS)
        ranked.append((score, x))
    ranked.sort(key=lambda z: (z[0], z[1].get("date", "")), reverse=True)
    selected_building = [x for score, x in ranked if score > 0][:3]
    for _, x in ranked:
        if len(selected_building) >= 3: break
        if x not in selected_building: selected_building.append(x)
    print(f"Open-access selection: Nature/Chemistry={len(selected_nature)}, Building and Environment={len(selected_building)}")
    enrich_abstracts(selected_nature + selected_building)
    return world[:3], selected_nature[:3], selected_building[:3]

def gemini_rewrite(sections, weather_text):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key: return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    blocks = []
    for section, entries in sections.items():
        blocks.append(f"\n## {section}\n")
        for i, x in enumerate(entries, 1):
            abstract = x.get("abstract") or x.get("summary") or "(Résumé indisponible)"
            blocks.append(f"{i}. Titre: {x['title']}\nSource: {x['source']}\nLien: {x['link']}\nAbstract (anglais, récupéré ou RSS): {abstract}\n")
    prompt = f"""Prépare un briefing matinal en français, naturel à écouter, daté du {NOW:%d/%m/%Y}.
Météo : {weather_text}
Utilise exclusivement les informations fournies, sans inventer. Pour chaque article scientifique, explique en français le contenu de l'abstract, traduis fidèlement les informations utiles, distingue résultats et limites si l'abstract le permet. Ne cite pas les noms des auteurs. Ne termine pas les présentations par une phrase invitant à consulter la source. Structure : météo, 3 actualités mondiales, 3 articles de Nature dont un Nature Chemistry, 3 articles Building and Environment pertinents pour qualité de l'air intérieur/confort thermique. Environ 900 à 1300 mots maximum.
Données :
{''.join(blocks)}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        r = requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}],
                                     "generationConfig":{"temperature":0.2}}, timeout=90)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as ex:
        print("Gemini error; using abstract fallback:", ex)
        return None

def fallback_script(weather_text, world, nature, building):
    lines = [f"Bonjour. Voici votre briefing du {NOW:%d/%m/%Y}.", weather_text]
    for label, entries in [("Actualités mondiales", world),
                           ("Articles de Nature, dont Nature Chemistry", nature),
                           ("Building and Environment : qualité de l'air et confort thermique", building)]:
        lines.append(label + ".")
        for i, x in enumerate(entries, 1):
            lines.append(f"Sujet {i}. {x['title']}.")
            if x["category"] in ("nature", "chemistry", "building"):
                abstract = x.get("abstract") or x.get("summary") or "Résumé indisponible."
                lines.append("Résumé scientifique disponible en anglais : " + abstract)
            else:
                lines.append(x.get("summary") or "Aucun résumé fourni dans le flux RSS.")
    lines.append("Fin du briefing. Bonne journée.")
    return "\n\n".join(lines)

async def make_audio(text, out):
    import edge_tts
    chunks = [text[i:i+2800] for i in range(0, len(text), 2800)]
    parts = []
    for i, chunk in enumerate(chunks):
        p = SITE / f"part_{i:02d}.mp3"
        await edge_tts.Communicate(chunk, "fr-FR-DeniseNeural", rate="-5%").save(str(p))
        parts.append(p)
    import shutil, subprocess
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and len(parts) > 1:
        listing = SITE / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)],
                       cwd=SITE, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for p in parts: p.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)
    elif len(parts) == 1:
        parts[0].replace(out)
    else:
        # A simple single-file fallback: keep first part as main audio and log the limitation.
        parts[0].replace(out)
        print("Warning: ffmpeg unavailable; only the first audio segment was combined.")

def item_html(x):
    abstract = x.get("abstract") or x.get("summary") or "Résumé indisponible."
    status = x.get("abstract_status", "not_checked")
    labels = {"publisher_page":"Abstract récupéré sur la page de l’éditeur",
              "page_metadata":"Résumé récupéré dans les métadonnées de l’éditeur",
              "structured_metadata":"Résumé récupéré dans les métadonnées structurées",
              "rss_fallback":"Résumé RSS utilisé (abstract complet non trouvé)",
              "unavailable":"Abstract inaccessible",
              "crossref":"Résumé récupéré via Crossref",
              "openalex":"Résumé récupéré via OpenAlex"}
    return f"""<article><h3>{html.escape(x['title'])}</h3>
<p><small>{html.escape(x['source'])} · {html.escape(x.get('date',''))}</small></p>
<p><strong>{html.escape(labels.get(status, 'Résumé disponible'))}</strong></p>
<p>{html.escape(abstract)}</p>
<p><a href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener">Lire la publication originale</a></p></article>"""

def main():
    weather_text = weather()
    world, nature, building = select_items()
    sections = {"Actualités mondiales": world, "Nature (dont Nature Chemistry)": nature,
                "Building and Environment": building}
    script = gemini_rewrite(sections, weather_text) or fallback_script(weather_text, world, nature, building)
    (SITE / "briefing.txt").write_text(script, encoding="utf-8")
    audio_path = SITE / "briefing.mp3"
    try: asyncio.run(make_audio(script, audio_path))
    except Exception as ex: print("Audio generation failed:", ex)
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Briefing matinal</title>
<style>body{{font-family:system-ui,sans-serif;max-width:850px;margin:2rem auto;padding:0 1rem;line-height:1.55}}article{{border-bottom:1px solid #ddd;padding:.7rem 0}}audio{{width:100%}}small{{color:#555}}.weather{{padding:1rem;background:#eef5ff;border-radius:12px}}</style></head><body>
<h1>Briefing matinal — {NOW:%d/%m/%Y}</h1><p>Les résumés scientifiques affichés proviennent des pages des éditeurs lorsqu'ils sont accessibles ; sinon, le résumé RSS est indiqué.</p>
<div class="weather"><h2>Météo</h2><p>{html.escape(weather_text)}</p><small>Données météo : Infoclimat. Consultez les sources originales pour les détails.</small></div>
<h2>Écouter le briefing</h2>{"<audio controls src='briefing.mp3'></audio>" if audio_path.exists() else "<p>Audio indisponible pour cette exécution.</p>"}
<p><a href="briefing.txt">Lire ou télécharger le texte complet</a></p>
<h2>3 actualités mondiales</h2>{''.join(item_html(x) for x in world)}
<h2>3 articles de Nature (dont Nature Chemistry)</h2>{''.join(item_html(x) for x in nature)}
<h2>3 articles de Building and Environment</h2>{''.join(item_html(x) for x in building)}
<footer><p>Prototype personnel. Certains éditeurs peuvent bloquer l'accès automatisé ou ne pas exposer l'abstract publiquement.</p></footer></body></html>"""
    (SITE / "index.html").write_text(page, encoding="utf-8")
    print("Briefing created:", NOW.isoformat())

if __name__ == "__main__":
    main()
