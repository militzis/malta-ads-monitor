# -*- coding: utf-8 -*-
"""
auto_block_nonpolitical.py
Automatically blocks non-political pages from the YES pool in politician_ads.db.
Updates page_blocklist.json and sets election_related='NO' in the DB.
"""

import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = "politician_ads.db"
BLOCKLIST_PATH = "page_blocklist.json"

# ── 1. EXPLICIT BLOCK: page IDs we are 100% sure are non-political ──────────
EXPLICIT_BLOCK = {
    # NovelTime (entertainment apps)
    "1046513341869386": "NovelTime Plus - Midnight Tales (entertainment app)",
    "814703498393241": "NovelTime-Endless Stories (entertainment app)",
    # Media / magazines
    "134072880051806": "Madame Figaro Cyprus (fashion magazine)",
    "107868663961031": "Kypros Daily (news media)",
    "120779801608022": "Lady Times (media)",
    "103536132597758": "Nikos Georgiou - Ειδήσεις (news media)",
    "169696516233452": "Ο Νέος Ελληνικός Κόσμος (media)",
    "107832368838210": "More.com Cyprus (media)",
    "851925431335780": "Larnaka Talks (media)",
    "316361765093900": "IMH (media)",
    "571235809599203": "Tothemaonline.com (news site)",
    "106635818709351": "Cytavision (TV channel)",
    "1627399754011001": "Rythmos (radio)",
    # Real estate
    "755904577824713": "Stavrinos-estates Cyprus (real estate)",
    "1204890732902068": "Inprohome (real estate)",
    "408361862371089": "Propxp (real estate)",
    # Municipalities
    "317812228341573": "Agia Napa Municipality",
    "541443389208831": "Δήμος Λευκωσίας - Nicosia Municipality",
    "1527209187569087": "Δήμος Ιεροκηπίας - Ierokipia Municipality",
    "1881374465458896": "Δήμος Πόλεως Χρυσοχούς",
    "1523797697855594": "Δήμος Λεμεσού - Limassol Municipality",
    "293463737174264": "Καταφύγιο Αδέσποτων Σκύλων Δήμου Αθηένου (municipality dog shelter)",
    "518933804625799": "Κοινοτικό Συμβούλιο Αμιάντου (community council)",
    # Theatres / arts / culture (non-political)
    "488409351030263": "Θέατρο Λέξη (theatre)",
    "101986615814870": "Θέατρο Δέντρο (theatre)",
    "481691152407153": "Καλλιτεχνικό Εργαστήρι Άννα Λοΐζου (arts workshop)",
    "194507507087137": "Θεατρικό Εργαστήρι Μόνικα Μελέκη (theatre)",
    "276271355558726": "Θεατρικό Εργαστήρι Σκεύη Ανδρέου (theatre)",
    "456516920879117": "Θεατρικές παραγωγές Φοίνικας (theatre)",
    "298477251124": "θέατρο ένα (theatre)",
    "102118112287843": "Θ.Σ.Κ Θεατρική Στέγη Κύπρου (theatre)",
    "1514721782121276": "Θεατρική Ομάδα Persona (theatre)",
    "151762301831597": "Κινηματογραφική Λέσχη Λεμεσού (film club)",
    "668156959707779": "ΆρτιCulture (cultural org)",
    "819385317935517": "Όλβιον - Κέντρο Τέχνης και Βιωματικής Μάθησης (arts centre)",
    "1807705789349202": "Πολιτιστικός Όμιλος Ρήγαινα Περβολιών (cultural association)",
    "100462725071708": "Πολιτιστική ΟΜΑΔΑ - Η Παράσταση (cultural group)",
    "878149552045636": "Atheras Peoples & Cultures (cultural)",
    "775493668975923": "Κυπρίων Θέασις (theatre/cultural)",
    "345421661998051": "Drama Epic (entertainment)",
    "1302514716558850": "Κίνηση Πολιτισμού (cultural movement)",
    "113194043394062": "Όμιλος Λογοτεχνίας και Κριτικής (literary club)",
    # Religious
    "105685098224776": "Αγιος Εφραίμ - Οι φίλοι του Αγίου (religious)",
    "109889194067986": "Ιερός Ναός Αγίου Εφραίμ - Τάλα (church)",
    "113087165158893": "Παρεκκλήσιο Αγίου Εφραίμ (chapel)",
    "278750125822135": "Παναγία Χρυσοσπηλιώτισσα Κάτω Δευτερά (church)",
    "1818385411600305": "Η Του Σωτήρος Χριστού Ανάστασις (religious)",
    "106378234428185": "Ιερός Ναός Παναγίας Χρυσολοφίτισσας Λόφου (church)",
    "937555192976330": "ΥΣΕΕ Κύπρου - Cyprus Hellenic Ethnic Religion (religious org)",
    "168844297130226": "Κύκλος Φίλων της Μαρίας Βαλτόρτα (religious)",
    # Restaurants / food / cafes / bars
    "1744582972279421": "Gastronomy Essentials (restaurant supplies)",
    "969976126519209": "Καφενείον ΕΡΜΗΣ (café)",
    "145865705286656": "Λοζάν Μουσικό Καφενείο-Ουζερί (music café)",
    "1487243888238097": "Εν Τέχνο Καφενείο (café)",
    "100197369125055": "Μεζεδοπωλείο Καραντίνα (restaurant)",
    "158400907362605": "Τατσιά Μεζεδοτεχνείο (restaurant)",
    "101027968384827": "Palaia Elia (restaurant)",
    "151715931652584": "To Edesma tou Paradeisou (food)",
    "1158842187616378": "Κοσμικό Κέντρον Αντωνάκη - Antonakis Music Hall (entertainment venue)",
    "2068115076819659": "Sta Ouza Mas (restaurant)",
    "506504939221888": "Η φρουταρία του Κυριάκου (fruit shop)",
    # Medical / pharma / health
    "465987706758805": "Όμιλος Βιοιατρική - medical labs (medical)",
    "1171523046211155": "Χάρης Αντωνίου - Σχολικός Ψυχολόγ (psychologist)",
    "103727875419517": "Σύνδεσμος Φίλων Κέντρου Προληπτικής Παιδιατρικής (medical association)",
    "435681582955405": "Pharmamind (pharmacy)",
    "517956508078873": "Φαρμακείο Αθηνά Μιχαήλ (pharmacy)",
    "168771869643204": "The 1:1 Diet with Tasos Armostis (diet programme)",
    # Sports clubs
    "648808405185986": "Nautical Club Larnaca (sports club)",
    "809839269089281": "Απόλλων Λεμεσού (football club)",
    "107272228707847": "ΠΑΕΕΚ Κερύνειας (sports club)",
    # Beauty / cosmetics / fashion
    "1663531387291671": "Royal Cosmetics (cosmetics)",
    "345716732163577": "Ilovestyle.com (fashion/lifestyle)",
    "103108665243595": "Maria Filippou MUA (makeup artist)",
    # Tourism / travel / entertainment
    "1535263790045558": "Moyses The Expedition (tourism/adventure)",
    "218521148159647": "MyTroodos.com (tourism portal)",
    "720255761179470": "Smart City Guide (tourism)",
    "106413211646131": "Giraffe Experience (entertainment)",
    # Education / science
    "1703110099905839": "House Of Science - Σπίτι Της Επιστήμης (science centre)",
    "206840993213862": "Τhe Study Room - Ιδιωτικό Φροντιστήριο (tutoring)",
    "127769223746728": "Εθνικό Κέντρο Συντονισμού Κυβερνοασφάλειας (cybersecurity agency)",
    "359990714123068": "Οργανισμός Νεολαίας Κύπρου (government youth board)",
    "195769260498973": "University of Cyprus Alumni (alumni association)",
    "887087134693878": "Κέντρο Παραγωγικότητας Κύπρου (productivity centre)",
    # Business / commercial
    "1469358346619042": "Opto Hellas (optician)",
    "720152937842367": "Lumina Mare (business)",
    "146661542120037": "Γαλακτοβιομηχανία Αχναγάλ (dairy industry)",
    "107551280980367": "iMinder (app/business)",
    "1713763408851895": "Uniplex (business)",
    "242676639198043": "Yiotis Christou Ltd (company)",
    "779316438605435": "grigoriou.signs (signage business)",
    "100711365886404": "Συνεργείο Καθαρισμών Αντώνης (cleaning service)",
    "1512760138992754": "Gavriel Crop Nutrition (agriculture business)",
    "813920905144708": "Women4Cyber Cyprus (cybersecurity org)",
    "646627969136950": "Allwyn CY / ΟΠΑΠ Κύπρου (lottery)",
    "100272582471117": "Growth Hacking Cyprus (marketing)",
    "110723141842171": "andreas.georgiou_p.t (personal trainer)",
    "837271639460233": "Kalliphono by Maria Tsangari (music school)",
    # Children / parenting
    "310934165676880": "Sophia For Children (children's products)",
    "791189724306414": "MommyCool (parenting)",
    # Events / tickets
    "476331789048473": "SoldOut Tickets (ticketing)",
    "369974223058094": "Ticketmaster Cyprus (ticketing)",
    "272357732791888": "chryso events (events)",
    # Funeral
    "516311585391469": "Πένθιμα Γεγονότα (funeral services)",
    # Wedding / celebration
    "459679964836128": "Ονειροσταλίδα Μπομπονιέρες Γάμου (wedding products)",
    # Music groups (non-political)
    "100748132606333": "Μουσικό Σχήμα Εξάρχοντες (music group)",
    "638162749916490": "Χορωδία Ανεμόεσσα Μητροπολιτικού Ναού (choir)",
    "1591238851158398": "Sol Music Productions (music production)",
    # Greek-related non-Cyprus-political
    "109706817338385": "MoveD.gr (Greek movement)",
    # Lifestyle / misc
    "112955403863189": "Μελάνι σε χαρτί (literary/cultural)",
    "103291789005815": "Μικρή Άρκτος (children's)",
    "421875658305885": "MagicTouch (entertainment/events)",
    "313200798730582": "MyLife (lifestyle)",
    "1788557328061678": "Παρατηρητήριο Τρίτης Ηλικίας Κύπρου (elderly org)",
    "375114525688377": "En Platron (venue/bar)",
    "344240979064421": "Beesbuzz (business)",
    "114836607309522": "Amber n' dust (fashion/lifestyle)",
    "102718322474108": "whitetailed_designs (design studio)",
    "126978790504940": "dig_stage_production (production)",
    "506897502718680": "Urban Gorillas (entertainment)",
    "365495019982538": "Symfiliosi (non-political)",
    "368838679652717": "Fever Club (nightclub/entertainment)",
    "508113585888098": "Rotaract Larnaca Kition (service club)",
    "108299387243118": "Splash It (leisure)",
    "489092084283481": "Andri Eleftheriou Coach (life coach)",
    "333328670211236": "Εθνικόφρονα Σωματεία Απόλλων Λυμπιών (sports/patriotic association)",
    # Misc non-political foreign names
    "106517852328126": "Andreea Popescu (non-Cyprus politician)",
    "105981989049575": "Milica Jovanović (non-Cyprus politician)",
    "115036534800491": "Kovács Eszter (non-Cyprus politician)",
    "101678346029297": "Dr Piotr Zieliński (non-Cyprus politician)",
    "123673594152742": "Dott. Marco Bianchi (non-Cyprus politician)",
    # Consumer org
    "484951294976883": "Κυπριακός Σύνδεσμος Καταναλωτών (consumer association)",
    # Other clearly non-political
    "127586543929133": "George Marinakis / Stillness in Motion (photographer)",
    "707444845794849": "Κωνσταντίνος ο Κουροπαλάτης (historical figure page)",
    "740174869184809": "ΡΗΓΑΣ Φεραίος (historical figure page)",
    "526317610571634": "Ο Πράκτορας που Πρόδωσε τον Μακάριο (book/historical page)",
    "174218825772036": "Ελληνικό Σύμπαν (Greek universe - non-political)",
    "189439494747782": "Παγκύπριος Σύνδεσμος Δημοκρατικών Αντιστασιακών (veterans association)",
    "1846449602296345": "Χαμογέλα μου / Smile to me (lifestyle)",
    "771295392722899": "Άμα με βρεις να μ' αγαπάς (entertainment/book)",
    "772262425964722": "Luigi Nicolas (non-political individual)",
    "586338854572785": "Yliana Memerfop (non-political)",
    "985924164613457": "dromos.elpidas (non-political)",
}

# ── 2. KEEP: page IDs that must NOT be blocked ───────────────────────────────
KEEP_PAGE_IDS = {
    # Trade unions
    "548288891914230",   # Παγκύπρια Εργατική Ομοσπονδία ΠΕΟ
    "371329889611505",   # Πασυκαφ / Pasykaf
    # Party youth wings / women's movements
    "173342948271",      # Νεολαία Δημοκρατικού Συναγερμού (ΝΕΔΗΣΥ)
    "1489556681297997",  # Γυναικείο Κίνημα ΠΟΓΟ
    "103553984976781",   # ΠΟΓΟ Λευκωσίας - Κερύνειας
    "1673768649503027",  # ΠΟΓΟ Λάρνακας
    "470203346169817",   # Νεολαία Λύσης - Lysi Youth
    # Political parties
    "117799504920552",   # ΑΚΕΛ - AKEL
    "314797935039548",   # ΑΚΕΛ Περβολιών Λάρνακας
    "1375073242810135",  # ΑΚΕΛ Αμμοχώστου
    "132722277222083",   # ΕΔΟΝ Πάφου - EDON Pafou
    "341926215672399",   # Νέα Δύναμη
    "640305702496775",   # Δημοκρατική Αλλαγή
    "190821917443357",   # Κομμουνιστική Πρωτοβουλία Κύπρου
    "310481255471944",   # ΔΕΚ - Δημοκρατικό Εθνικό Κίνημα
    "101901127944882",   # ΔΗΑΝΑ
    # Confirmed politicians / municipal councillors
    "574683626048556",   # Φελλάς Μιχάλης Δημοτικός Σύμβουλος Λεμεσού
    "754699914874253",   # Φελλάς Μιχάλης (second page)
    "756115551158238",   # Αντρέας Νικολάου Κόκκινος Δημοτικός Σύμβουλος Λεμεσού
    "1030548216968977",  # ΑυτοδιοίκησηCY (local governance - political content)
    "107513542170251",   # Δήμος Γεωργιάδης (politician - not municipality)
    "103469391602268",   # Δήμος Γεωργιάδης - Demos Georgiades (politician)
    # Instagram-only seed records
    "6703296155",        # Φράγκου Αντωνία (Instagram)
    "597806658",         # Στυλιανού Μάριος (Instagram)
    "2853875253",        # Φούλη Βερόνικα (Instagram)
    # Candidate pages with explicit election keywords in name
    "1038630016001930",  # Αλέκος Αργυρού - Υποψήφιος Βουλευτής Άλμα
    "1039357462587671",  # Δρ Ανδρέας Χειμωνίδης - Υποψήφιος Βουλευτής Λεμεσού
    "819253351280771",   # Αντώνης Παράσχου Υποψήφιος Βουλευτής Λεμεσού
    "1017553231437957",  # antoniafrangou2026
    "564878266710127",   # kourtoulos2026
    "865790916625202",   # Έλληνας Σάββας
    "111658824514531",   # Χρωματί-ΖΩ την ΕΛΠΙΔΑ ΚΥΡΙΑΚΟΣ ΚΟΥΔΟΥΝΑΣ
}

# ── 3. KEYWORD-BASED BLOCKING (applied to page_name, case-insensitive) ───────
# If ANY keyword matches the page name, block it (unless in KEEP)
BLOCK_KEYWORDS = [
    # Theatres
    "θέατρο", "θεατρ", "θεατρικ",
    # Religious
    "ιερός ναός", "ιερος ναος", "παναγία", "παναγια", "παρεκκλήσιο", "παρεκκλησιο",
    "αγίου", "αγιου", "αγίας", "αγιας", "εκκλησ", "εκκλησία", "εκκλησια",
    "χριστού", "χριστου", "μητροπολ",
    # Restaurants / taverns / food
    "ταβέρνα", "ταβερνα", "μεζεδ", "καφενείο", "καφενειο", "ουζερί", "ουζερι",
    "εστιατόρ", "εστιατορ", "restaurant", "tavern",
    # Municipalities (block remaining ones)
    "municipality", "δήμος ", "δημος ", "κοινοτικ",
    # Real estate
    "estates", "real estate", "properties", "realty",
    # Sports clubs (not party-affiliated)
    "football club", "ποδοσφαιρ", "αθλητικ", "γυμναστήριο", "γυμναστηριο",
    # Medical
    "φαρμακείο", "φαρμακειο", "ιατρ", "κλινικ", "νοσοκομ",
    # Funeral
    "πένθιμα", "πενθιμα", "κηδεία", "κηδεια",
    # Cleaning / technical services
    "καθαρισμ", "συνεργείο", "συνεργειο",
    # Entertainment / nightlife
    "nightclub", "night club",
    # Tickets / events (commercial)
    "ticketmaster", "soldout tickets",
    # Lottery
    "lottery", "λαχείο", "λαχειο",
    # Personal trainer
    "personal trainer", "p.t",
    # Music production (non-political)
    "μουσικές παραγωγές", "μουσικες παραγωγες",
    # Choir
    "χορωδία", "χορωδια",
    # Cosmetics / beauty
    "cosmetics", "beauty", "μακιγιάζ", "μακιγιαζ", "makeup artist", "mua",
    # Tutoring / study
    "φροντιστήριο", "φροντιστηριο",
    # Signage / design (commercial)
    "signs", "signage",
    # Wedding
    "μπομπονιέρ", "μπομπονιερ", "γάμου", "γαμου", "βάπτιση", "βαπτιση",
    # Diet / nutrition (commercial)
    "diet", "nutrition",
]

# ── 4. KEEP KEYWORDS (protect pages whose name contains these) ────────────────
KEEP_KEYWORDS = [
    "βουλευτ",       # βουλευτής / υποψήφιος βουλευτής
    "υποψήφι",       # υποψήφιος
    "υποψηφι",
    "πογο",          # ΠΟΓΟ party
    "νεδησυ",        # NEDISY
    "nedisy",
    "εδον",          # EDON
    "edon",
    "ακελ",          # AKEL
    "akel",
    "δηκο",          # DIKO
    "diko",
    "δησυ",          # DISY
    "disy",
    "δηπα",          # DIPA
    "dipa",
    "νεα δυναμη",    # party
    "κομμουνιστ",    # communist party
    "δημοκρατικ αλλαγ",  # political party
    "δημοκρατικό εθνικό κίνημα",
    "πολιτικ",       # political
    "γυναικείο κίνημα",  # women's movement
    "γυναικειο κινημα",
    "νεολαία",       # youth wing
    "νεολαια",
    "δημοτικός σύμβουλ",  # municipal councillor (political role)
    "δημοτικος συμβουλ",
    "2026",          # election year marker in name
    "ΠΕΟ",           # trade union
    "πεο",
    "pasykaf",
    "πασυκαφ",
    "αυτοδιοίκηση",  # local governance (political)
    "αυτοδιοικηση",
]


def main():
    # Load blocklist
    with open(BLOCKLIST_PATH, encoding="utf-8") as f:
        bl = json.load(f)
    existing_blocked = set(bl["pages"].keys())

    # Connect to DB
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Get all YES pages not already blocked
    rows = cur.execute("""
        SELECT page_id, page_name, COUNT(*) as cnt
        FROM politician_ads
        WHERE election_related='YES'
        GROUP BY page_id, page_name
        ORDER BY cnt DESC
    """).fetchall()

    to_block = {}   # page_id -> reason
    kept = []
    already_blocked = []

    for page_id, page_name, cnt in rows:
        pname_lower = (page_name or "").lower()

        if page_id in existing_blocked:
            already_blocked.append((page_id, page_name, cnt))
            continue

        if page_id in KEEP_PAGE_IDS:
            kept.append((page_id, page_name, cnt, "KEEP_EXACT"))
            continue

        # Check KEEP keywords first (they override BLOCK keywords)
        keep_hit = None
        for kw in KEEP_KEYWORDS:
            if kw.lower() in pname_lower:
                keep_hit = kw
                break

        if keep_hit:
            kept.append((page_id, page_name, cnt, f"KEEP_KEYWORD:{keep_hit}"))
            continue

        # Check explicit block list
        if page_id in EXPLICIT_BLOCK:
            to_block[page_id] = (page_name, EXPLICIT_BLOCK[page_id], cnt)
            continue

        # Check block keywords
        block_hit = None
        for kw in BLOCK_KEYWORDS:
            if kw.lower() in pname_lower:
                block_hit = kw
                break

        if block_hit:
            to_block[page_id] = (page_name, f"keyword match: '{block_hit}'", cnt)
            continue

        # Default: keep (uncertain - needs manual review)
        kept.append((page_id, page_name, cnt, "no rule matched"))

    print(f"\n{'='*70}")
    print(f"PAGES TO BLOCK: {len(to_block)}")
    print(f"{'='*70}")
    for pid, (pname, reason, cnt) in sorted(to_block.items(), key=lambda x: -x[1][2]):
        print(f"  [{cnt:4d} ads] {pname[:55]:<55} → {reason}")

    print(f"\n{'='*70}")
    print(f"PAGES KEPT (not blocked): {len(kept)}")
    print(f"{'='*70}")
    for pid, pname, cnt, reason in sorted(kept, key=lambda x: -x[2]):
        print(f"  [{cnt:4d} ads] {pname[:55]:<55} ({reason})")

    print(f"\n{'='*70}")
    print(f"Already in blocklist: {len(already_blocked)}")
    print(f"{'='*70}")

    print(f"\nReady to block {len(to_block)} pages affecting "
          f"{sum(c for _,(_, _, c) in to_block.items())} ads.")
    answer = input("Proceed? (yes/no): ").strip().lower()
    if answer not in ("yes", "y"):
        print("Aborted.")
        return

    # Apply blocks
    blocked_count = 0
    ads_updated = 0
    for page_id, (page_name, reason, cnt) in to_block.items():
        # Update blocklist json
        bl["pages"][page_id] = f"{page_name} — auto-blocked: {reason}"
        # Update DB
        cur.execute(
            "UPDATE politician_ads SET election_related='NO' WHERE page_id=? AND election_related='YES'",
            (page_id,)
        )
        ads_updated += cur.rowcount
        blocked_count += 1

    conn.commit()
    conn.close()

    # Save updated blocklist
    with open(BLOCKLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(bl, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Blocked {blocked_count} pages")
    print(f"✓ Updated {ads_updated} ads to election_related='NO'")
    print(f"✓ Blocklist now has {len(bl['pages'])} entries")
    print("\nDone. Run make_summary_excel.py to regenerate the Excel.")


if __name__ == "__main__":
    main()
