"""Zbierka viet na prepis. Bežné vety, aké človek naozaj povie; bez diakritiky a interpunkcie.

Používa sa, keď nie je nastavený LLM (alebo neodpovedá), a z nej sa berú aj príklady do promptu.
Každá veta musí prejsť `sentences.is_valid`; kontroluje to test.
"""

SK = """
dnes rano som si kupil cerstvy chlieb v pekarni
po praci pojdeme spolu na kratku prechadzku
vlak do kosic mal dnes dvadsat minut meskanie
na obed sme mali polievku a vyprazany syr
zajtra musim skoro vstat lebo mam skusku
moja sestra studuje medicinu v martine
v sobotu pojdeme s kamaratmi na hokej
zabudol som si doma kluce od kancelarie
vonku uz niekolko dni bez prestavky prsi
tento film som videl uz aspon trikrat
stara mama pecie najlepsie kolace na svete
novy telefon ma ovela lepsiu bateriu
cez leto chceme ist na dovolenku k moru
autobus odchadza kazdu hodinu z namestia
vcera vecer sme dlho hrali spolocenske hry
kava bez cukru mi chuti ovela viac ako sladka
nas pes sa boji burky a ohnostrojov
v kniznici je vzdy ticho a prijemne teplo
potrebujem si kupit nove topanky na zimu
moj otec kazde rano cita noviny pri kave
dnes je vonku krasne slnecne pocasie
internet nam doma zase nefunguje cely den
na vikend planujeme vylet do vysokych tatier
ucitel nam zadal velmi tazku domacu ulohu
v obchode uz nemali ziadne cerstve ovocie
moja mama pracuje ako sestra v nemocnici
po veceri si rad pozriem dobry serial
tento rok chcem precitat aspon desat knih
bicykel mam v pivnici uz od minulej jesene
susedia maju v zahrade velku staru jablon
pocitac sa mi dnes dvakrat sam restartoval
na stanici sme cakali skoro celu hodinu
brat sa uci hrat na gitare uz druhy rok
v zime sa rad lyzujem na strmych svahoch
dnes vecer varim cestoviny s paradajkami
prednaska z matematiky bola dnes zrusena
kamarat mi pozical zaujimavu knihu o vesmire
moj stary notebook je uz naozaj velmi pomaly
rano som nestihol ranajky ani autobus
deti sa na dvore hraju s novou loptou
v piatok poobede mame poradu celeho timu
heslo do systemu si musim zmenit kazdy mesiac
na trhu predavaju domaci med a ovci syr
cesta do skoly mi trva asi pol hodiny
minuly tyzden sme malovali celu kuchynu
moja kamaratka sa stahuje do ineho mesta
v lete chodime plavat na jazero za dedinou
dnes som pracoval z domu a bolo to prijemne
potrebujem opravit pokazenu pracku v kupelni
na narodeniny som dostal nove sluchadla
nas tim vyhral zapas v poslednej minute
rad pocuvam hudbu ked sa ucim na skusky
v meste otvorili novu taliansku restauraciu
zajtra ma byt chladno a velmi veterno
musim si nabit telefon lebo je skoro vybity
moja babka byva v malom dome pri lese
na chodbe niekto nechal otvorene okno
kolega mi pomohol opravit chybu v programe
vecer pojdem behat do parku pri rieke
tento tyzden mam vela prace a malo casu
v pondelok zacina novy semester na univerzite
mama ma poprosila aby som vyniesol smeti
do prace chodim kazdy den elektrickou
nase mesto ma krasne historicke namestie
po dlhej zime sa konecne vsetci tesime na jar
na stole som nasiel odkaz od mladsej sestry
chcel by som sa naucit dobre po spanielsky
v noci ma zobudil hlasny zvuk z ulice
toto leto bolo naozaj horuce a velmi suche
pred skuskou som bol trochu nervozny
na ranajky mam najradsej vajicka a cierny caj
v kine davaju novy film o ceste do vesmiru
moja sestra pecie kazdu nedelu jablkovy kolac
lekar mi odporucil viac pohybu a spanku
nakup som zaplatil kartou cez telefon
nas ucitel vie zaujimavo rozpravat o historii
kvety na balkone treba kazdy den polievat
dnes mi prisiel balik ktory som cakal tyzden
v zahrade nam dozrievaju sladke ceresne
stary most cez rieku prave opravuju
rodicia mi volaju kazdy vecer po praci
na horach uz napadol prvy tohtorocny sneh
projekt musime odovzdat do konca mesiaca
moj brat pracuje ako programator v kosiciach
cez prestavku si vzdy kupim nieco sladke
v nedelu rano chodime na dlhu prechadzku
klavesnica na mojom pocitaci je uz stara
kazdy clovek ma svoj vlastny rytmus pisania
siet v nasej skole je dnes velmi pomala
studenti dnes pisali test z bezpecnosti
po dazdi sa na oblohe objavila duha
v obyvacke mame novu pohodlnu sedacku
kamarati ma pozvali na oslavu do chaty
rano pijem velky pohar vody s citronom
moja macka cely den spi na parapete
na letisku sme cakali na oneskorene lietadlo
dedko mi casto rozprava pribehy z mladosti
v praci sme dnes mali skolenie o bezpecnosti
tuto pesnicku pocuvam stale dookola
polievka je este horuca tak chvilu pockaj
na parkovisku pred domom nie je volne miesto
moj sused je velmi mily a ochotny clovek
dnes vecer sa pozerame na futbal s priatelmi
vianocny stromcek zdobime vzdy cela rodina
v lese sme nasli plny kos cerstvych hub
batoh mam uz zbaleny na zajtrajsi vylet
kazde rano idem do prace okolo parku
ucim sa programovat v jazyku python
obed v skolskej jedalni bol dnes celkom dobry
mesto v noci vyzera uplne inak ako cez den
nova spoluziacka prisla k nam z presova
dvere do laboratoria su zamknute na kartu
zoznam uloh na dnes je nekonecne dlhy
v zime si rad dam horuci caj s medom
na koncert sme isli vlakom az do viedne
moj mladsi brat zbiera stare mince a znamky
pred spanim si vzdy precitam par stran
dnes som stretol stareho kamarata zo skoly
voda v jazere bola prekvapivo tepla
na skusku sa ucim uz treti den po sebe
nase auto je v servise uz druhy tyzden
chlieb s maslom a medom je stara klasika
v praci pouzivam dva monitory naraz
rano byva na cestach husta hmla a namraza
hory su na jesen plne krasnych farieb
moja teta ma maly obchod s kvetmi v centre
server bol cez noc dve hodiny nedostupny
kazdy pouzivatel ma svoje vlastne heslo
po obede si doprajem kratky odpocinok
dnes sa mi nechce vobec nic robit ani ucit
kavovar v kancelarii je zase pokazeny
dazdnik som si zabudla v rannom vlaku
na zahrade sme zasadili paradajky a papriku
kniznica sa v sobotu zatvara uz na obed
do skoly chodi na bicykli aj v zime
poradu presunuli na zajtra poobede o druhej
subor som ulozil ale uz neviem kam presne
deti postavili pred domom velkeho snehuliaka
do tohto receptu treba muku maslo a dve vajcia
muzeum je prvu nedelu v mesiaci zadarmo
telefon mi spadol na zem a praskol displej
do noci sme sa rozpravali o starych casoch
v tlaciarni sa dnes zase minul papier
posli mi prosim spravu do piatku do obeda
pekaren na rohu ulice vzdy krasne vonia
nasi susedia maju velmi priatelskeho psa
pocas silnej burky vypadla elektrina
""".split("\n")

EN = """
i bought fresh bread at the bakery this morning
the train to london was twenty minutes late
we are going for a short walk after work
my sister studies medicine at the university
it has been raining for several days now
i forgot my office keys at home again today
the new phone has a much better battery
we want to spend the summer by the sea
the bus leaves from the square every hour
my father reads the newspaper every morning
the weather outside is sunny and warm today
our internet at home is not working again
the teacher gave us very difficult homework
my mother works as a nurse at the hospital
i like to watch a good series after dinner
this year i want to read at least ten books
my computer restarted itself twice today
we waited at the station for almost an hour
my brother has played guitar for two years
tonight i am cooking pasta with tomatoes
the math lecture was cancelled this morning
a friend lent me a great book about space
my old laptop is getting really slow lately
i missed both breakfast and the bus today
the children are playing in the yard outside
we have a team meeting on friday afternoon
i have to change my password every month
they sell local honey and cheese at the market
the way to school takes about half an hour
last week we painted the whole kitchen
my friend is moving to another city soon
in summer we swim in the lake near the village
i worked from home today and it was nice
i need to fix the broken washing machine
i got new headphones for my birthday
our team won the game in the last minute
i like listening to music while i study
a new italian restaurant opened in town
tomorrow should be cold and very windy
i must charge my phone because it is dying
my grandmother lives in a small house by the woods
someone left the window open in the hall
a colleague helped me fix a bug in the code
this evening i will go running in the park
i have a lot of work and little time this week
the new semester starts on monday morning
my mom asked me to take out the trash
our town has a beautiful old main square
after a long winter we look forward to spring
i found a note from my roommate on the table
i would like to learn to speak spanish well
a loud noise from the street woke me up
this summer was really hot and very dry
i was a little nervous before the exam
for breakfast i like eggs and a cup of tea
the cinema is showing a new film about space
my sister bakes an apple pie every sunday
the doctor told me to sleep and move more
i paid for the groceries with my phone
our teacher tells great stories about history
the flowers on the balcony need water daily
the package i waited for finally arrived
sweet cherries are ripening in our garden
they are repairing the old bridge right now
my parents call me every evening after work
the first snow has fallen in the mountains
we must hand in the project by the end of month
my brother works as a programmer in the city
i always buy something sweet during the break
on sunday mornings we go for a long walk
the keyboard on my computer is quite old
every person has their own typing rhythm
the network at our school is very slow today
the students wrote a test on security today
a rainbow appeared in the sky after the rain
we have a new comfortable sofa at home
my friends invited me to a party at the cabin
i drink a large glass of water every morning
my cat sleeps on the windowsill all day
we waited at the airport for a delayed plane
grandpa often tells stories from his youth
we had security training at work today
i keep listening to this song on repeat
the soup is still hot so wait a moment
there is no free parking spot near the house
my neighbor is a very kind and helpful man
we are watching football with friends tonight
the whole family decorates the tree together
we found a basket of mushrooms in the forest
my backpack is packed for the trip tomorrow
every morning i walk to work through the park
i am learning to write programs in python
lunch at the school canteen was quite good
the city looks completely different at night
the door to the lab opens only with a card
my list of tasks for today is endless
in winter i enjoy hot tea with honey
we took the train to the concert in vienna
my younger brother collects old coins
i always read a few pages before sleeping
today i met an old friend from school
the water in the lake was surprisingly warm
our car has been in the garage for two weeks
bread with butter and honey is a classic
i use two monitors at work at the same time
there is thick fog on the roads this morning
the mountains are full of colors in autumn
my aunt owns a small flower shop downtown
the server was down for two hours last night
every user has their own secret password
after lunch i take a short rest on the couch
i do not feel like doing anything today
the coffee machine in the office is broken
she left her umbrella on the morning train
we planted tomatoes and peppers in the garden
the library closes early on saturday evening
he rides his bike to school even in winter
the meeting was moved to tomorrow afternoon
i saved the file but forgot where i put it
the kids built a snowman in front of the house
this recipe needs flour butter and two eggs
the museum is free on the first sunday
my phone fell on the floor and the screen broke
we stayed up late talking about old times
the printer ran out of paper again today
please send me the report before friday noon
the bakery on the corner smells wonderful
our neighbors have a very friendly dog
the lights went out during the heavy storm
i need a new pair of shoes for the winter
""".split("\n")

CORPUS = {
    "sk": [line.strip() for line in SK if line.strip()],
    "en": [line.strip() for line in EN if line.strip()],
}
