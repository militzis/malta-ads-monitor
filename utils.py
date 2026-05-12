"""
Shared utilities for the Cyprus Political Ads Monitor.

Imported by:
  - make_combined_excel.py
  - app.py

Add new keywords / exclusion logic here — both scripts pick it up automatically.
"""
import os, csv, re, unicodedata

BASE      = os.path.dirname(os.path.abspath(__file__))
EXCL_FILE = os.path.join(BASE, "exclusions.csv")
CAND_FILE = os.path.join(BASE, "candidates.csv")
CAT_FILE  = os.path.join(BASE, "page_categories_cache.csv")

PARTY_PAGE_LABEL = 'Κομματικές/Πολιτικές Σελίδες'

# ── Keywords ──────────────────────────────────────────────────────────────────

PARTY_KEYWORDS = [
    'ακελ','akel','δησυ','disy','δηκο','diko','εδεκ','edek',
    'δηπα','dipa','ελαμ','elam','βολτ','volt','αλμα','alma',
    'δεκ ','dek ','οικολογοι','αμδη','σηκου πανω',
    'νεδησυ','nedisy','εδον ','edon ','νεολαια δημοκρατικ',
    'δημοκρατικος συναγερμος','δημοκρατική αλλαγή','δημοκρατικη αλλαγη',
    'κομμουνιστική πρωτοβουλία','κομμουνιστικη πρωτοβουλια',
    'προοδευτικη κ.φ','παγκυπρια εργατικη',
]

BUSINESS_KEYWORDS = [
    # Greek services
    'φαρμακείο','φροντιστήριο','εργαστήρι','εργαστήριο','γκαράζ','έπιπλα',
    'συνεργείο','καθαρισμών','χωματουργικές','παραδοσιακά προϊόντα','φρουταρία',
    'λογιστική','ψυχολόγος','χορωδία','πολιτιστικό σωματείο','ιδιωτικό κέντρο',
    'ιδιαίτερα μαθήματα','συμβουλευτικός','γενικός χειρουργός',
    # English services
    'supermarket','coaching','art gallery','archaeologist','historian',
    'psychology','training services','teaching','restaurant','cafe ','café',
    'do eat','inspired coaching','constructions','developers','jewellery',
    'catering','car sales','insurance','marine electrician','medical center',
    'art space','photography','mua','personal stylist','dietitian','nutritionist',
    'leatherex','3d factory','signs','ltd','llc',' α.ε',' ε.π.ε',' ο.ε',
    'personal trainer','shiatsu','pharmacy','artistic lab',
    'δρ.','δρ ','md ',', md','phd','msc',
    'photograph',
    # Food & drink
    'tavern','taverna','grill','mezedo','μεζεδο','ouzeri','ουζερί','winery',
    'kafeneio','kafenion','καφενεί','καφενείο','coffee house','beach bar',
    'rock bar','jazz club','wine bar','vinylio','fish market','ταβέρν',
    'ιχθυ','αετρι','mezedotexneio','μεζεδοτεχν',
    # Media & entertainment
    'podcast',' times','daily','rythmos','cablenet','cytavision',
    'partyline','portalcy','protathlima','tothema','ticketmaster',
    'soldout ticket','omegalive','black lemon','city free press',
    'inner report','lady times','fact grid','kypros daily',
    'alpha.jobs','alpha podcasts','ant1','ero times','artgrid',
    'balla.com','more.com','mysunshine','mytroodos','pame kypros',
    'politis.com','supersport','tolkers','world promo','skylight',
    'larnaka talks','check in cyprus','smart city guide','eparxiaki',
    'liva lit','dialogos','curis network',
    # Theatres & performing arts
    'theatre','theatro','θέατρο','θεατρ','opera','orchestra',
    'ensemble','festival','book fair','art walk','choreograph',
    'performing arts','music festival','stage live','stage production',
    'sardam','satiriko','anyhow theatre','κλπ theatre','amphitheatre',
    'θεατρική','θεατρικ','θεατράκι','pantomime','θέαση',
    # Cultural organisations
    'cultural movement','εκδόσεις','εκδοτικ','cultural foundation',
    'κινηματογραφ','cinema society','book festival','art festival',
    'λέσχη','art collective','culture tones','arts foundation',
    'pharos arts','pissis music','rialto',
    # Churches / religious
    'ιερός ναός','ιερά μητρόπολη','ιερά επισκοπή','μητροπολιτικός ναός',
    'παρεκκλήσιο','ιερός προσκυνηματικός','ορθόδοξ','χριστιανο',
    'αγίου εφραίμ','παναγία χρυσ','ιερά ','αγίου ιούδα',
    # Sports federations / clubs
    'federation','ομοσπονδία','cycling club','nautical club',
    'cheerleading','basketball federation','football manifesto',
    'hpo sports','themasports','protathlima',
    # Retail / commercial
    'hypermarket','casino','car rental','car rentals','toys',
    'kidswear','violin shop','handmade jewels','cosmetics',
    'ilovestyle','alphamega','ikea','lidl','toyota','cxc toys',
    'cwc pro','gastronomy','kelly','giraffe','golden donkeys',
    'pianeti','royal cosmetics','royale dance','feggera',
    'gift 2 you','make my bed','melitini baby','m.pantelides',
    'printing house','all by wood','invitation for wedding',
    'souvenir','σουβενίρ','charmar','amber n','beesbuzz',
    'biogel','magic cemy','magictouch','stefansxxl',
    'm & a creation','native energy','opto hellas',
    'adama hellas','boussias','cardet','icon biomed',
    'inpro','ina essentials','vector security',
    # Municipalities
    'municipality','δήμος ','κοινοτικό συμβούλιο','municipal market','municipal',
    # NGOs / Foundations (non-political)
    'foundation','charity','red cross','rare disorders','rotaract',
    'women4cyber','ithaki charity','psi foundation','special inclusive',
    'reset-research','thousand days','reunite our lives',
    'sophia for children','support cy','alliance for',
    'youth board','generation for change','blue heart cyprus',
    'center for social innovation','ena foundation','dione youth',
    # Health & medical
    'spine surgery','rehab lab','pharmamind','ygeiawatch',
    'men vitality','mind & health','journey into midwifery',
    'starantzis','physio','men\'s vitality',
    # Real estate
    'stavrinos-estates','properties','real estate','elmes homes',
    'propertytalk','inprohome','propxp','bazaraki',
    # Education (non-politician)
    'education lab','global educational','exelixi','education center',
    'ergo key','ergodotisi',
    # Misc non-political
    'partylinecyprus','growth hacking','urban gorillas',
    'nolimitway','noveltime','rich bonus','splash it',
    'neon reel','world promo','moyses the expedition',
    'artrat collective','avant garde','profit','cwc',
    'my books','myikona','artgrid','lush beach',
    'luminamare','lumina mare','aei film','drama epic',
    'downtown live','fever club','savino rock','savino live',
    'stage live','kcineplex','k-cineplex',
    'main event production','kolossi park',
    'larnaca events','pame kypros',
    'eri times','erotimes','ergotherapia',
    'babyconcertcy','chryso events',
    'decor d art','décor','fiore domenica',
    'gastronomy essentials','gavriel crop',
    'c2 cyprus','carac ','check in cy',
    'eureka group','artgrid.gr',
    # Businesses
    'charalambides christis','cellar 27','kitchen bar','elite lounge',
    'elitelounge','platon all day','sta ouza','wpa live','edesma tou paradeisou',
    'encantado','contessa beauty','contessa dating','gotshirts','faschool',
    'οπαπ','ονειροσταλίδα','kyriacou technics','zyprus real estate',
    'palaia elia',
    # Media
    'alphanews','cyprus corporate news','cyprus local news','in business news',
    'starnews','the cyprus political television','new media house',
    # Cultural/Arts orgs
    'culturetones','cyprus jazz','tango academy','diamantidou school',
    'theatriki omada garage','thoc','κέντρο θεάτρου','κοσμικό κέντρον αντωνάκη',
    'μουσικό σχήμα','λαογραφικ','κυπρίων θέασις',
    'κυπριακός οργανισμός εκπαίδευσης μέσω','sol music productions',
    'collyva art','standinline','pinxit','etheras studio','at vivo music',
    'gps to music','gpsto','lykofos','λυκόφως',
    # Religious
    'αγιος εφραίμ','εφραίμ','η του σωτήρος','χρυσοσπηλιώτ','υσεε κύπρου',
    'κύκλος φίλων','θησαυρίσματα',
    # Sports clubs
    'απόλλων λεμεσού','apollon limassol','παεεκ','παοκ κλήρου',
    'shekillz','digenisypson','onisillos','hpo sports','themasports',
    # Universities / Schools
    'university of cyprus alumni','university of limassol','university of nicosia',
    'pascal private secondary','chemistry lab by chara',
    # Other non-political orgs
    'αντικαρκινικός','πασυκαφ','pasykaf','παρατηρητήριο',
    'κέντρο παραγωγικότητας','κέντρο νεότητας','κυπριακός σύνδεσμος καταναλωτών',
    'επιστημονικό τεχνικό επιμελητήριο','ετεκ','ινστιτούτο ερευνών προμηθέας',
    'ανοικτό σχολείο','δημοτική βιβλιοθήκη','διατμηματικές εξετάσεις','εξετάσεις α',
    'αυθεντική περγαμηνή','βιταμίνα/vitamina',
    'γαλακτοβιομηχανία','γεύσεις ζωής','γεώργιος καραϊσκάκης',
    'γραφείο επιτρόπου','γραφείο τύπου','λάλλαρος',
    'μελάνι σε χαρτί','μικρή άρκτος','μωρά θαύματα','mora thavmata',
    'ο πράκτορας που πρόδωσε','πένθιμα γεγονότα','πιλόττα',
    'πολιτιστικό κέντρο πανεπιστημίου','προσκλητηρια',
    'σχολή βυζαντινών','σύνδεσμος φίλων κέντρου',
    'το υαλουργείο','τσιπουρομπερδέματα','φ.π.κ πρωτοπορία',
    'φεστιβάλ όψεις','φεστιβάλ γέλιου','φλου','φουρνιστό',
    'χάντρες του κόσμου','χαμογέλα μου','smile to me',
    'ουράνια λεπίδα','κύκλος μουσική kiklos','kiklos mousiki',
    'imh','mks family tours','mksfamily','tsaggaris bus','pluton travel',
    'h.t.s. hadjikakou','travel services','purple sparrow',
    'skoufa gallery','skoufa','salonica view','silverbrand',
    'savino live','pro loco cerisano','onisillos',
    'hpo sports','skouroumounis training','skouroumounis',
    'uniplex','volume 7','your voice','zwdia','zyprus',
    'iminder','men vitality','men\'s vitality',
    'athena astrologer','atheras peoples','baf ',
    'coco challenge','en platron','εν πλωρ','εν πλώ',
    'evaggelos polyzois','ευαγγελος πολυζοης',
    'house of science','σπίτι της επιστήμης',
    'inspiration path','lumina mare','movd.gr','moved.gr',
    'new media house','νέα δύναμη','neadynamis',
    'organ','συμφιλίωσι','symfiliosi',
    'support cy','the 1:1 diet',
    'κύπρου νόστος','κώστας γεωργίου','λάλλαρος',
    'ο νέος ελληνικός κόσμος','ουράνια λεπίδα',
    'παγκύπριος σύνδεσμος δημοκρατικών','πσσγπ','μοναδικά χαμόγελα',
    'ραστώνη','ρηγας φεραίος','ρηγασ','hhpo',
    'addison clark','afentiko',
]

NON_POLITICAL_CATEGORIES = {
    'Restaurant/Cafe','Restaurant','Cafe','Bar','Bakery','Food & Beverage',
    'Food & Grocery','Winery/Vineyard','Distillery','Brewery',
    'Retail and Consumer Merchandise','Clothing (Brand)','Jewelry/Watches',
    'Shopping & Retail','Retail','Grocery Store',
    'News & Media Website','Media/News Company',
    'Broadcasting & Media Production Company','TV Channel','Radio Station','Podcast',
    'Arts & Entertainment','Movie Theater','Concert Venue','Music Venue',
    'Performing Arts','Comedy Club','Casino',
    'Amateur Sports Team','Professional Sports Team','Sports Club',
    'Sports League','Sports & Recreation','Gym/Physical Fitness Center',
    'Medical & Health','Doctor','Dentist','Hospital/Clinic',
    'Health/Beauty','Spa','Beauty Salon','Cosmetics Store',
    'Education','College & University','School','Tutoring/Education',
    'Religious Organization','Church','Mosque','Synagogue',
    'Local Business','Company','Real Estate','Automotive',
    'Insurance Company','Travel Agency','Hotel & Lodging',
    'Bank/Financial Institution','Nonprofit Organization',
}

# ── Text helpers ──────────────────────────────────────────────────────────────

def _strip_accents(s: str) -> str:
    """Remove diacritics (works for Greek and Latin)."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s or '')
        if unicodedata.category(c) != 'Mn'
    )

def _norm(s: str) -> str:
    return _strip_accents((s or '').lower())

def _name_parts(name: str, min_len: int = 3) -> list:
    return [p for p in re.split(r'[\s\|\.\-\_]+', _norm(name)) if len(p) >= min_len]

def _is_latin(text: str) -> bool:
    """True if >70% of alphabetic characters are ASCII (Latin script)."""
    t = (text or '').strip()
    if not t:
        return False
    ascii_chars = sum(1 for c in t if ord(c) < 128 and c.isalpha())
    total_chars = sum(1 for c in t if c.isalpha())
    return total_chars > 0 and ascii_chars / total_chars > 0.7

# ── Candidate name list (loaded once at import) ───────────────────────────────

CANDIDATE_NAMES: list = []
if os.path.exists(CAND_FILE):
    with open(CAND_FILE, encoding='utf-8') as _f:
        for _row in csv.reader(_f):
            if _row and _row[0].strip():
                CANDIDATE_NAMES.append(_row[0].strip().lower())

# ── Core filter functions ─────────────────────────────────────────────────────

def flag_page(page_name: str, politician_query: str) -> str:
    """
    Classify a page row as one of:
      OK          – page name matches the attributed candidate
      LATIN_NAME  – Latin/English transliteration of the candidate
      PARTY_PAGE  – official party or political organisation
      CHECK       – name mismatch that needs manual review
    """
    pn_norm  = _norm(page_name)
    cand     = (politician_query or '').split('|')[0].lower()
    parts    = _name_parts(cand)

    if any(_norm(kw) in pn_norm for kw in PARTY_KEYWORDS):
        return 'PARTY_PAGE'

    if parts and any(p in pn_norm for p in parts):
        return 'OK'

    if _is_latin(page_name):
        pn_parts = _name_parts(page_name)
        for pp in pn_parts:
            for cp in parts:
                if pp[:4] == cp[:4] or cp[:4] == pp[:4]:
                    return 'LATIN_NAME'
        return 'LATIN_NAME'

    # Matches a *different* candidate → worth a manual check
    for cname in CANDIDATE_NAMES:
        cparts = _name_parts(cname)
        if cparts and any(p in pn_norm for p in cparts):
            return 'CHECK'

    return 'CHECK'


def is_business(page_name: str) -> bool:
    # Use _norm() so polytonic / accented variants always match
    pn = _norm(page_name)
    return any(_norm(kw) in pn for kw in BUSINESS_KEYWORDS)


def is_excluded(page_id: str, page_name: str,
                excl_ids: set, excl_names: set) -> bool:
    pid = str(page_id or '').strip()
    pn  = (page_name or '').lower().strip()
    if pid in excl_ids:
        return True
    if pn in excl_names:
        return True
    if any(excl in pn for excl in excl_names):
        return True
    return False


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_exclusions(path: str = EXCL_FILE):
    """Return (excluded_ids: set, excluded_names: set) from a CSV file."""
    ids, names = set(), set()
    if not os.path.exists(path):
        return ids, names
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            t, v = row.get('type', '').strip(), row.get('value', '').strip()
            if t == 'page_id' and v:
                ids.add(v)
            elif t == 'page_name' and v:
                names.add(v.lower())
    return ids, names


def load_page_categories(path: str = CAT_FILE) -> dict:
    """Return {page_id: category_string} from the cache CSV."""
    cats = {}
    if not os.path.exists(path):
        return cats
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            cats[row['page_id'].strip()] = row.get('category', '')
    return cats


def is_non_political_by_category(page_id: str,
                                  page_categories: dict) -> bool:
    cat = page_categories.get(str(page_id or '').strip(), '')
    return cat in NON_POLITICAL_CATEGORIES
