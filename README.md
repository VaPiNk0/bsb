# Keystroke 2FA – TUKE FEI KEMT

Semestrálny projekt: behaviorálna biometrická autentifikácia podľa dynamiky písania. Aplikácia simuluje jednoduchý administračný panel, do ktorého sa používateľ dostane iba po úspešnom overení **hesla + keystroke dynamics**.

## Architektúra

- `backend/app.py` – Flask API, 2FA workflow, session, bezpečnostné limity.
- `backend/features.py` – kontrola sekvencie kláves a extrakcia dwell/DD/UD príznakov.
- `backend/scoring.py` – robustná šablóna, Manhattan/Mahalanobis, LOO threshold.
- `backend/db.py` – SQLite schéma a úložisko biometrických vektorov/audit logu.
- `backend/config.py` – konfigurácia cez premenné prostredia.
- `frontend/` – čisté HTML/CSS/JS, bez frameworku.
- `evaluate.py` – FAR/FRR/EER, ROC/DET, metric/enrollment/k/scenario porovnania.
- `tests/` – unit testy pre features a scoring.

## Prečo práve tieto nastavenia

**Vety:** každý challenge dostane novú vetu (35–55 znakov, malé písmená a-z a medzera). Vety generuje `backend/sentences.py`: ak je nastavený `LLM_URL`, použije sa lokálny LLM cez OpenAI-kompatibilné API (Ollama, LM Studio, llama.cpp, vLLM) a viety sa predgenerujú do zásobníka na pozadí; inak (alebo keď LLM neodpovie) sa použije offline šablónový generátor. Vetu si drží server, takže ju klient nemôže zmeniť. Keďže znaky sa porovnávajú podľa `event.key`, funguje to rovnako na QWERTY aj QWERTZ.

**Príznaky:** vektor nie je viazaný na pozíciu znaku, ale na klávesu a dvojicu znakov: 27 dwell (a–z a medzera) + 44 DD pre frekventované bigramy + 16 globálnych (medián dwell/DD/UD, 90. percentil DD, % rollover a kvartily a chvosty rozloženia dwell/DD/UD: p10, p25, p75, p90) = **87 rozmerov**. Globálne príznaky sa počítajú zo všetkých stlačení vo vete, preto sú najmenej zašumené; 11 príznakov rozloženia pribudlo vo verzii šablóny 4 a na benchmarku nižšie samy znížili EER asi o 1 percentuálny bod. Vzorky uložené staršou verziou majú 76 hodnôt a chýbajúci koniec sa berie ako nepozorovaný, takže fungujú ďalej; nové príznaky sa do šablóny dostanú novou registráciou alebo mesačnou obnovou. Čo veta neobsahuje, je `null` a pri skórovaní sa preskočí (skóre sa počíta len cez príznaky, ktoré šablóna pozná aspoň z 2 vzoriek a ktoré sa v pokuse vyskytli; minimum 15).

**MAD namiesto σ:** per-feature scale používa `1.4826 × MAD`, s minimom 5 ms. MAD je pri malom datasete robustnejší voči jednotlivým výkyvom než obyčajná smerodajná odchýlka.

**Robustné odstránenie outlierov:** vzorky sa najprv ohodnotia robustným odklonom od mediánu; extrémne riadky sa vyradia iba vtedy, keď ostane aspoň polovica dát. Pri malom datasete je lepšie byť konzervatívny.

**Shrinkage škály a orezanie z:** príznak videný len v 2–5 vetách má MAD veľmi nepresnú (často náhodne skoro nulovú), takže jediný bigram vedel dať |z| ≈ 5–10. To nafukovalo rozptyl genuine skóre, a tým aj prah. Preto sa škála každého príznaku ťahá k typickej relatívnej škále dobre pozorovaných príznakov (váha 4 pozorovania) a |z| jedného príznaku sa orezáva na 3. Na podvzorke verejného datasetu Aalto 136M Keystrokes (587 ľudí, 10 viet enrollment, zvyšné vety genuine, 40 cudzích viet na účet) to znížilo EER z ~18 % na ~10,5 % a FAR pri k = 2 zo 42 % na 20 %.

**k = 1.5:** na tom istom benchmarku (polovica ľudí odložená, na druhej sa ladilo; čísla sú z odloženej) dáva aktuálna verzia pri jednom pokuse k = 0.75 FRR 19 % / FAR 4 %, k = 1 FRR 15 % / FAR 6 %, k = 1.5 FRR 8 % / FAR 9 %, k = 2 FRR 5 % / FAR 13,5 %; EER 8,8 % (predtým 10,1 %). Po zamietnutí však appka hneď ponúkne novú vetu, takže rozhoduje, čo sa stane v priebehu niekoľkých pokusov: do 3 pokusov je to pri k = 1 FRR 0,3 % / FAR 11 %, pri k = 1.5 FRR 0,1 % / FAR 16 % (pôvodná verzia s k = 1.5: FRR 0,6 % / FAR 21 %). Opakovanie pomáha pravému používateľovi oveľa viac než cudziemu (jeho pokusy sú zlé konzistentne). Benchmark je však písaný v jednom sedení; pri prihlásení v iný deň než registrácia vychádzajú skóre pravého používateľa vyššie než LOO odhad, a k = 1 ho v praxi zamietalo príliš často. Default je preto 1.5; pre prísnejší systém nastav `K=1.0`, pre menej zamietnutí `K=2.0`. V evaluácii sa k sweepuje, pretože bezpečnostne kritický admin systém nemá byť obhajovaný iba jednou arbitrárnou hodnotou. Čísla sú z cudzieho datasetu (anglické vety, každý účastník na vlastnej klávesnici), takže sú orientačné; vlastné meranie rob cez `evaluate.py`.

**Mahalanobis:** používa sa na porovnanie. Kovariancia z-skóre sa shrinkuje k identite; pri skórovaní sa berie len podmatica pre pozorované príznaky a výsledok sa delí počtom príznakov, aby boli skóre porovnateľné medzi vetami.

**Enrollment 12–20:** default UI je 16 (stred rozsahu), minimum je 12, lebo pri meniacom sa texte treba viac vzoriek. Každá vzorka je iná veta. Dáta sa držia jednotlivo v DB, takže `evaluate.py` vie simulovať rôzny počet vzoriek bez opätovného zberu (prepíš `DEFAULT_ENROLLMENT_COUNTS` na hodnoty ≥ 12).

**Prah:** každý enrollment sample sa skóruje voči šablóne z ostatných vzoriek (leave-one-out); výsledkom je `mean + k·std`. Tým sa prah neodvodzuje z rovnakého self-match, čo by ho príliš optimisticky znižovalo. Smerodajná odchýlka z 12–20 LOO skóre je sama nepresná (niekto by dostal náhodou voľný prah, iný prísny), preto sa ťahá k typickému pomeru std/mean = 0,16 s váhou 10 pozorovaní (`calibrate_threshold` v `scoring.py`).

## Bezpečnostný model

### 2FA

Heslo je prvý faktor, keystroke dynamics druhý faktor. Appka nepoužíva biometriku ako náhradu hesla.

### Rate limiting a lockout

- default: max. 15 pokusov / účet + IP za 60 s,
- default: 5 neúspešných overení -> 300 s account lock,
- test mode je **defaultne vypnutý** (`ALLOW_TEST_MODE=0`), lebo ho inak vie zapnúť ktorýkoľvek klient a obišiel by lockout. Na zber dát ho zapni premennou prostredia (PowerShell: `$env:ALLOW_TEST_MODE=1`); v ňom je lockout vypnutý, kým nenastavíš `ENFORCE_LOCKOUT_IN_TEST_MODE=1`.
- známy limit: lockout je na účet, takže zlé heslo od kohokoľvek zamkne cudzí účet na 5 minút (DoS). Pre demo akceptovateľné, patrí do diskusie.

### Replay

Každý enrollment aj verification pokus dostane náhodný jednorazový challenge token s 90 s platnosťou. Token sa po použití označí ako použitý. Navyše sa hash feature vektora porovnáva s predchádzajúcimi pokusmi; identický vektor sa odmietne a audit log ho označí ako `replay`.

Dôležitá hranica: challenge token sám o sebe nezabráni sofistikovanému klientovi, ktorý zachytený vektor skutočne prepošle cez nový challenge. Zvolená ochrana je preto **demo-level replay detection**, nie kryptograficky viazaný liveness protokol. To treba uviesť v diskusii.

### Privacy

Backend nedostáva surový text vety. Frontend ho používa iba lokálne na validáciu. Backend dostáva `event.code`, `down`, `up`, z ktorých vytvorí číselný vektor; do DB sa ukladajú iba číselné príznaky a metadáta pokusu.

Na internet tento demo server nevystavuj bez TLS, proper session secretu a reverse-proxy hardeningu.

## Spustenie

Odporúčaný Python: **3.12+**.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Potom otvor `http://127.0.0.1:5000`.

Databáza sa vytvorí automaticky v `data/keystroke.db`.

### Konfigurácia

```text
METRIC=manhattan|mahalanobis
K=1.5
ADAPTIVE_TEMPLATE=0|1
ALLOW_TEST_MODE=0
ENFORCE_LOCKOUT_IN_TEST_MODE=0
```

Príklad:

```powershell
$env:METRIC="mahalanobis"; $env:K="2.5"; python run.py
```

## Demo scenár na obhajobu

1. Zaregistruj `admin01` s testovacím heslom dlhým aspoň 10 znakov.
2. Prepíš zobrazené vety (každá je iná). Preklepy oprav Backspace-om (max. 15 opráv na vetu); Shift, Caps Lock a paste pokus zneplatnia a appka sama ponúkne novú vetu.
3. Na login ukáž genuine pokus → po poslednom znaku sa vyhodnotí sám, zobrazí výsledok, skóre a prah. Po zamietnutí príde automaticky nová veta (nový challenge).
4. Zapni Testovací mód. Ako tester zvoľ napr. `P02`, cieľ `admin01`, typ `impostor` a prepíš zobrazenú vetu vlastným štýlom.
5. Pre imitáciu zvoľ `imitacia` a nechaj testera vedome spomaľovať/rýchliť rytmus podľa cieľového používateľa.
6. Pri úspešnom genuine login otvor admin panel a ukáž audit log.

## Zber datasetu pre 5–10 ľudí

Najčistejší experiment je držať **rovnaký počítač, rovnakú fyzickú klávesnicu, rovnaký browser a rovnaké nastavenie layoutu**.

Pre každého účastníka:

- enrollment: 12–20 viet,
- genuine verification: 10–20 pokusov v niekoľkých minútach/časoch,
- zero-effort impostor: iný účastník 10–20 pokusov na cieľový účet,
- imitácia: 5–10 pokusov, kde útočník cielene napodobňuje cieľ.

Prakticky pre 8 ľudí je silný minimálny dataset: 12 enrollment + 12 genuine + 12 zero-effort impostor + 6 imitácia na používateľa, ak čas dovolí.

V test mode používaj `Tester ID` typu `P01`, `P02`, ...; typ `genuine` má mať actor = cieľový používateľ, `impostor` actor ≠ cieľ a `imitacia` actor ≠ cieľ.

## Vyhodnotenie

```bash
python evaluate.py --db data/keystroke.db --out results
```

Vzniknú:

- `summary_metrics.csv` – FAR/FRR/EER pre metric × enrollment count,
- `eer_per_user.csv` – EER per user,
- `operating_points.csv` – empirické FRR pri cieľovom FAR 1 % a 5 % s per-user thresholdmi,
- `scenario_comparison.csv` – genuine vs zero-effort vs imitation,
- `roc_manhattan_global.png`, `roc_mahalanobis_global.png`,
- `det_manhattan_global.png`, `det_mahalanobis_global.png`.

Poznámka k FAR 1 %: pri malom počte impostorov nemusí byť 1 % diskrétne dosiahnuteľné. Vtedy interpretuj výsledok ako „0 false accepts v malej testovacej vzorke“, nie ako dôkaz reálneho 1 % FAR.

## Diskusia limitov

- **Malý dataset:** EER/FAR/FRR budú mať vysokú neistotu; jeden chybný pokus môže výrazne zmeniť percentá.
- **Keyboard/device dependence:** dwell a flight times závisia od typu klávesnice, OS, browsera, latencie a fyzických návykov.
- **Session/time drift:** únava, stres alebo zmena pracovného prostredia menia rytmus.
- **Text-dependent → free-ish text:** meniaca sa veta zvyšuje variabilitu (rôzne bigramy v každom pokuse), takže EER bude pravdepodobne horší než pri jednej pevnej vete. Je to cena za to, že sa veta nedá naučiť naspamäť ani zachytiť a prehrať.
- **Challenge/replay:** detekcia identického vektora nie je plnohodnotná anti-replay kryptografia.
- **Threshold calibration:** `mean + k·std` je jednoduchý model; pri reálnom nasadení by bol vhodný validačný dataset oddelený od testu a kalibrácia thresholdov na ROC/DET.
- **Mahalanobis:** pri malom počte vzoriek a vysokom rozmere je kovariancia nestabilná, preto shrinkage; aj tak je to hlavne porovnávací experiment.
- **Adaptácia:** voliteľná adaptácia môže časom absorbovať drift, ale nesmie automaticky absorbovať úspešný impostor; v implementácii sa adaptuje iba genuine úspech.

## Testy

```bash
pytest -q
```

## Obhajobná veta k hlavnej myšlienke

„Systém nehodnotí, čo používateľ napísal, ale ako rýchlo jednotlivé klávesy stláča a uvoľňuje a aké časové odstupy má medzi klávesami; z týchto meraní vytvára používateľskú behaviorálnu šablónu a rozhoduje podľa vzdialenosti od nej.“


## Zmeny oproti pôvodnej verzii

- **Oprava static ciest**: `Flask(static_folder=None)`. Pôvodne vlastná `/static` cesta nefungovala a CSS/JS vracali 404.
- **Rollover kláves**: záporné UD (ďalšia klávesa stlačená pred uvoľnením predošlej) je už povolené; pribudli horné limity na dwell/DD a test.
- **Test mode** je defaultne vypnutý (pozri vyššie).
- **Bez enumerácie používateľov**: `/api/verification/begin` vracia rovnakú odpoveď pre neexistujúci účet aj účet bez šablóny; zamknutie sa odhalí len pri správnom hesle.
- **Presnejšie časovanie**: frontend berie čas klávesy z `event.timeStamp`, nie z `performance.now()` v handleri, takže prekresľovanie UI neskresľuje dwell/DD.
- **Presnejšie rozpoznanie** (verzia šablóny 4): 11 nových príznakov rozloženia časov, stabilnejší prah; kovariancia sa ukladá len pre Mahalanobis (šablóna má ~2 kB namiesto ~90 kB).
- **Robustnejšie skórovanie** (verzia šablóny 3): shrinkage škály, orezanie |z|. Staršie šablóny sa pri najbližšom prihlásení automaticky prepočítajú z uložených enrollment vzoriek.
- **Nový frontend**: živý graf rytmu písania (dwell/DD pre každú klávesu), segmentovaný postup registrácie, ukazovateľ skóre voči prahu a v admin paneli graf skóre/prah všetkých pokusov.

## Generátor viet a lokálny LLM

Bez konfigurácie beží offline šablónový generátor (stovky kombinácií, nie je to neurónová sieť). Na generovanie lokálnym modelom:

```powershell
# príklad: Ollama
ollama pull qwen2.5:7b
$env:LLM_URL="http://127.0.0.1:11434/v1"
$env:LLM_MODEL="qwen2.5:7b"
python run.py
```

Voliteľne `LLM_API_KEY`, `LLM_TIMEOUT` (s). Server si každú dávku vygenerovaných viet prefiltruje (odstráni diakritiku a interpunkciu, skontroluje dĺžku 35–55 znakov) a vety, ktoré neprejdú, zahodí. Ak LLM neodpovedá, prihlásenie ďalej funguje s offline generátorom. V audit logu a DB (`phrase`) je uložená veta každého pokusu.

**Poznámka k starej databáze:** starší `data/keystroke.db` s pevným 136-prvkovým vektorom je nekompatibilný. Zmaž ho pred prvým spustením.

## Mesačná obnova šablóny (drift písania)

Štýl písania sa v čase mení: človek sa zrýchli, zvykne si na klávesnicu, alebo naopak spomalí (únava, zranenie, iná klávesnica). Šablóna z jedného dňa by časom zamietala vlastného používateľa. Preto sa raz za `TEMPLATE_REFRESH_DAYS` (default 30) dní šablóna obnovuje.

**Priebeh:**
1. Po úspešnom prihlásení server vráti `refresh.due = true`, ak je šablóna staršia než 30 dní. UI ponúkne obnovu (dá sa odložiť tlačidlom „Neskôr").
2. Používateľ prepíše `REFRESH_SAMPLES` (default 6) nových viet. Obnova je možná len v prihlásenej relácii, takže útočník bez prejdenej biometrie šablónu neotrávi.
3. Každá vzorka sa skóruje voči aktuálnej šablóne. Ak je nad `REFRESH_MAX_RATIO` (1,6×) prahu, odmietne sa a používateľ napíše ďalšiu. To chráni pred otrávením šablóny a pred zmenou, ktorú už nepôsobí ten istý človek.
4. Po zozbieraní vzoriek sa vyhodnotí drift a šablóna sa prepočíta z posledných `MAX_ENROLLMENT` (20) vzoriek (staré vypadávajú, nové pribúdajú). Záznam ide do tabuľky `template_history` a zobrazí sa v admin paneli.

**Vyhodnotenie driftu (`backend/drift.py`):**

| Verdikt | Podmienka | Čo sa stane |
|---|---|---|
| zlepšenie | tempo o ≥ 5 % rýchlejšie alebo rozptyl o ≥ 10 % nižší, bez protichodného signálu | prah sa môže sprísniť (najviac o 30 %), systém je presnejší |
| stabilné | žiadna výrazná zmena alebo protichodné signály | šablóna sa len doplní |
| zhoršenie | tempo o ≥ 10 % pomalšie alebo rozptyl o ≥ 15 % vyšší | prah sa môže uvoľniť, ale najviac o 25 % za cyklus (`THRESHOLD_MAX_GROWTH`) |

Tempo je zmena mediánu času medzi stlačeniami (DD). Stabilita je zmena typického rozptylu príznakov, opravená na rôzny počet pozorovaní (nové sady sú malé a rozptyl by inak vychádzal systematicky nižší). Ak by zhoršenie vyžadovalo väčšie uvoľnenie prahu než povoľuje strop, prah zostane na stropu a UI odporučí novú registráciu. Strop bráni tomu, aby sa systém postupným „zhoršovaním" vyprázdnil.

**Demo bez čakania mesiaca:** spusti server s `ALLOW_TEST_MODE=1`, po prihlásení otvor admin panel a pri používateľovi klikni na „Posunúť o mesiac". Pri ďalšom prihlásení sa ponúkne obnova. Pri demo zrýchlení píš zhruba o 10 % rýchlejšie, nie dvojnásobne: výrazne iné písanie ochrana proti otráveniu šablóny odmietne.

**Parametre:** `TEMPLATE_REFRESH_DAYS`, `REFRESH_SAMPLES`, `REFRESH_MAX_RATIO`, `THRESHOLD_MAX_GROWTH`, `THRESHOLD_MAX_SHRINK`.

**Limity (do diskusie):**
- Obnova je zatiaľ len ponuka. Systém nevynucuje, že ju používateľ dokončí, v admin paneli je vidno len „po termíne".
- Automatická nová registrácia pri veľkom drifte nie je implementovaná, len odporúčanie.
- Mesačné obnovy sa dajú vyhodnotiť až z reálnych dát za niekoľko mesiacov. Simulovaný posun času ukazuje mechanizmus, nie skutočný vývoj písania.
- `evaluate.py` kalibruje z prvých `n` enrollment vzoriek, takže obnovy (`cycle > 0`) výsledky FAR/FRR neovplyvňujú.
