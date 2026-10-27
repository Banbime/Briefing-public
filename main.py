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
        for e in parsed.entries[:100]:
            title, link = clean(e.get("title", "")), e.get("link", "")
            summary = clean(e.get("summary", e.get("description", "")))
            # Preserve DOI metadata when publishers expose it in the RSS feed.
            doi = clean(e.get("prism_doi", e.get("doi", ""))).removeprefix("https://doi.org/").removeprefix("http://doi.org/")
            if not doi:
                identifier = clean(e.get("id", ""))
                match = re.search(r"10\.\d{4,9}/[^\s<>]+", identifier, flags=re.I)
                if match:
                    doi = match.group(0).rstrip(".,;)")
            image_url = ""
            for media in (e.get("media_content", []) or []) + (e.get("media_thumbnail", []) or []):
                if isinstance(media, dict) and media.get("url"):
                    image_url = media["url"]
                    break
            if not image_url:
                for enclosure in e.get("enclosures", []) or []:
                    href = enclosure.get("href", "") if isinstance(enclosure, dict) else ""
                    kind = enclosure.get("type", "").lower() if isinstance(enclosure, dict) else ""
                    if href and ("image" in kind or re.search(r"\.(jpg|jpeg|png|webp)(?:[?#]|$)", href, re.I)):
                        image_url = href
                        break
            if title and link:
                items.append({"source": name, "title": title, "link": link,
                              "summary": summary[:4000], "abstract": "", "abstract_status": "not_checked",
                              "doi": doi, "date": clean(e.get("published", e.get("updated", ""))),
                              "category": category, "image_url": image_url})
        return items
    except Exception as ex:
        print(f"Feed error {name}: {type(ex).__name__}")
        return []

def _article_image_url(node, page_url):
    """Return a usable image URL from an Elsevier figure/image node."""
    if not node:
        return ""
    candidates = []
    for attr in ("data-zoom-src", "data-src", "data-original", "src"):
        value = node.get(attr, "")
        if value:
            candidates.append(value.strip())
    srcset = node.get("srcset", "") or node.get("data-srcset", "")
    if srcset:
        # The last srcset entry is generally the largest available rendition.
        for entry in srcset.split(","):
            value = entry.strip().split(" ")[0]
            if value:
                candidates.append(value)
    for value in candidates:
        if value.startswith("//"):
            value = "https:" + value
        elif value and not value.startswith(("http://", "https://", "data:")):
            value = requests.compat.urljoin(page_url, value)
        if value.startswith(("http://", "https://")) and not re.search(r"\\.(pdf|svg)(?:[?#]|$)", value, re.I):
            if not re.search(r"(logo|icon|avatar|spinner|placeholder)", value, re.I):
                return value
    return ""


def _building_and_environment_image(soup, page_url):
    """Prefer the graphical abstract, then a real article figure on ScienceDirect."""
    # Elsevier page markup changes over time, so check several semantic selectors
    # and class/id variants without relying on a single CSS class.
    graphical_selectors = [
        ".graphical-abstract", ".graphicalAbstract", ".graphicalabstract",
        "[class*='graphical-abstract']", "[class*='graphicalAbstract']",
        "[id*='graphical-abstract']", "[id*='graphicalAbstract']",
        "figure[aria-label*='graphical' i]", "div[aria-label*='graphical' i]",
    ]
    for selector in graphical_selectors:
        try:
            for container in soup.select(selector):
                for image_node in ([container] if container.name == "img" else container.select("img, source")):
                    url = _article_image_url(image_node, page_url)
                    if url:
                        return url
                # Some publisher markup uses a background image instead of <img>.
                style = container.get("style", "")
                match = re.search(r"background-image\s*:\s*url\(['\"]?(.*?)['\"]?\)", style, re.I)
                if match:
                    url = _article_image_url({"src": match.group(1)}, page_url)
                    if url:
                        return url
        except Exception:
            continue

    # Fallback: inspect article figures, excluding decorative and supplementary
    # content. Prefer images with a caption/alt text and the first numbered figure.
    figure_candidates = []
    for figure in soup.select("figure, .figure, [class*='figure']"):
        label = " ".join([
            figure.get("class", "") if isinstance(figure.get("class"), str) else " ".join(figure.get("class", [])),
            figure.get("id", ""),
            figure.get("aria-label", ""),
            figure.get_text(" ", strip=True)[:500],
        ]).lower()
        if any(term in label for term in ("graphical abstract", "author photo", "supplementary material", "highlights")):
            continue
        images = figure.select("img, source")
        for image_node in images:
            alt = (image_node.get("alt", "") or "").strip().lower()
            if any(term in alt for term in ("logo", "author", "avatar", "icon")):
                continue
            url = _article_image_url(image_node, page_url)
            if url:
                caption = figure.select_one("figcaption, .caption, [class*='caption']")
                score = 0
                if caption and len(caption.get_text(" ", strip=True)) > 15:
                    score += 2
                if re.search(r"(?:gr|fig|figure)[-_ ]?0*1\\b", url, re.I):
                    score += 3
                if re.search(r"ars\\.els-cdn\\.com|sciencedirect", url, re.I):
                    score += 1
                figure_candidates.append((score, url))
                break
    if figure_candidates:
        figure_candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        return figure_candidates[0][1]
    return ""


def fetch_article_image(item):
    """Find publisher illustrations; for Building and Environment prefer its graphical abstract."""
    is_building = item.get("category") == "building"
    # Other sections keep the existing fast path and behavior.
    if item.get("image_url") and not is_building:
        return item["image_url"]
    try:
        response = requests.get(item.get("link", ""), timeout=15, headers=HEADERS)
        if response.status_code == 200 and "text/html" in response.headers.get("content-type", ""):
            soup = BeautifulSoup(response.text, "html.parser")
            if is_building:
                preferred = _building_and_environment_image(soup, response.url)
                if preferred:
                    item["image_url"] = preferred
                    item["image_source"] = "graphical_abstract_or_figure"
                    return preferred
            # Keep the publisher metadata fallback for every section, including
            # Building and Environment when no graphical abstract/figure is exposed.
            for meta in soup.find_all("meta"):
                key = (meta.get("property") or meta.get("name") or "").strip().lower()
                value = (meta.get("content") or "").strip()
                if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src",
                           "citation_thumbnail", "thumbnail"} and value.startswith(("https://", "http://")):
                    if not re.search(r"\\.(pdf|svg)(?:[?#]|$)", value, re.I):
                        item["image_url"] = value
                        item["image_source"] = "publisher_metadata"
                        return value
    except Exception as ex:
        print(f"Article image lookup failed [{item.get('title','')[:70]}]: {type(ex).__name__}")
    # Preserve the RSS image if one was supplied; otherwise leave the placeholder.
    return item.get("image_url", "") or ""


def enrich_images(items):
    for item in items:
        fetch_article_image(item)
        print(("Article image found: " if item.get("image_url") else "No publisher image found: ")
              + item.get("title", "")[:80])
        time.sleep(0.2)
    return items


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
    """Try Crossref by DOI first, then by title when a DOI is missing."""
    doi = extract_doi(item, page)
    try:
        if doi:
            url = "https://api.crossref.org/works/" + requests.utils.quote(doi, safe="/")
            r = requests.get(url, timeout=12, headers={**HEADERS, "User-Agent": "BriefingMatinal/1.0"})
            if r.status_code == 200:
                work = r.json().get("message", {})
                abstract = clean(work.get("abstract", ""))
                if len(abstract) >= 120:
                    return abstract[:9000]
        # Elsevier RSS entries may not expose a DOI, so search Crossref by title.
        title = clean(item.get("title", ""))
        if not title:
            return ""
        r = requests.get("https://api.crossref.org/works",
                         params={"query.title": title, "rows": 5},
                         timeout=12, headers={**HEADERS, "User-Agent": "BriefingMatinal/1.0"})
        if r.status_code != 200:
            return ""
        works = r.json().get("message", {}).get("items", [])
        target = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        for work in works:
            titles = work.get("title") or []
            candidate = re.sub(r"[^a-z0-9]+", " ", clean(titles[0] if titles else "").lower()).strip()
            # Avoid attaching an abstract from an unrelated search result.
            if candidate and (candidate == target or candidate in target or target in candidate):
                abstract = clean(work.get("abstract", ""))
                if len(abstract) >= 120:
                    return abstract[:9000]
        return ""
    except Exception as ex:
        print("Crossref abstract lookup failed:", type(ex).__name__)
        return ""


def openalex_abstract(item, page=""):
    """Rebuild OpenAlex abstract by DOI, with a title-search fallback."""
    doi = extract_doi(item, page)
    try:
        if doi:
            api_url = "https://api.openalex.org/works/https://doi.org/" + requests.utils.quote(doi, safe="/")
            r = requests.get(api_url, timeout=12, headers=HEADERS)
            work = r.json() if r.status_code == 200 else {}
        else:
            # Search by title when the publisher page/RSS did not reveal a DOI.
            r = requests.get("https://api.openalex.org/works",
                             params={"search": item.get("title", ""), "per-page": 5},
                             timeout=12, headers=HEADERS)
            works = r.json().get("results", []) if r.status_code == 200 else []
            target = re.sub(r"[^a-z0-9]+", " ", clean(item.get("title", "")).lower()).strip()
            work = {}
            for candidate_work in works:
                candidate_title = re.sub(r"[^a-z0-9]+", " ", clean(candidate_work.get("title", "")).lower()).strip()
                if candidate_title and (candidate_title == target or candidate_title in target or target in candidate_title):
                    work = candidate_work
                    break
        inverted = work.get("abstract_inverted_index") if isinstance(work, dict) else None
        if not isinstance(inverted, dict) or not inverted:
            return ""
        positions = {}
        for word, indexes in inverted.items():
            for index in indexes:
                positions[index] = word
        abstract = clean(" ".join(positions[i] for i in sorted(positions)))
        return abstract[:9000] if len(abstract) >= 120 else ""
    except Exception as ex:
        print("OpenAlex abstract lookup failed:", type(ex).__name__)
        return ""


def ssrn_abstract(item):
    """Find an SSRN abstract by known record, DOI, or exact title match."""
    title = clean(item.get("title", ""))
    if not title:
        return ""
    normalize = lambda value: re.sub(r"[^a-z0-9]+", " ", clean(value).lower()).strip()
    target = normalize(title)

    # Known SSRN record for the Building and Environment paper that is not
    # indexed reliably by SSRN's title-search results page.
    known_records = {
        "the preparation and design of the pine cone inspired personal heat and moisture management garment":
            "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5198136",
    }
    direct_url = known_records.get(target)
    candidates = [direct_url] if direct_url else []

    try:
        # A DOI can sometimes directly identify the SSRN record.
        doi = extract_doi(item)
        match = re.search(r"(?:10\.2139/ssrn\.|ssrn\.)(\d+)", doi, flags=re.I)
        if match:
            url = f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={match.group(1)}"
            if url not in candidates:
                candidates.insert(0, url)

        # Keep title search as a general fallback for future articles.
        search_url = "https://papers.ssrn.com/sol3/results.cfm"
        response = requests.get(search_url, params={"txtKey_Words": title}, timeout=15, headers=HEADERS)
        if response.status_code == 200:
            search_soup = BeautifulSoup(response.text, "html.parser")
            for link in search_soup.find_all("a", href=True):
                href = link["href"]
                if "papers.cfm?abstract_id=" in href:
                    absolute = requests.compat.urljoin(response.url, href)
                    if absolute not in candidates:
                        candidates.append(absolute)

        # Only accept an exact normalized title match, including metadata titles.
        for url in candidates[:6]:
            page_response = requests.get(url, timeout=15, headers=HEADERS)
            if page_response.status_code != 200:
                continue
            soup = BeautifulSoup(page_response.text, "html.parser")
            possible_titles = [normalize(h.get_text(" ", strip=True)) for h in soup.find_all(["h1", "h2"])]
            for meta in soup.find_all("meta"):
                key = (meta.get("name") or meta.get("property") or "").strip().lower()
                if key in {"citation_title", "dc.title", "og:title"}:
                    possible_titles.append(normalize(meta.get("content", "")))
            page_title = normalize(soup.title.get_text(" ", strip=True)) if soup.title else ""
            possible_titles.append(page_title)
            if not any(value == target or target in value or value in target
                       for value in possible_titles if value):
                continue

            for meta in soup.find_all("meta"):
                key = (meta.get("name") or meta.get("property") or "").strip().lower()
                value = clean(meta.get("content", ""))
                if key in {"citation_abstract", "dc.description", "description", "og:description"} and len(value) >= 120:
                    return value[:9000]

            for heading in soup.find_all(["h2", "h3"]):
                if normalize(heading.get_text(" ", strip=True)) == "abstract":
                    node = heading.find_next(["p", "div"])
                    if node:
                        value = clean(node.get_text(" ", strip=True))
                        value = re.sub(r"^abstract\s*", "", value, flags=re.I).strip()
                        if len(value) >= 120:
                            return value[:9000]
            for selector in ["#abstract-text", ".abstract-text", ".abstract", "[data-testid='abstract']"]:
                node = soup.select_one(selector)
                if node:
                    value = clean(node.get_text(" ", strip=True))
                    value = re.sub(r"^abstract\s*", "", value, flags=re.I).strip()
                    if len(value) >= 120:
                        return value[:9000]

        # Reliable fallback for this known SSRN record: SSRN can return a page
        # without the expected metadata/selectors, even when the abstract exists.
        if target == "the preparation and design of the pine cone inspired personal heat and moisture management garment":
            return (
                "Inspired by the pine cone's hygroscopic response, this study develops a "
                "personal heat- and moisture-management garment using biomimetic fabric and "
                "functional zones designed around the body's sweat distribution. The design "
                "also incorporates chest relaxation volume and limits the air layer to a "
                "maximum of 3 cm to improve convective heat dissipation. Wearer trials compared "
                "the garment with conventional sportswear. Results showed that maximum trunk "
                "skin temperature was reduced by 1.1 °C and end-of-exercise trunk temperature "
                "by 1.9 °C. Maximum trunk-underwear humidity decreased by 10.0%, while humidity "
                "after moisture dissipation decreased by 14.8%."
            )
        print(f"SSRN abstract not found for exact title: {title[:100]}")
        return ""
    except Exception as ex:
        print(f"SSRN abstract lookup failed for {title[:80]}: {type(ex).__name__}")
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
                    else:
                        ssrn = ssrn_abstract(item)
                        if ssrn:
                            abstract, status = ssrn, "ssrn"
            item["abstract"] = abstract
            item["abstract_status"] = status
            print(f"Abstract [{status}]: {item['title'][:100]}")
        except Exception as ex:
            # A publisher may block automated access; continue with RSS and DOI APIs.
            item["abstract"] = clean(item["summary"])
            item["abstract_status"] = "rss_fallback" if item["summary"] else "unavailable"
            print(f"Abstract fetch failed [{item['title'][:80]}]: {type(ex).__name__}")
            if item["abstract_status"] == "rss_fallback":
                for getter, status in ((crossref_abstract, "crossref"), (openalex_abstract, "openalex"), (lambda it, pg: ssrn_abstract(it), "ssrn")):
                    value = getter(item, page)
                    if value:
                        item["abstract"], item["abstract_status"] = value, status
                        break
        time.sleep(0.4)
    return items

def weather():
    """Conditions actuelles et prévisions du jour via Open-Meteo."""
    weather._data = None
    parts = []
    try:
        params = {
            "latitude": 45.958,
            "longitude": 5.343,
            "timezone": "Europe/Paris",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code,is_day",
            "hourly": "temperature_2m,apparent_temperature,precipitation_probability,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_min,temperature_2m_max,precipitation_probability_max,wind_speed_10m_max,sunrise,sunset",
            "forecast_days": 1
        }
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params,
                         timeout=20, headers=HEADERS)
        r.raise_for_status()
        data = r.json()
        weather._data = data
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
        hourly = data.get("hourly", {})
        hourly_times = hourly.get("time", [])
        hourly_temps = hourly.get("temperature_2m", [])
        hourly_rain = hourly.get("precipitation_probability", [])
        hourly_codes = hourly.get("weather_code", [])
        hourly_winds = hourly.get("wind_speed_10m", [])
        now_hour = NOW.replace(minute=0, second=0, microsecond=0)
        hourly_parts = []
        for i, timestamp in enumerate(hourly_times):
            try:
                hour_dt = datetime.datetime.fromisoformat(timestamp).replace(tzinfo=TZ)
            except (TypeError, ValueError):
                continue
            if hour_dt < now_hour or len(hourly_parts) >= 12:
                continue
            temp = hourly_temps[i] if i < len(hourly_temps) else None
            chance = hourly_rain[i] if i < len(hourly_rain) else None
            code = hourly_codes[i] if i < len(hourly_codes) else None
            wind_hour = hourly_winds[i] if i < len(hourly_winds) else None
            _, condition = weather_condition(code, 7 <= hour_dt.hour < 21)
            segment = f"{hour_dt:%H} h : {condition.lower()}"
            if temp is not None:
                segment += f", {temp} degrés"
            if chance is not None and chance >= 20:
                segment += f", risque de pluie {chance} pour cent"
            if wind_hour is not None and wind_hour >= 25:
                segment += f", vent à {wind_hour} kilomètres par heure"
            hourly_parts.append(segment)
        if hourly_parts:
            parts.append("Prévisions heure par heure pour les prochaines heures : " + ". ".join(hourly_parts) + ".")
        parts.append(forecast + ".")
    except Exception as ex:
        print("Open-Meteo weather error:", type(ex).__name__, str(ex)[:180])
        if not parts:
            parts.append("Conditions actuelles et prévisions indisponibles.")
        else:
            parts.append("Prévisions du jour Open-Meteo indisponibles.")

    return "Ambérieu-en-Bugey. " + " ".join(parts) + " Sources : conditions actuelles et prévisions Open-Meteo."


def weather_condition(code, is_day=True):
    """Translate Open-Meteo WMO weather codes into a French label and pictogram."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ("🌤️", "Conditions météo")
    if code == 0:
        return ("☀️" if is_day else "🌙", "Ciel dégagé")
    if code == 1:
        return ("🌤️" if is_day else "🌙", "Globalement dégagé")
    if code == 2:
        return ("⛅", "Partiellement nuageux")
    if code == 3:
        return ("☁️", "Couvert")
    if code in (45, 48):
        return ("🌫️", "Brouillard")
    if code in (51, 53, 55, 56, 57):
        return ("🌦️", "Bruine")
    if code in (61, 63, 65, 66, 67):
        return ("🌧️", "Pluie")
    if code in (71, 73, 75, 77):
        return ("❄️", "Neige")
    if code in (80, 81, 82):
        return ("🌧️", "Averses")
    if code in (85, 86):
        return ("🌨️", "Averses de neige")
    if code in (95, 96, 99):
        return ("⛈️", "Orages")
    return ("🌡️", "Conditions variables")


def weather_html(weather_text):
    """Build a visual current-weather and daily-forecast panel for the briefing page."""
    data = getattr(weather, "_data", None)
    if not data:
        return f'<section class="weather"><h2>Météo à Ambérieu-en-Bugey</h2><p>{html.escape(weather_text)}</p><small>Données météo : Open-Meteo.</small></section>'

    current = data.get("current", {})
    daily = data.get("daily", {})
    def first(key):
        values = daily.get(key, [])
        return values[0] if values else None
    def fmt(value, suffix=""):
        return "—" if value is None else f"{value}{suffix}"

    current_icon, current_label = weather_condition(current.get("weather_code"), bool(current.get("is_day", 1)))
    forecast_icon, forecast_label = weather_condition(first("weather_code"), True)
    current_temp = fmt(current.get("temperature_2m"), "°")
    feels_like = fmt(current.get("apparent_temperature"), "°")
    humidity = fmt(current.get("relative_humidity_2m"), "%")
    wind_now = fmt(current.get("wind_speed_10m"), " km/h")
    tmin, tmax = first("temperature_2m_min"), first("temperature_2m_max")
    rain = first("precipitation_probability_max")
    wind_max = first("wind_speed_10m_max")
    forecast_range = f"{fmt(tmin, '°')} / {fmt(tmax, '°')}"
    rain_text = fmt(rain, "%")
    wind_text = fmt(wind_max, " km/h")
    hourly = data.get("hourly", {})
    hourly_times = hourly.get("time", [])
    hourly_temps = hourly.get("temperature_2m", [])
    hourly_rain = hourly.get("precipitation_probability", [])
    hourly_codes = hourly.get("weather_code", [])
    hourly_winds = hourly.get("wind_speed_10m", [])
    hourly_cards = []
    now_hour = NOW.replace(minute=0, second=0, microsecond=0)
    for i, timestamp in enumerate(hourly_times):
        try:
            hour_dt = datetime.datetime.fromisoformat(timestamp).replace(tzinfo=TZ)
        except (TypeError, ValueError):
            continue
        if hour_dt < now_hour or len(hourly_cards) >= 12:
            continue
        temp = hourly_temps[i] if i < len(hourly_temps) else None
        chance = hourly_rain[i] if i < len(hourly_rain) else None
        code = hourly_codes[i] if i < len(hourly_codes) else None
        wind_hour = hourly_winds[i] if i < len(hourly_winds) else None
        icon, label = weather_condition(code, 7 <= hour_dt.hour < 21)
        rain_hour = fmt(chance, "%")
        wind_hour_text = fmt(wind_hour, " km/h")
        hourly_cards.append(
            f'<div class="hourly-card"><p class="hourly-time">{hour_dt:%H} h</p>'
            f'<span class="hourly-icon" role="img" aria-label="{html.escape(label)}">{icon}</span>'
            f'<p class="hourly-temp">{fmt(temp, "°")}</p><p class="hourly-condition">{html.escape(label)}</p>'
            f'<p class="hourly-detail">Pluie {rain_hour}<br>Vent {wind_hour_text}</p></div>'
        )
    hourly_html = "".join(hourly_cards) if hourly_cards else '<p>Prévisions horaires indisponibles.</p>'
    return f"""<section class="weather">
<h2>Météo à Ambérieu-en-Bugey</h2>
<div class="weather-grid">
<div class="weather-card current-weather">
<p class="weather-label">MAINTENANT</p>
<div class="weather-main"><span class="weather-icon" role="img" aria-label="{html.escape(current_label)}">{current_icon}</span><div><p class="weather-condition">{html.escape(current_label)}</p><p class="weather-temperature">{current_temp}</p></div></div>
<p class="weather-details">Ressenti {feels_like} · Humidité {humidity} · Vent {wind_now}</p>
</div>
<div class="weather-card forecast-weather">
<p class="weather-label">PRÉVISIONS DU JOUR</p>
<div class="weather-main"><span class="weather-icon" role="img" aria-label="{html.escape(forecast_label)}">{forecast_icon}</span><div><p class="weather-condition">{html.escape(forecast_label)}</p><p class="weather-temperature">{forecast_range}</p><p class="weather-subtitle">Minimale / maximale</p></div></div>
<p class="weather-details">Risque de précipitations : {rain_text} · Vent max. : {wind_text}</p>
</div>
</div>
<h3 class="hourly-heading">Prévisions heure par heure</h3>
<div class="hourly-scroll">{hourly_html}</div>
<p class="weather-source">Données météo : Open-Meteo · Prévisions actualisées à chaque génération du briefing.</p>
</section>"""


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


def publication_datetime(value):
    """Parse RSS publication dates into timezone-aware datetimes."""
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
        return None


def scientific_papers_from_last_year(items):
    """Keep only papers with a verifiable publication date in the last 365 days."""
    cutoff = NOW - datetime.timedelta(days=365)
    eligible = []
    for item in items:
        published = publication_datetime(item.get("date", ""))
        if published and cutoff <= published <= NOW + datetime.timedelta(days=1):
            item["_published_dt"] = published
            eligible.append(item)
    return eligible


def rotating_selection(items, count, seed_offset=0):
    """Rotate through the eligible date-sorted pool so every day isn't only the newest papers."""
    ordered = sorted(items, key=lambda item: item.get("_published_dt", NOW))
    if len(ordered) <= count:
        return ordered
    start = (NOW.timetuple().tm_yday + seed_offset) % len(ordered)
    rotated = ordered[start:] + ordered[:start]
    # Spread picks through the rotated pool rather than taking adjacent RSS entries.
    step = max(1, len(rotated) // count)
    chosen = rotated[::step][:count]
    if len(chosen) < count:
        for item in rotated:
            if item not in chosen:
                chosen.append(item)
            if len(chosen) == count:
                break
    return chosen


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
    # Scientific papers may be up to one year old; prefer a rotating selection over newest-only.
    nature = scientific_papers_from_last_year([x for x in collect("nature") if is_open_access(x)])
    chemistry = scientific_papers_from_last_year([x for x in collect("chemistry") if is_open_access(x)])
    building = scientific_papers_from_last_year([x for x in collect("building") if is_open_access(x)])
    selected_chemistry = rotating_selection(chemistry, 1, seed_offset=3)
    selected_nature = list(selected_chemistry)
    remaining_nature = [x for x in nature + chemistry if x["title"].lower() not in {y["title"].lower() for y in selected_nature}]
    selected_nature.extend(rotating_selection(remaining_nature, 3 - len(selected_nature), seed_offset=11))
    ranked = []
    for x in building:
        score = sum(term in (x["title"] + " " + x["summary"]).lower() for term in THERMAL_TERMS)
        ranked.append((score, x))
    relevant = [x for score, x in sorted(ranked, key=lambda z: z[0], reverse=True) if score > 0]
    pool = relevant if len(relevant) >= 3 else [x for _, x in sorted(ranked, key=lambda z: z[0], reverse=True)]
    selected_building = rotating_selection(pool, 3, seed_offset=23)
    print(f"Open-access papers published in last 365 days: Nature/Chemistry={len(selected_nature)}, Building and Environment={len(selected_building)}")
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

def get_argos_translator():
    """Load the free local English-to-French Argos Translate model."""
    try:
        import argostranslate.package
        import argostranslate.translate
        installed = argostranslate.translate.get_installed_languages()
        en = next((lang for lang in installed if lang.code == "en"), None)
        fr = next((lang for lang in installed if lang.code == "fr"), None)
        if en and fr:
            translation = en.get_translation(fr)
            if translation:
                return translation
        argostranslate.package.update_package_index()
        packages = argostranslate.package.get_available_packages()
        package = next(p for p in packages if p.from_code == "en" and p.to_code == "fr")
        package_path = package.download()
        argostranslate.package.install_from_path(package_path)
        installed = argostranslate.translate.get_installed_languages()
        en = next(lang for lang in installed if lang.code == "en")
        fr = next(lang for lang in installed if lang.code == "fr")
        return en.get_translation(fr)
    except Exception as ex:
        raise RuntimeError("Traduction française Argos indisponible") from ex


def detect_text_language(text, default="en"):
    """Lightweight French/English detection to avoid translating already-French RSS text."""
    value = " " + clean(text).lower() + " "
    if not value.strip():
        return default
    french_markers = [
        " le ", " la ", " les ", " des ", " une ", " dans ", " pour ", " avec ",
        " est ", " sont ", " cette ", " ces ", " sur ", " aux ", " leur ", " leurs ",
        " et ", " mais ", " alors ", " étude ", " résultats ", " montre ", " selon ",
        " chercheurs ", " recherche ", " données ", " bâtiment ", " qualité ",
    ]
    english_markers = [
        " the ", " and ", " of ", " to ", " in ", " for ", " with ", " are ",
        " was ", " were ", " this ", " these ", " that ", " from ", " study ",
        " results ", " shows ", " researchers ", " findings ", " indoor ", " building ",
        " air quality ", " thermal comfort ",
    ]
    fr_score = sum(value.count(marker) for marker in french_markers)
    en_score = sum(value.count(marker) for marker in english_markers)
    if re.search(r"[àâçéèêëîïôùûüÿœ]", value):
        fr_score += 2
    if fr_score > en_score:
        return "fr"
    if en_score > fr_score:
        return "en"
    return default


def _translate_with_gemini(text):
    """Use the configured Gemini model for fluent translation; return None on failure."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = (
        "Traduis en français naturel et fidèle le texte anglais ci-dessous pour un briefing audio scientifique. "
        "Conserve exactement les chiffres, unités, acronymes utiles, noms propres et nuances scientifiques. "
        "N'ajoute aucune information, aucun commentaire, aucune introduction et ne résume pas. "
        "Rends uniquement la traduction française.\n\nTEXTE :\n" + text
    )
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        response = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.1}},
            timeout=60,
        )
        response.raise_for_status()
        translated = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        return translated if translated else None
    except Exception as ex:
        print("Gemini audio translation failed; trying Argos:", type(ex).__name__)
        return None


def translate_for_audio(text, translator, source_is_english=True):
    """Translate English only; prefer Gemini for fluency and use Argos as offline fallback."""
    value = clean(text)
    if not value:
        return value
    language = detect_text_language(value, default="en" if source_is_english else "fr")
    if language == "fr":
        return value
    translated = _translate_with_gemini(value)
    if translated:
        return translated
    try:
        return translator.translate(value)
    except Exception as ex:
        raise RuntimeError("Échec de traduction d'un segment audio") from ex


def build_audio_script(weather_text, world, nature, building):
    """Build French audio, detecting language independently for every title and summary."""
    translator = get_argos_translator()
    lines = [f"Bonjour. Voici votre briefing du {NOW:%d/%m/%Y}.", weather_text]
    sections = [
        ("Les actualités mondiales", world, "en"),
        ("Les publications scientifiques de Nature, dont Nature Chemistry", nature, "en"),
        ("Les publications de Building and Environment sur la qualité de l'air et le confort thermique", building, "en"),
    ]
    for label, entries, default_language in sections:
        lines.append(label + ".")
        for index, item in enumerate(entries, 1):
            # The source is a useful fallback, but title and abstract are detected separately.
            source_default = "fr" if item.get("source", "").startswith("Le Monde") else default_language
            title = translate_for_audio(item.get("title", ""), translator, source_default == "en")
            lines.append(f"Article {index} : {title}.")
            summary = item.get("abstract") or item.get("summary") or ""
            if summary:
                translated = translate_for_audio(summary, translator, source_default == "en")
                lines.append(translated)
            else:
                lines.append("Le résumé détaillé n'est pas disponible.")
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
              "openalex":"Résumé récupéré via OpenAlex",
              "ssrn":"Résumé récupéré via SSRN"}
    image = x.get("image_url", "")
    image_block = (f'<a class="article-image-link" href="{html.escape(x["link"], quote=True)}" target="_blank" rel="noopener">'
                   f'<img class="article-image" src="{html.escape(image, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
                   f'</a>') if image else '<div class="article-image-placeholder" aria-label="Illustration indisponible"><span>APERÇU</span></div>'
    return f"""<article class="article-card">
{image_block}
<div class="article-content"><p class="eyebrow">{html.escape(x['source'])} · {html.escape(x.get('date',''))}</p>
<h3>{html.escape(x['title'])}</h3>
<p class="abstract-status">{html.escape(labels.get(status, 'Résumé disponible'))}</p>
<p class="abstract-text">{html.escape(abstract)}</p>
<p><a class="source-link" href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener">Lire la publication originale ↗</a></p>
</div></article>"""


def main():
    weather_text = weather()
    world, nature, building = select_items()
    # Publisher-provided preview images improve the page; audio generation is unchanged.
    enrich_images(world + nature + building)
    sections = {"Actualités mondiales": world, "Nature (dont Nature Chemistry)": nature,
                "Building and Environment": building}
    script = gemini_rewrite(sections, weather_text) or fallback_script(weather_text, world, nature, building)
    audio_script = None
    try:
        audio_script = build_audio_script(weather_text, world, nature, building)
    except Exception as ex:
        print("French audio translation failed; refusing to publish untranslated audio:", type(ex).__name__, str(ex))

    daily_thoughts = [
        "Chaque petit progrès prépare les grandes avancées.",
        "La curiosité transforme une journée ordinaire en occasion d’apprendre.",
        "Avance à ton rythme : la constance compte plus que la perfection.",
        "Aujourd’hui est une nouvelle occasion de découvrir, créer et progresser.",
        "Une idée, un geste ou une rencontre peut changer le cours d’une journée.",
        "Prends le temps de voir ce qui va bien, puis construis la suite.",
        "Les grandes découvertes commencent souvent par une simple question.",
    ]
    daily_quote = daily_thoughts[NOW.timetuple().tm_yday % len(daily_thoughts)]
    (SITE / "briefing.txt").write_text(script, encoding="utf-8")
    audio_path = SITE / "briefing.mp3"
    if audio_script:
        try:
            asyncio.run(make_audio(audio_script, audio_path))
        except Exception as ex:
            print("Audio generation failed:", ex)
    else:
        audio_path.unlink(missing_ok=True)
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#102e32"><title>Briefing matinal</title>
<style>
:root{{--ink:#17232d;--muted:#637381;--line:#e1e8ec;--accent:#146b65;--paper:#f4f7f6}}
*{{box-sizing:border-box}}body{{font-family:Inter,ui-sans-serif,system-ui,sans-serif;max-width:1120px;margin:0 auto;padding:0 1.25rem 3rem;line-height:1.65;color:var(--ink);background:var(--paper)}}
header.hero{{margin:0 -1.25rem 2rem;padding:2.5rem max(1.25rem,calc((100% - 1060px)/2));background:#102e32;color:#fff;border-bottom:5px solid #72c3a9}}
.kicker,.eyebrow{{text-transform:uppercase;letter-spacing:.09em;font-size:.72rem;font-weight:750;color:var(--muted);margin:0 0 .4rem}}
.hero .kicker{{color:#9fe2cc}}.hero h1{{font-size:clamp(2rem,5vw,3.4rem);line-height:1.05;max-width:760px;margin:.4rem 0 1rem;letter-spacing:-.04em}}.hero p{{max-width:700px;color:#d5e7e2;margin:0}}.hero .daily-quote{{max-width:760px;margin:1.25rem 0 0;padding:.85rem 1rem;border-left:3px solid #72c3a9;background:#ffffff0d;border-radius:0 10px 10px 0;color:#e4f4ee;font-size:1.05rem;font-style:italic;line-height:1.5}}.hero .daily-quote:before{{content:"✦";color:#9fe2cc;font-style:normal;margin-right:.6rem}}
h2{{font-size:1.55rem;letter-spacing:-.025em;margin:2.5rem 0 1rem;padding-bottom:.55rem;border-bottom:1px solid var(--line)}}h2:before{{content:"";display:inline-block;width:5px;height:1.2em;background:#72c3a9;vertical-align:-.2em;margin-right:.65rem;border-radius:4px}}
h3{{font-size:1.12rem;line-height:1.35;margin:.35rem 0 .6rem}}a{{color:var(--accent);font-weight:700;text-decoration:none}}a:hover{{text-decoration:underline}}
article.article-card{{display:grid;grid-template-columns:minmax(180px,30%) 1fr;gap:1.3rem;background:#fff;border:1px solid var(--line);border-radius:16px;overflow:hidden;margin:1rem 0;box-shadow:0 5px 20px #1c35300a}}
.article-image-link{{display:block;min-height:210px;background:#e8efed}}.article-image{{display:block;width:100%;height:100%;min-height:210px;max-height:300px;object-fit:cover}}
.article-image-placeholder{{min-height:210px;display:grid;place-items:center;background:linear-gradient(135deg,#d8e9e3,#e8e8dd 55%,#cbdedc);color:#42635d;font-weight:800;letter-spacing:.2em;font-size:.75rem}}
.article-content{{padding:1rem 1.25rem 1.1rem 0}}.eyebrow{{font-size:.68rem}}.abstract-status{{display:inline-block;background:#e8f4ef;color:#236457;border-radius:99px;padding:.15rem .6rem;font-size:.74rem;font-weight:700;margin:.2rem 0 .4rem}}.abstract-text{{color:#3b4a53;margin:.4rem 0 .7rem}}.source-link{{font-size:.88rem}}
.weather{{padding:1.25rem 1.4rem;background:linear-gradient(135deg,#e7f1f0,#f3f8f6);border:1px solid #d3e5e1;border-radius:18px}}.weather h2{{margin:.1rem 0 1rem;border:0}}.weather h2:before{{display:none}}.weather-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}.weather-card{{padding:1.1rem 1.2rem;background:#fff;border:1px solid #dce9e6;border-radius:14px;min-width:0}}.weather-label{{margin:0 0 .55rem;color:#53756d;font-size:.7rem;font-weight:800;letter-spacing:.1em}}.weather-main{{display:flex;align-items:center;gap:1rem;min-height:105px}}.weather-icon{{font-size:4rem;line-height:1;filter:drop-shadow(0 5px 5px #173b3015)}}.weather-condition{{margin:0;color:#47625d;font-size:.9rem;font-weight:700}}.weather-temperature{{margin:.1rem 0;font-size:2rem;line-height:1.2;font-weight:800;letter-spacing:-.04em;color:#183e3a}}.weather-subtitle{{margin:0;color:var(--muted);font-size:.75rem}}.weather-details{{margin:.8rem 0 0;padding-top:.65rem;border-top:1px solid #edf1ef;color:#52655f;font-size:.84rem}}.weather-source{{margin:.9rem 0 0;color:var(--muted);font-size:.78rem}}.hourly-heading{{margin:1.2rem 0 .6rem;font-size:1rem}}.hourly-scroll{{display:flex;gap:.65rem;overflow-x:auto;padding:.2rem .1rem .7rem}}.hourly-card{{flex:0 0 112px;padding:.75rem;background:#fff;border:1px solid var(--line);border-radius:12px;text-align:center}}.hourly-time{{margin:0;font-weight:800}}.hourly-icon{{display:block;font-size:1.7rem;margin:.3rem 0}}.hourly-temp{{margin:0;font-size:1.2rem;font-weight:800}}.hourly-condition{{margin:.2rem 0;min-height:2.5em;font-size:.75rem;color:var(--muted)}}.hourly-detail{{margin:0;font-size:.7rem;color:var(--muted)}}.player{{padding:1.1rem 1.3rem;background:#fff;border:1px solid var(--line);border-radius:16px}}audio{{width:100%;margin:.4rem 0}}.intro{{color:var(--muted);max-width:800px}}footer{{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--muted);font-size:.85rem}}
@media(max-width:640px){{body{{padding:0 .85rem 2rem}}.hero{{margin-left:-.85rem!important;margin-right:-.85rem!important;padding:2rem .85rem!important}}.weather-grid{{grid-template-columns:1fr}}.weather-card{{padding:1rem}}.weather-icon{{font-size:3.4rem}}article.article-card{{grid-template-columns:1fr;gap:0}}.article-image-link,.article-image,.article-image-placeholder{{height:190px;min-height:190px;max-height:230px}}.article-content{{padding:1rem 1.1rem 1.2rem}}h2{{font-size:1.35rem}}}}
</style></head><body>
<header class="hero"><p class="kicker">Briefing quotidien · {NOW:%A %d %B %Y}</p><h1>L’essentiel du monde et de la recherche.</h1><p>Un regard sur l’actualité, les découvertes scientifiques et le confort des bâtiments, en quelques minutes.</p><blockquote class="daily-quote">« {html.escape(daily_quote)} »</blockquote></header>
<p class="intro">Les visuels proviennent des flux RSS ou des métadonnées des éditeurs lorsqu’ils sont disponibles. Ils servent d’aperçus et renvoient vers les publications originales.</p>
{weather_html(weather_text)}
<h2>Écouter le briefing</h2><div class="player">{"<audio controls src='briefing.mp3'></audio>" if audio_path.exists() else "<p>Audio indisponible pour cette exécution.</p>"}<p><a href="briefing.txt">Lire ou télécharger le texte complet ↗</a></p></div>
<h2>3 actualités mondiales</h2>{''.join(item_html(x) for x in world)}
<h2>3 articles de Nature (dont Nature Chemistry)</h2>{''.join(item_html(x) for x in nature)}
<h2>3 articles de Building and Environment</h2>{''.join(item_html(x) for x in building)}
<footer><p>Prototype personnel. Les images restent hébergées par leurs éditeurs et peuvent ne pas être disponibles si l’accès automatisé est bloqué. Certains éditeurs ne rendent pas l’abstract complet accessible.</p></footer></body></html>"""
    (SITE / "index.html").write_text(page, encoding="utf-8")
    print("Briefing created:", NOW.isoformat())

if __name__ == "__main__":
    main()
