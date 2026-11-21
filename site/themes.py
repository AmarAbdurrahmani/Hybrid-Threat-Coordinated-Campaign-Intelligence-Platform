"""Transparent keyword theme tagger (Albanian, Serbian Latin + Cyrillic, English).

Each theme is a list of regular expressions shown verbatim on the site, so anyone can audit what a theme
means. A comment can carry several themes. This is keyword tagging, not a learned classifier.
"""
from __future__ import annotations

import re

THEMES: dict[str, dict] = {
    "contests_independence": {"label": "Contests Kosovo's independence", "short": "Kosovo is Serbia", "patterns": [
        r"kosovo\s*(?:je|is|=)\s*(?:srbija|serbia|србија)", r"косово\s*(?:је|=)\s*србија", r"\bkis\b", r"kosovo\s*i\s*metohija", r"косово\s*и\s*метохија",
        r"\bkim\b", r"(?:fake|not a|nije)\s*(?:country|state|država|drzava)", r"lažna\s*država", r"srpska\s*(?:zemlja|svetinja)", r"never\s*recogni[sz]e",
        r"kosovo\s*(?:belongs|is part of)\s*(?:to\s*)?serbia", r"srce\s*srbije", r"срце\s*србије", r"no\s*surrender"]},
    "supports_independence": {"label": "Supports Kosovo's independence", "short": "Kosova independent", "patterns": [
        r"kosova\s*(?:republik|e\s*pavarur|shtet)", r"republic\s*of\s*kosovo", r"kosovo\s*is\s*(?:a\s*)?(?:country|independent|kosovo)", r"kosova\s*është\s*e\s*jona",
        r"pavarësi", r"pavaresi", r"independen(?:t|ce)\s*kosovo", r"kosovo\s*independen", r"recogni[sz]e\s*kosovo"]},
    "religion": {"label": "Religion", "short": "Religion", "patterns": [
        r"\bgod\b", r"\bbog\b", r"\bбог\b", r"\bzot\b", r"\ballah", r"orthodox", r"pravoslav", r"православ", r"\bchurch", r"crkv", r"цркв", r"monaster", r"manastir",
        r"манастир", r"islam", r"muslim", r"musliman", r"christian", r"hrišć", r"hrisc", r"хришћ", r"katolik", r"catholic", r"\bxhami", r"džamij", r"dzamij", r"\bsveti\b", r"\bmosque"]},
    "nato": {"label": "NATO / KFOR", "short": "NATO", "patterns": [r"\bnato\b", r"\bнато\b", r"\bkfor\b", r"\bкфор\b", r"bombing\s*of\s*(?:serbia|yugoslavia)", r"bombardovan", r"бомбардов", r"\b1999\b"]},
    "russia": {"label": "Russia", "short": "Russia", "patterns": [r"russia", r"\brusij", r"русиј", r"\brus(?:i|ët|et)\b", r"putin", r"путин", r"moscow", r"moskv", r"москв", r"kremlin", r"kremlj", r"\brussian"]},
    "war": {"label": "War and violence", "short": "War", "patterns": [r"\bwar\b", r"\brat\b", r"\bрат\b", r"\blufta?\b", r"\bluft[eë]", r"genocid", r"геноцид", r"masakr", r"massacre",
        r"\bkill", r"\bubij", r"\bубиј", r"\bvra(?:j|n|ni|sin)\b", r"\barmy\b", r"\bvojsk", r"војск", r"\bushtri", r"terror"]},
    "albania": {"label": "Albania / Albanian nation", "short": "Albania", "patterns": [r"\balbania", r"\bshqip", r"\bshqiptar", r"\balbanac", r"\bшиптар", r"\bšiptar", r"\bsiptar", r"\bалбан", r"great(?:er)?\s*albania", r"velika\s*albanija", r"bashkim\s*kombëtar", r"\bkuq\s*e\s*zi"]},
    "eu_us": {"label": "EU / United States", "short": "EU/US", "patterns": [r"\beu\b", r"\bbrussel", r"\bбрисел", r"\bbrisel", r"\beurop", r"\bевроп", r"\busa\b", r"\bamerica", r"\bamerik", r"\bамерик", r"\bsad\b", r"\bсад\b", r"\btrump"]},
    "politicians": {"label": "Kurti / Vučić / leaders", "short": "Leaders", "patterns": [r"kurti", r"курти", r"vu[čc]i[ćc]", r"вучић", r"osmani", r"\bthaçi|\bthaci|\btaci\b", r"rama\b", r"brnabi"]},
}
_RX = {k: re.compile("|".join(f"(?:{p})" for p in v["patterns"]), re.IGNORECASE) for k, v in THEMES.items()}


def tag(text: str | None) -> list[str]:
    if not text:
        return []
    return [k for k, rx in _RX.items() if rx.search(text)]
