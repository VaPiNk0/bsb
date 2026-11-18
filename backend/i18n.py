"""Anglické znenie hlášok, ktoré API vracia v poli `error`.

Hlášky v kóde ostávajú po slovensky; ak si klient vypýta angličtinu (hlavička X-Lang),
odpoveď sa preloží až tu. Neznáma hláška ostane v pôvodnom znení.
"""
from __future__ import annotations

import re

EXACT = {
    "Neplatný CSRF token": "Invalid CSRF token",
    "Očakávam JSON objekt": "Expected a JSON object",
    "Meno musí obsahovať 3–32 znakov: A-Z, a-z, 0-9, ., _, -": "The username must be 3–32 characters: A-Z, a-z, 0-9, ., _, -",
    "Najprv sa prihlás.": "Log in first.",
    "Príliš veľa pokusov, chvíľu počkaj.": "Too many attempts, wait a moment.",
    "Neplatný typ pokusu": "Invalid attempt type",
    "V test mode musí byť zadaný tester ID": "Test mode requires a tester ID",
    "Genuine pokus musí mať tester ID rovnaké ako cieľový účet": "A genuine attempt needs the tester ID to match the target account",
    "Impostor/imitácia musí pochádzať od iného testera": "An impostor or imitation attempt must come from a different tester",
    "Neautorizované": "Not authorized",
    "Heslo musí mať aspoň 10 znakov": "The password must be at least 10 characters",
    "Heslá sa nezhodujú": "The passwords do not match",
    "Tento účet už má šablónu pre tento typ zariadenia.": "This account already has a template for this type of device.",
    "Na pridanie ďalšieho zariadenia treba platný párovací kód.": "Adding another device needs a valid pairing code.",
    "Registrácia nie je rozbehnutá. Začni ju znova.": "No sign-up is in progress. Start it again.",
    "Šablóna pre toto zariadenie už existuje.": "A template for this device already exists.",
    "Neznámy typ zariadenia": "Unknown device type",
    "Používateľ existuje, ale heslo nesedí.": "This user exists, but the password is wrong.",
    "Registrácia tohto mena práve prebieha. Skús to znova.": "A sign-up with this username is in progress. Try again.",
    "Na serveri nastala chyba. Skús to o chvíľu znova.": "The server ran into an error. Try again in a moment.",
    "Používateľ neexistuje": "This user does not exist",
    "Challenge je neplatný alebo expirovaný": "The challenge is invalid or expired",
    "Challenge je neplatný, použitý alebo expirovaný.": "The challenge is invalid, used or expired.",
    "Identická enrollment vzorka bola už použitá. Zopakuj ju.": "An identical sample was already used. Type it again.",
    "Identická vzorka bola už použitá. Zopakuj ju.": "An identical sample was already used. Type it again.",
    "Maximum enrollment vzoriek už bolo dosiahnuté.": "The maximum number of samples has been reached.",
    "Autentifikácia zlyhala.": "Authentication failed.",
    "Účet je dočasne zamknutý.": "The account is temporarily locked.",
    "Detegovaný replay / identický vektor.": "Replay detected: identical timing vector.",
    "Veta obsahovala príliš málo použiteľných kláves. Skús znova s novou vetou.": "The sentence had too few usable keys. Try again with a new one.",
    "Neznáma tabuľka": "Unknown table",
    "Táto vzorka sa príliš líši od tvojej šablóny, preto sa nepoužila. Skús ju napísať znova.": "This sample differs too much from your template, so it was not used. Type it again.",
    "Dostupné len v test mode.": "Available in test mode only.",
    "Overenie, že nie si robot, zlyhalo. Skús to znova.": "The bot check failed. Try again.",
    "Chýbajú údaje o stlačených klávesoch": "Keystroke data is missing",
    "Príliš veľa stlačení kláves": "Too many keystrokes",
    "Časová postupnosť kláves je nekonzistentná": "The keystroke timing is inconsistent",
    "Backspace na prázdnom texte": "Backspace on empty text",
    "Príliš veľa opráv": "Too many corrections",
    "Napísaný text sa nezhoduje so zadanou vetou": "The typed text does not match the sentence",
    "Príliš málo čistých prechodov medzi klávesami (veľa opráv)": "Too few clean key transitions (too many corrections)",
    "Enrollment vzorky pokrývajú príliš málo príznakov, zopakuj registráciu": "The samples cover too few features, repeat the sign-up",
}

PATTERNS = [
    (re.compile(r"^Potrebných je aspoň (\d+) vzoriek\.$"), r"At least \1 samples are needed."),
    (re.compile(r"^Neplatný záznam na pozícii (\d+)$"), r"Invalid record at position \1"),
    (re.compile(r"^Neplatná klávesa na pozícii (\d+)$"), r"Invalid key at position \1"),
    (re.compile(r"^Neplatné časové údaje na pozícii (\d+)$"), r"Invalid timing at position \1"),
    (re.compile(r"^Neplatné down/up na pozícii (\d+)$"), r"Invalid down/up at position \1"),
]


def to_english(message: str) -> str:
    if message in EXACT:
        return EXACT[message]
    for pattern, replacement in PATTERNS:
        if pattern.match(message):
            return pattern.sub(replacement, message)
    return message
