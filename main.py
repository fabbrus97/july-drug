import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
from typing import List, Tuple, Optional, Any

API_KEY = "bada1dd77453448ed659a82b73340311"
BASE_URL = "https://cancer-druginteractions.org/api/v1"

CACHE = {} #fake cache, is not refreshed

HEADERS_CANCER = {
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Accept": "application/json, text/plain, */*",
    "X-Api-Key": API_KEY,
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

def cancer_drugint_checker(drug_a: str, drug_b: str):
    """
    Fetches interaction info between two drugs from cancer-druginteractions.org.
    Searches both /drugs and /comeds endpoints for IDs.
    Returns a dict or None if no match found.
    """

    def fetch_json(endpoint):
        resp = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS_CANCER, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # --- Step 1: fetch both endpoints ---
    try:
        drugs_data = CACHE.get("drugs_data")
        if drugs_data == None:
            drugs_data = fetch_json("drugs")
            CACHE["drugs_data"] = drugs_data
        comeds_data = CACHE.get("comeds_data")
        if comeds_data == None:
            comeds_data = fetch_json("comeds")
            CACHE["comeds_data"] = comeds_data

    except Exception as e:
        print(f"Error fetching drug lists: {e}")
        return None

    # --- Step 2: unified lookup across both datasets ---
    def find_drug_id(name):
        name_lower = name.lower()

        # Search in /drugs
        for drug in drugs_data:
            all_names = [drug.get("name", "").lower()] + [n.lower() for n in drug.get("drug_trade_names", [])]
            if name_lower in all_names:
                return drug["id"]

        # Search in /comeds
        for drug in comeds_data:
            all_names = [drug.get("name", "").lower()] + [n.lower() for n in drug.get("drug_trade_names", [])]
            if name_lower in all_names:
                return drug["id"]

        return None

    id_a = find_drug_id(drug_a)
    id_b = find_drug_id(drug_b)

    if not id_a or not id_b:
        print(f"Could not find IDs for: {drug_a} ({id_a}), {drug_b} ({id_b})")
        return None

    # --- Step 3: fetch interactions ---
    try:
        interactions = CACHE.get("interactions")
        if interactions == None:
            interactions = fetch_json("interactions")
            CACHE["interactions"] = interactions
    except Exception as e:
        print(f"Error fetching interactions: {e}")
        return None

    # --- Step 4: match and return result ---
    for inter in interactions:
        if (
            (inter["primary_drug_id"] == id_a and inter["co_drug_id"] == id_b)
            or (inter["primary_drug_id"] == id_b and inter["co_drug_id"] == id_a)
        ):
            return {
                "source": "Cancer-DrugInteractions.org",
                "interaction_status": inter.get("interaction_status"),
                "evidence_grade": inter.get("evidence_grade"),
                "summary": BeautifulSoup(inter.get("summary", ""), "html.parser").get_text(strip=True),
            }

    return None

def drugscom_interaction_checker(drug_a: str, drug_b: str) -> List[Tuple[Optional[str], Optional[str]]]:
    """
    Looks up the interaction between two drugs using Drugs.com.
    Returns a list of (status_label, paragraph_text) tuples (may be empty).
    """

    BASE_SEARCH_URL = "https://www.drugs.com/api/interaction/search/"
    BASE_INTERACTION_URL = "https://www.drugs.com/interactions-check.php"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0",
        "Accept": "application/json, text/plain, */*",
    }

    def _find_ids_from_search_result(res: Any) -> Optional[Tuple[int, int]]:
        """
        Given the value of the first item in the JSON response, try to extract (ddc_id, brand_name_id).
        Handles both dict and list-shaped responses.
        """
        # Case 1: dict-like ({"ddc_id": ..., "brand_name_id": ...})
        if isinstance(res, dict):
            ddc = res.get("ddc_id")
            brand = res.get("brand_name_id", 0)
            if ddc is not None:
                return int(ddc), int(brand or 0)

        # Case 2: list/tuple like your example: ['FOUND', 'Alfentanil', [...], [{dict-with-ddc}], ...]
        if isinstance(res, (list, tuple)):
            # Look for a nested list whose first element is a dict with 'ddc_id'
            for item in res:
                if isinstance(item, (list, tuple)) and item:
                    first_item = item[0]
                    if isinstance(first_item, dict) and "ddc_id" in first_item:
                        return int(first_item.get("ddc_id")), int(first_item.get("brand_name_id", 0) or 0)
            # As a more defensive fallback, scan any nested dict for ddc_id
            def scan_for_ddc(obj):
                if isinstance(obj, dict):
                    if "ddc_id" in obj:
                        return int(obj.get("ddc_id")), int(obj.get("brand_name_id", 0) or 0)
                    for v in obj.values():
                        found = scan_for_ddc(v)
                        if found:
                            return found
                elif isinstance(obj, (list, tuple)):
                    for v in obj:
                        found = scan_for_ddc(v)
                        if found:
                            return found
                return None
            return scan_for_ddc(res)

        return None

    def lookup_drug(name: str) -> Tuple[int, int]:
        """Return (ddc_id, brand_name_id) or raise ValueError."""
        q = quote_plus(name)
        r = requests.get(f"{BASE_SEARCH_URL}?search={q}", headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            raise ValueError(f"No search results for '{name}'")

        # The API returns a mapping like {"0": ...}. Take the first value.
        first_value = next(iter(data.values()))
        ids = _find_ids_from_search_result(first_value)
        if ids:
            return ids

        # If nothing found, attempt to look through every value in the response
        for val in data.values():
            ids = _find_ids_from_search_result(val)
            if ids:
                return ids

        raise ValueError(f"Could not extract ddc_id/brand_name_id for '{name}' from response")

    # ------------- lookup both drugs -------------
    ddc1, brand1 = lookup_drug(drug_a)
    ddc2, brand2 = lookup_drug(drug_b)

    # ------------- fetch interactions page -------------
    url = f"{BASE_INTERACTION_URL}?drug_list={ddc1}-{brand1},{ddc2}-{brand2}"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    html = resp.text

    # ------------- parse HTML -------------
    soup = BeautifulSoup(html, "html.parser")
    results: List[Tuple[Optional[str], Optional[str]]] = []

    for div in soup.select("div.interactions-reference-wrapper"):
        # print("[DEBUG] div")
        # print(div)
        status_el = div.select_one("span.ddc-status-label")
        # There may be multiple <p> tags; collect their text concatenated or choose the first.
        p_el = div.select_one("p")
        status_text = status_el.get_text(strip=True) if status_el else None
        # print("[DEBUG] status text is", status_text)
        p_text = p_el.get_text(" ", strip=True) if p_el else None
        if status_text or p_text:
            results.append((status_text, p_text))
        break

    return results


if __name__ == "__main__":
    from flask import Flask, request, render_template_string

    app = Flask(__name__)

    # --- HTML templates ---
    form_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Drug Interaction Checker</title>
        <style>
            body { font-family: sans-serif; max-width: 700px; margin: 40px auto; }
            form { display: flex; flex-direction: column; gap: 10px; }
            input { padding: 8px; font-size: 1em; }
            button { padding: 10px; font-size: 1em; cursor: pointer; }
            .result { border: 1px solid #ccc; padding: 15px; border-radius: 10px; margin-top: 20px; }
            .source { font-weight: bold; color: #333; margin-bottom: 5px; }
        </style>
    </head>
    <body>
        <h1>Drug Interaction Checker</h1>
        <form method="post" action="/check">
            <input type="text" name="drug_a" placeholder="First drug name" required>
            <input type="text" name="drug_b" placeholder="Second drug name" required>
            <button type="submit">Check Interactions</button>
        </form>
    </body>
    </html>
    """

    results_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Interaction Results</title>
        <style>
            body { font-family: sans-serif; max-width: 700px; margin: 40px auto; }
            .result { border: 1px solid #ccc; padding: 15px; border-radius: 10px; margin-top: 20px; }
            .source { font-weight: bold; color: #333; margin-bottom: 5px; }
            a { color: #007bff; text-decoration: none; }
            a:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <h1>Results for {{ drug_a }} and {{ drug_b }}</h1>

        {% if results %}
            {% for r in results %}
                <div class="result">
                    <div class="source">{{ r.source }}</div>
                    {% if r.summary %}
                        <p>{{ r.summary }}</p>
                    {% endif %}
                    {% if r.evidence_grade %}
                        <p><strong>Evidence grade:</strong> {{ r.evidence_grade }}</p>
                    {% endif %}
                    {% if r.interaction_status %}
                        <p><strong>Status:</strong> {{ r.interaction_status }}</p>
                    {% endif %}
                </div>
            {% endfor %}
        {% else %}
            <p><em>No interactions were found on either source.</em></p>
        {% endif %}

        <p><a href="/">Check another pair</a></p>
    </body>
    </html>
    """

    @app.route("/", methods=["GET"])
    def index():
        return render_template_string(form_html)

    @app.route("/check", methods=["POST"])
    def check():
        drug_a = request.form.get("drug_a", "").strip()
        drug_b = request.form.get("drug_b", "").strip()

        results = []

        # Call both sources safely
        cancer_result = cancer_drugint_checker(drug_a, drug_b)
        if cancer_result:
            results.append(cancer_result)

        drugscom_results = drugscom_interaction_checker(drug_a, drug_b)
        if drugscom_results:
            for status, text in drugscom_results:
                results.append({
                    "source": "Drugs.com",
                    "interaction_status": status,
                    "summary": text
                })

        return render_template_string(results_html, drug_a=drug_a, drug_b=drug_b, results=results)

    app.run(host="0.0.0.0", port=6907, debug=True)
