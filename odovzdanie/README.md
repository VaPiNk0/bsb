# Keystroke 2FA – odovzdanie

Semestrálny projekt z predmetu Biometrické systémy bezpečnosti (TUKE FEI KEMT): prihlásenie heslom
a druhým faktorom, ktorým je rytmus písania (keystroke dynamics).

Obsah tohto priečinka:

| Súbor | Čo to je |
|---|---|
| `README.md` | tento návod na spustenie |
| `Keystroke2FA_dokumentacia.pdf` | dokumentácia projektu (návrh, príznaky, rozhodovanie, bezpečnosť, nasadenie) |
| `Keystroke2FA_zdrojove_kody.zip` | zdrojové kódy (backend, frontend, testy, nasadenie) |

- Verejný repozitár: <https://github.com/VaPiNk0/bsb>
- Nasadená verzia: <https://keystroke-2fa.pages.dev>

## Požiadavky

- Python **3.12** alebo novší (`python --version`)
- moderný prehliadač (Chrome, Edge, Firefox, Safari); funguje na počítači aj na telefóne
- Node.js 18+ len pre nasadenie na Cloudflare Pages a pre jeden test frontendu (nepovinné)

## Spustenie lokálne

1. Rozbaľ `Keystroke2FA_zdrojove_kody.zip` (alebo `git clone https://github.com/VaPiNk0/bsb.git`) a otvor
   terminál v rozbalenom priečinku.
2. Vytvor virtuálne prostredie a nainštaluj závislosti.

   Windows (PowerShell):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

   Linux / macOS:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Spusti server:

   ```bash
   python run.py
   ```

4. Otvor v prehliadači <http://127.0.0.1:5000>.

Databáza SQLite sa vytvorí sama v `data/keystroke.db`. Nič ďalšie (databázový server, kľúče) lokálne
netreba: ochrana proti botom (Turnstile) a proxy sú bez nastavených premenných vypnuté.

### Skúška z telefónu v tej istej sieti

`run.py` počúva len na `127.0.0.1`. Ak chceš stránku otvoriť z telefónu v tej istej Wi-Fi, spusti:

```bash
flask --app backend.app run --host 0.0.0.0 --port 5000
```

a na telefóne otvor `http://<IP počítača>:5000`. Ťukni na vetu, otvorí sa klávesnica. Dotyková
klávesnica nehlási dĺžku držania klávesy, preto sa registruj aj prihlasuj na tom istom zariadení.

## Ako systém použiť

1. **Registrácia:** zadaj meno a heslo (aspoň 10 znakov) a prepíš aspoň 12 zobrazených viet.
   Píš malými písmenami bez diakritiky; preklepy oprav Backspace-om. Potom klikni na
   „Dokončiť registráciu“.
2. **Prihlásenie:** zadaj meno a heslo a prepíš jednu vetu. Po poslednom znaku sa pokus vyhodnotí sám
   (skóre voči prahu). Pri zamietnutí príde nová veta.
3. **Admin panel** (`/admin`): audit log pokusov, výsledky FAR/FRR/EER, export CSV. Lokálne ho vidí každý
   prihlásený používateľ; vo verejnom nasadení len účty v premennej `ADMIN_USERS`.

## Užitočné nastavenia (premenné prostredia)

| Premenná | Default | Význam |
|---|---|---|
| `METRIC` | `manhattan` | `manhattan` alebo `mahalanobis` |
| `K` | `1.5` | prísnosť prahu (`mean + k·std`); menej = prísnejšie |
| `MIN_ENROLLMENT` | `12` | najmenší počet viet pri registrácii |
| `ALLOW_TEST_MODE` | `0` | `1` zapne testovací mód na zber impostor/imitácia pokusov |
| `DATABASE_URL` | prázdne | Postgres (napr. Supabase) namiesto SQLite |

Príklad (PowerShell): `$env:METRIC="mahalanobis"; $env:K="2"; python run.py`

## Testy a vyhodnotenie

```bash
pytest -q
python evaluate.py --db data/keystroke.db --out results
```

`evaluate.py` z nazbieraných pokusov vytvorí FAR/FRR/EER tabuľky a ROC/DET grafy v `results/`.

## Verejné nasadenie

Backend beží na Renderi (`render.yaml`), databáza na Supabase a frontend s proxy `/api` na Cloudflare
Pages (`pages/`). Podrobný postup je v hlavnom `README.md`, časť o nasadení.
