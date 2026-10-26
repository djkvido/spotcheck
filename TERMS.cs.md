# Podmínky použití SpotCheck

Verze 2, 26. října 2026. Anglická verze: [TERMS.md](TERMS.md).

SpotCheck tě požádá o souhlas s těmito podmínkami při instalaci a při prvním spuštění. Pokud s nimi nesouhlasíš, SpotCheck neinstaluj a nepoužívej.

## 1. Co je SpotCheck

SpotCheck je hobby open-source projekt, který vydává djkvido na https://github.com/djkvido/spotcheck. Pomáhá udržovat pořádek v místní hudební knihovně: načte seznamy skladeb z playlistů, které mu zadáš, najde skladby ve veřejně dostupných zdrojích, každý soubor porovná s oficiální ukázkou a změří jeho skutečnou kvalitu zvuku. Jeho převodník převádí zvukové a video soubory mezi formáty přímo v tvém počítači.

SpotCheck je svobodný software pod licencí GNU General Public License verze 3 nebo novější (GPL). Kopírování, úpravy a šíření SpotChecku se řídí licencí GPL. Tyto podmínky popisují, za co při používání odpovídáš ty. Neomezují žádné právo, které ti dává GPL.

## 2. Sestaveno z open-source softwaru

SpotCheck je sestavený z veřejně dostupného open-source softwaru, který používá podle licencí jeho autorů: Python, spotDL, yt-dlp, FFmpeg s knihovnami LAME, Opus a Chromaprint, Deno, FastAPI, Mutagen a další. Úplný seznam s verzemi a licencemi je v souboru `THIRD_PARTY_NOTICES.txt` ve složce instalace a v [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), texty licencí jsou ve složce `licenses`. Autoři těchto programů se SpotCheckem nejsou nijak spojeni a nepodporují ho.

Instalátory sestavuje GitHub Actions přímo z veřejného zdrojového kódu. Zdrojový kód každého vydání, včetně úplného zdrojového kódu přibaleného FFmpeg, je přiložený k vydání.

## 3. Tvoje odpovědnost

SpotCheck je nástroj. O tom, co dělá, rozhoduješ ty. Nestahuje zvuk ze Spotify, neobchází DRM ani jinou ochranu proti kopírování a žádnou hudbu nehostuje ani nesdílí.

Používáním SpotChecku potvrzuješ, že:

- ho používáš pro vlastní osobní a nekomerční potřebu;
- zpracováváš jen obsah, který smíš podle práva své země stáhnout, zkopírovat a uchovávat;
- dodržuješ podmínky služeb, které přes něj používáš, například Spotify, YouTube, SoundCloud, Bandcamp, Deezer a Apple;
- ho nepoužíváš ke sdílení, šíření ani prodeji obsahu chráněného autorským právem.

Za to, co pomocí SpotChecku stáhneš a co se soubory uděláš, odpovídáš výhradně ty.

## 4. Připojení a stahování

SpotCheck běží na tvém počítači a přijímá spojení jen z něj. Nemá účet, analytiku ani telemetrii a jeho autorům o tobě nic neposílá. Připojuje se k:

- službám, na kterých jsou tvoje playlisty a skladby (Spotify, YouTube, YouTube Music, SoundCloud, Bandcamp), aby načetl seznamy skladeb a stáhl zvuk;
- Deezeru a iTunes Search API kvůli oficiálním ukázkám a metadatům a ke stránkám s texty písní, které používá spotDL (Genius, AZLyrics, Musixmatch);
- PyPI (pypi.org), aby na stránce Závislosti ukázal dostupné aktualizace svých součástí, a aby stáhl aktualizaci yt-dlp, když o ni požádáš;
- GitHubu (github.com) a stránkám, ze kterých se distribuuje FFmpeg a Deno, jen když spouštíš SpotCheck ze zdrojového kódu a požádáš ho o instalaci FFmpeg nebo Deno;
- GitHubu (github.com) ve Windows nebo ffmpeg.martin-riedl.de v macOS, jen když převodník požádáš o stažení rozšířených kodeků.

Aplikace nikdy sama od sebe neaktualizuje ani neinstaluje žádnou součást. Vždy řekne, co a odkud se chystá stáhnout, a začne až po tvém potvrzení. Když spouštíš SpotCheck ze zdrojového kódu, jeho instalační a spouštěcí skripty stáhnou potřebné balíčky Pythonu z PyPI a pokud chybí, také Python, FFmpeg a Deno z jejich oficiálních distribučních kanálů. Stažené součásti jsou software třetích stran pod vlastními licencemi.

## 5. Tvoje soubory

SpotCheck nikdy nezmění zvuk existujícího souboru bez zálohy a soubor nahradí jen po tvém potvrzení. Přesto si hudbu, na které ti záleží, zálohuj i sám.

## 6. Bez záruky

SpotCheck se poskytuje „tak, jak je“, bez jakékoli záruky, jak popisují články 15 a 16 licence GPL. V rozsahu, který dovoluje zákon, jeho autoři neodpovídají za žádnou škodu, ztrátu dat, nároky třetích stran ani jiné následky jeho použití.

## 7. Ochranné známky

SpotCheck není spojený se společnostmi Spotify, YouTube, Google, SoundCloud, Bandcamp, Deezer ani Apple, ani jimi podporovaný či sponzorovaný. Jejich názvy jsou ochranné známky příslušných vlastníků a používají se jen k popisu toho, s čím SpotCheck pracuje.

## 8. Změny

Když se tyto podmínky změní, SpotCheck tě před dalším použitím požádá o souhlas s novou verzí. Aktuální verze je vždy na https://github.com/djkvido/spotcheck/blob/main/TERMS.cs.md. Pokud se česká a anglická verze liší, platí anglická verze.
