"""Hand-written seed lexicons. Extend these with fact-checker / analyst data.

Everything here is a *heuristic seed*: it is meant to surface leads for a human
analyst, not to classify people. Keep false-positive notes next to each entry.
"""
from __future__ import annotations

import re

# --- Diacritic folding (length-preserving, so regex spans map back 1:1) ---------
_FOLD = str.maketrans({"ë": "e", "Ë": "E", "ç": "c", "Ç": "C", "â": "a", "Â": "A", "ê": "e", "ô": "o", "’": "'", "`": "'"})


def fold(s: str) -> str:
    return (s or "").translate(_FOLD).lower()


def wrx(src: str) -> re.Pattern:
    """Word-bounded, case-insensitive regex that treats any letter as a word char."""
    return re.compile(r"(?<![^\W\d_])(?:" + src + r")(?![^\W\d_])", re.IGNORECASE | re.UNICODE)


# --- 1. Geg / Standard doublets: the SAME word in both dialect forms in one comment.
#        Mixed Geg+standard writing is normal; a same-word doublet is the signal.
DOUBLETS = [
    {"geg": "asht", "std": "eshte", "label": "âsht ↔ është"},
    {"geg": "nji", "std": "nje", "label": "nji ↔ një"},
    {"geg": "kene", "std": "qene", "label": "kenë ↔ qenë"},
    {"geg": "cka", "std": "cfare", "label": "çka ↔ çfarë"},
    {"geg": "kurrgjo", "std": "asgje", "label": "kurrgjo ↔ asgjë"},
    {"geg": "tash", "std": "tani", "label": "tash ↔ tani"},
    {"geg": "vec", "std": "vetem", "label": "veç ↔ vetëm"},
    {"geg": "prap|prape", "std": "perseri|serish", "label": "prap ↔ përsëri"},
]
for d in DOUBLETS:
    d["geg_rx"], d["std_rx"] = wrx(d["geg"]), wrx(d["std"])

GEG_MARKERS = wrx(r"asht|nji|kene|cka|kurrgjo|tash|vec|prap|prape|qysh|krejt|du|me (?:pa|ba|shku|u \w+|i \w+|ruajt|mbrojt|punu|ardh)|punu")
STD_MARKERS = wrx(r"eshte|nje|qene|cfare|asgje|tani|vetem|perseri|serish|dua|gjithmone")

# --- 2. Serbian -> Albanian calques, with the literal English rendering used in
#        translated views (so the anomaly stays visible to English readers).
CALQUES = [
    {"re": r"pyetje e kohes|pyetje kohe", "en": r"question of the time", "sq": "pyetje e kohës", "src": "pitanje vremena", "natural": "a matter of time"},
    {"re": r"(?:te )?kemi ne shikim|keni ne shikim", "en": r"keep in sight", "sq": "të kemi në shikim", "src": "imati u vidu", "natural": "bear in mind"},
    {"re": r"ne fund te fundeve", "en": r"(?:at )?the end of the ends", "sq": "në fund të fundeve", "src": "na kraju krajeva", "natural": "at the end of the day"},
    {"re": r"cfare ka lidhje", "en": r"what connection does", "sq": "çfarë ka lidhje", "src": "kakve to ima veze", "natural": "what does that have to do with"},
    {"re": r"punohet per", "en": r"(?:it is )?being worked about", "sq": "punohet për", "src": "radi se o", "natural": "it is about"},
    {"re": r"vjen deri te", "en": r"comes up to the point", "sq": "vjen deri te", "src": "dolazi do toga", "natural": "it turns out that"},
    {"re": r"mos u bej i zgjuar", "en": r"make yourself smart", "sq": "mos u bëj i zgjuar", "src": "ne pravi se pametan", "natural": "don't act clever"},
]
for c in CALQUES:
    c["rx"], c["en_rx"] = wrx(c["re"]), wrx(c["en"])

# --- 3. Threat vectors (national-security narrative categories).
#        "sq" patterns run on diacritic-folded Albanian; "en" on English.
#        "exemplars" feed the semantic matcher (embedding similarity).
TROPES: dict[str, dict] = {
    "kfor_destabilization": {
        "label": "KFOR destabilisation", "severity": "STRATEGIC",
        "sq": [r"kfor[\w-]*(?:\s+\S+){0,3}\s+(?:do te largohet|po largohet|ikin|nuk ju mbron|s'ju mbron|nuk na mbron)"],
        "en": [r"kfor(?:\s+\S+){0,3}\s+(?:is leaving|is secretly leaving|will leave|no longer protects|does not protect|doesn't protect|won't protect)"],
        "exemplars": ["KFOR is secretly leaving Kosovo", "KFOR no longer protects you", "KFOR-i po largohet fshehurazi", "KFOR-i nuk ju mbron më"],
    },
    "anti_nato": {
        "label": "Anti-NATO propaganda", "severity": "STRATEGIC",
        "sq": [r"(?:nuk|s')\s*(?:eshte|asht|esht)\s+(?:miku|shoku|mik|shok)\s*(?:i\s+)?(?:yne|yni)", r"bazat? e huaja",
               r"okupim\w*|okupator\w*|pushtues\w*", r"nato\w*(?:\s+\S+){0,3}\s+(?:do te largohet|do ikin|na braktis\w*)", r"nuk e duan nato\w*"],
        "en": [r"(?:nato|it) is not our (?:friend|buddy|ally)", r"foreign bases", r"occupation|occupier\w*", r"don't want nato|do not want nato",
               r"nato(?:\s+\S+){0,8}\s+(?:will leave|will abandon)"],
        "exemplars": ["NATO is not our friend", "foreign bases are an occupation", "NATO-ja nuk është miku ynë", "bazat e huaja janë okupim"],
    },
    "infrastructure_panic": {
        "label": "Critical-infrastructure panic", "severity": "STRATEGIC",
        "sq": [r"(?:rryma|energjia|uji|interneti)(?:\s+\S+){0,3}\s+(?:do te nderpritet|nderpritet|do te ike|eshte helmuar|do te prishet)", r"pergatituni"],
        "en": [r"(?:power|electricity|water|internet)(?:\s+\S+){0,4}\s+(?:will be cut|is being cut|is poisoned|will go out)", r"get ready"],
        "exemplars": ["the power in the north will be cut tonight, get ready", "the water has been poisoned", "rryma në veri do të ndërpritet sonte"],
    },
    "ethnic_incitement": {
        "label": "Ethnic / religious incitement", "severity": "STRATEGIC",
        "sq": [r"(?:katoliket|muslimanet|ortodokset)(?:\s+\S+){0,6}\s+(?:kurre|nuk do)", r"muslimanet e vertete", r"kryqtar\w*"],
        "en": [r"(?:catholics|muslims|orthodox)(?:\s+\S+){0,6}\s+(?:will never|never)", r"true muslims", r"crusader\w*"],
        "exemplars": ["Catholics and Muslims will never be together", "true Muslims do not want the West", "katolikët dhe muslimanët nuk do të jenë kurrë bashkë"],
    },
    "sellout": {
        "label": "Sell-out / abandonment", "severity": "OPERATIONAL",
        "sq": [r"na (?:shet|tregton|shiti)", r"per interesat e (?:veta|tyre)"],
        "en": [r"selling us(?: out)?|sold us(?: out)?|trading us", r"for (?:its|their) (?:own )?interests"],
        "exemplars": ["they are selling us out for their own interests", "na shesin për interesat e tyre"],
    },
    "west_betrayal": {
        "label": "Western betrayal", "severity": "OPERATIONAL",
        "sq": [r"perendimi(?:\s+\S+){0,2}\s+na (?:tradhtoi|shiti|percan|perdor|mashtron)", r"na tradhtoi", r"brukseli na mashtron"],
        "en": [r"the west(?:\s+\S+){0,2}\s+(?:betrayed|sold|divides|uses|deceives) us", r"betrayed us", r"brussels is deceiving us"],
        "exemplars": ["the West betrayed us again", "Brussels is deceiving us", "Perëndimi na tradhtoi"],
    },
    "national_figures": {
        "label": "Erosion of national symbols", "severity": "OPERATIONAL",
        "sq": [r"skenderbe\w*(?:\s+\S+){0,2}\s+(?:mit|genjeshter\w*|tradhtar\w*|i shpikur)", r"heronjte(?: tane)? jane mite"],
        "en": [r"skanderbeg(?:\s+\S+){0,2}\s+(?:a myth|myth|a lie|a traitor)", r"our heroes are myths"],
        "exemplars": ["Skanderbeg was a myth invented by foreigners", "Skënderbeu ishte mit"],
    },
    "defeatism": {
        "label": "Defeatism / statehood denial", "severity": "OPERATIONAL",
        "sq": [r"(?:ne )?(?:s'|nuk )jemi shtet", r"serbia ka te drejte", r"plan te fshehte"],
        "en": [r"we are not a state", r"serbia is right", r"secret plan"],
        "exemplars": ["we are not a real state", "there is a secret plan against us", "ne s'jemi shtet"],
    },
}
for t in TROPES.values():
    t["sq_rx"] = [wrx(p) for p in t["sq"]]
    t["en_rx"] = [wrx(p) for p in t["en"]]

# --- Tokenisation helpers -----------------------------------------------------
STOP = set(
    "the is are was were be been a an of for to and or it its our ours us we this that these those will would not no on in with they "
    "their them you your what who why at by from has have had do does don't again one then just like than there here about into over more "
    "most some any all can could should must let i i'm "
    "nuk eshte asht yne yni per kjo dhe qe nga por jane ishte nje nji edhe kush pse mos apo jo tani tash une ata ato kete keto ky ajo ai "
    "do te ne na se si me ka ke jam jemi duhet".split()
)
SYN = {"shoku": "mik", "shok": "mik", "miku": "mik", "tregton": "shet", "shiti": "shet", "shesin": "shet", "tyre": "veta",
       "okupator": "okupim", "pushtues": "okupim", "pushtim": "okupim", "buddy": "friend", "ally": "friend", "friends": "friend",
       "trading": "selling", "sold": "selling", "sells": "selling", "sell": "selling", "occupier": "occupation", "occupiers": "occupation"}
_SUFFIXES = ["eve", "ave", "it", "in", "at", "ja", "ve", "e", "u", "i", "n", "s"]


def stem(w: str) -> str:
    if w in SYN:
        return SYN[w]
    for suf in _SUFFIXES:
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


_TOKEN = re.compile(r"[^\W\d_']+(?:'[^\W\d_]+)*", re.UNICODE)


def tokens_with_pos(folded: str):
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(folded)]


def content_set(toks) -> set[str]:
    return {stem(w) for w, _, _ in toks if len(w) >= 3 and w not in STOP}


_SQ_HINT = wrx(r"eshte|nuk|dhe|per|qe|te|nje|jane|kjo|na|asht|nji")


def detect_lang(text: str) -> str:
    """Cheap sq/en router. Swap for fastText lid.176 in production."""
    if re.search(r"[ëçË]", text or ""):
        return "sq"
    return "sq" if len(_SQ_HINT.findall(fold(text))) >= 2 else "en"
