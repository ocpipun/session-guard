<!-- SESSION-GUARD: 2.0 | component: doc-readme-tr | version: 2.0.0 -->
# Session Guard

**Sürüm 2.0** · Açık kaynak, **MIT lisansı** · [English](README.md) · Tasarım: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Session Guard, Claude Code için bir eklentidir. Claude'un göremediği şeyleri (bağlam büyüklüğü, mola, kota) ölçer,
Claude'a ne zaman devredeceğini ve duracağını söyler, biten oturumları kilitler. 2.0 ayrıca her proje, oturum ve
göreve bir kimlik verir, hangi görevin kime ait olduğunu korur ve her devirde yarım kalan işi saklar; böylece
paralel ya da sonraki oturumlar birbirinin işini ezmez, kaybetmez.

## 1. Bağlam eşikleri
Session Guard her mesajdan ve her araç çağrısından sonra bağlam büyüklüğünü oturum kaydından okur; Claude'a yalnız
bir eşik geçildiğinde talimat verir. Claude bir eşiğe hiçbir zaman erken davranmaz.

| Bağlam | Normal mod | Uzun mod |
|---|---|---|
| 250k | bildirim | bildirim |
| 300k | oturumun sahip olduğu her görevin devri; sonra yeni görev yok, yalnız sorular | bildirim |
| 350k | hedef durma: yeni oturum açın | — |
| 400k | kesin durma, oturum kilitlenir | — |
| 700k | — | bildirim; görev notları güncel tutulur, henüz devir yok |
| 800k | — | oturumun sahip olduğu her görevin devri; sonra iş yok, yalnız basit sorular |
| 900k | — | kesin durma, oturum kilitlenir |

- **Uzun mod:** `start long mode` yazın. Yalnız o oturum için geçerlidir, kapatılamaz.
- **İkinci devir yok:** 300k devrinden (normal) ya da 800k devrinden (uzun) sonra `start long mode` ve
  `finish session` reddedilir; böylece bir oturum birbiriyle çelişen iki devir notu bırakmaz. Oturum ölçümü o
  devirle birlikte yapılır.
- Her bildirim kullanıcıya kendi dilinde tek satır olarak gelir ("…eşiği geçildi… *Bu bir Session Guard bildirimidir.").

## 2. Molalar
Normal modda, Claude'un son cevabından bu yana 1 saatten fazla geçmişse:
- bağlam 175k üstündeyse mesaj **çalıştırılmaz**. Session Guard mesajı kelimesi kelimesine her görevin devir
  notuna yazar, Claude devreder, oturum kilitlenir;
- 175k ya da altındaysa tek satır hatırlatma, sonra mesaj çalışır.

Claude'un kendi arka plan işini beklemek mola sayılmaz. Uzun modda mola kuralı yoktur; böyle bir moladan hemen
sonra yazılan "start long mode" kuralı atlatmaz (oturum önce devreder).

**Mola bekçisi (bilgisayar, uygulama açık).** Claude bir cevabı bitirince arka planda bir bekçi başlar. 55 dakika
içinde mesaj gelmezse (normal mod, bağlam 175k üstünde) Claude'u uyandırır; Claude önbellek henüz geçerliyken (bir
saatte biter) devreder, oturum Claude çalışmaya başlayınca kilitlenir. Uygulama ya da bilgisayar kapalıysa hiçbir şey
çalışamaz: mola kuralı yukarıdaki gibi sıradaki mesajda devreye girer. Bulutta makine birkaç dakika boşta kalınca
duraklatılır (resmi belge), orada da bekçi çalışmaz. **Devretmeden kapananlar:** Claude yeniden başlayınca
(bilgisayarda herhangi bir oturum), devretmeden kapanmış (bilgisayar ya da uygulama kapalıydı) ve mola kuralı ya da
bağlam devir eşiği oluşmuş konuşmalar uygulamanın oturum mesajlarıyla uyandırılır: yalnız devir yaparlar, sonra
kilitlenirler. Kısa bir kesinti (1 saatten az) hiçbir şeyi uyandırmaz. **Devirden önce kilit yok:** mola kuralı, kesin
sınırlar ve mola bekçisi oturumu önce devrettirir (yalnız devir komutları çalışır), sonra kilitler.

**Mola ölçümü (isteğe bağlı, varsayılan kapalı).** Moladan sonra devam etmenin size maliyetini görmek için
Claude'dan `sg breaks on` komutunu çalıştırmasını isteyin. Bu bilgisayardaki bütün oturumlar ve projeler için
geçerlidir, `sg breaks off` ile kapanır. 1 saatten uzun her aradan sonra Claude o mesajın başında ve sonunda kotanızı
okur (yalnız masaüstü uygulamasında); aradan önceki son okumayla birlikte
`~/.claude/session_guard_v2/break_measurements.jsonl` dosyasına tek satır yazılır. Hiçbir veri bilgisayarınızdan
çıkmaz. `sg breaks show` satırları listeler. Her okuma birkaç token harcar.

## 3. Oturumu bitirme
`finish session` (mesajın başında, ardından virgül ya da nokta; ya da mesajın sonunda): Claude başka hiçbir şey
yapmaz, her görevi devredilebilir hâle getirir, devir notlarını yazar, oturum ölçümünü alır (Session Guard'ın
`report` satırı; uygulama okuyabiliyorsa 5 saatlik ve haftalık kota) ve oturum kilitlenir. Devirler yazılıp
yayımlanana kadar Claude çalışmaya devam ettirilir (en fazla iki kez).

## 4. Kota (bilgisayar)
5 saatlik kota %95'e ya da haftalık kota %99'a geldiyse ve sıfırlanmaya 45 dakika ya da daha fazla varsa: Claude
yeni iş başlatmaz, her görevin nerede kaldığını yazar, 15 dakika sonra oturum sıfırlanmaya kadar kilitlenir.
Masaüstü uygulamasında uygulamanın kullanım aracından, terminalde durum çubuğundan okunur (token harcamaz).
Bulutta kota okunamaz (2026-11-03'te ölçüldü: durum çubuğu orada çalışmıyor, kullanım aracı yok); biten kotayı
uygulamanın kendisi bildirir.

## 5. Oturum kilitleri
Kilitli oturuma mesaj ulaşmaz; kullanıcıya nedeni söylenir. Türleri:
- bağlam sınırı, oturumu bitirme, mola devri;
- kota (sıfırlanana kadar);
- devredildi (bütün görevlerini daha yeni bir oturum aldı);
- proje 2.0'a geçmeden önce başlamış konuşma;
- yanlış yer (bilgisayar projesi bulutta açıldı ya da tersi);
- yeniden başlat (Session Guard kurulmadan ya da güncellenmeden önce açılmış oturum: Claude'u tamamen kapatıp açın).

24 saatlik gözcü, bir tur ya da bir arka plan işi bir gündür sürüyorsa kullanıcıya söyler.

## 6. Projeler, oturumlar ve görevler
- **Kimlikler:** proje `PRJ-XXXXXXXX`, oturum `S-YYYYAAGG-xxxxxx` (`$SG_SESSION_ID` içinde de), görev `TASK-001`,
  alt görev `TASK-001-1`, `TASK-001-2-1`. Kimlikler hiçbir zaman yeniden kullanılmaz.
- **Sahiplik:** görevi yalnız sahibi değiştirir. Boştaki görev (sahibi yok, sahibi bitti ya da kilitlendi, ya da
  devir bekliyor) alınabilir; aktif bir oturumun görevi yalnız kullanıcının onayıyla. Sahiplik geçmişi silinmez.
- **Sınırlar:** projede aynı anda çalışan her ajan için en fazla 10 normal ve 3 acil ana görev (acil: gerekçeli
  ve kullanıcı onaylı; iki ajan: 20 + 6), 50 sıradaki görev, 50 fikir (geçici kimlik IDEA-xxx; fikir göreve
  dönüşünce yerini görev kimliği alır). Çok adımlı bir projeyi ya da toplu bir mesajı Claude bu biçime kendisi
  ayırır. Alt görevler, kurulum, migration ve kural-betiği işleri sayılmaz. Sınır aşılırsa Claude kullanıcıya
  önce bekleyen işleri bitirmesini ya da bırakmasını söyler.
- **Önce acil iş:** acil görev listenin başına geçer ve ajanına aittir: sürerken o ajan yeni görev almaz (yeni
  istekler kaybolmasın diye fikir olarak kaydedilir); diğer ajanlar çalışmaya devam eder.
- **Alt ajana yalnız alt görev:** alt ajan (Agent aracı) yalnız oturumun sahip olduğu bir görevin alt göreviyle
  başlatılır, alt görev kimliği isteminde yazar; Session Guard başka başlatmaları reddeder, alt ajan ana görev
  açamaz. Alt ajanlar ve alt görevler sayılmaz; alt ajan kendi alt görevini devredip kapatabilir.
- **Alt oturum ajanları:** bir oturumdan başlatılan oturum, aynı kurallarla çalışan ayrı bir ajandır. Türü
  başlarken istemdeki `SG-PARENT: <kimlik> bound` ya da `SG-PARENT: <kimlik> independent` satırıyla belirlenir:
  - *Bağlı* (`<üst>-B<n>`): üst oturumun işine yardım eder (test, işin bir parçası), yalnız üst oturumun
    görevlerinin alt görevleri üzerinde çalışır. Üst oturum devretmeden önce `sg handoff children` bağlı alt
    oturumları ve onlara gönderilecek mesajı verir; üst oturum devir notunu onlarınkinden sonra yazar, birlikte
    kilitlenirler.
  - *Bağımsız* (`<üst>-I<n>`): kendi ana görevleri vardır, üst oturumun işini almaz. Üst oturum durunca Session
    Guard ona işini devredilebilir hâle getirmesini (dal ve PR devir notunda) söyler, o da çalışmayı sürdürür.
  - Türler birbirinin işini alamaz. Bir devri devralan oturum alt oturum değil, devam oturumudur.
- **Sıkıştırma ve çatallama:** Claude Code konuşmayı sıkıştırınca Session Guard kuralları ve STATUS'u yeniden
  verir, küçülen bağlamı ölçer, tek seferlik bildirimler yeniden çalışır. Çatallanan oturum (/fork, /branch) yeni
  bir oturum olur; modu (uzun mod dahil) ve bağlamı korunur.
- **Aynı işi iki kez yapmamak:** kullanıcı yeni iş verince (proje başında toplu mesaj ya da sonradan yeni
  görevler) `sg task check` süren, acil, sıradaki bütün görevleri ve fikirleri, yeni işlerle ortak kelimesi olan
  biten işleri (2.0 öncesi işler kelimeyle bulunur) ve neredeyse aynı olanları listeler. Claude bunları
  karşılaştırır; çakışma, çelişki ya da neredeyse aynı iş varsa kullanıcıya kendi dilinde açıklar; neredeyse
  aynısı reddedilir.
- **Bekleyen işler STATUS dışında:** sıradaki görevler `docs/TASK_BACKLOG.md`, fikirler `docs/IDEA_TASKS.md`
  dosyasındadır; ikisi de üretilir, her kimlik ayrı bölümdür, kayıtlar satır numarasıyla değil kimlikle değişir.
  STATUS yalnız sayılarını gösterir, böylece kısa kalır. IDEA_TASKS.md'de "## Add an idea" başlığı altına (ya da
  STATUS'ta bir "## Notes" başlığı altına) yazılan her madde kimlikli bir fikre dönüşür ve mevcut işlerle
  karşılaştırılır.
- **Ne zaman ne okunur:** oturum başında CLAUDE.md ve STATUS.md; yeni işte yalnız yukarıdaki denetim; arşive
  yalnız gerekince, aramayla bakılır (2.0 kayıtları kimlikle, 2.0 öncesi kayıtlar kelimeyle).
- **Gerektiğinde yeni kimlik:** kaldığı yerden devam aynı kimlikle; hatalı biten işi yeniden yapmak ya da biten
  işe özellik eklemek eskisine bağlı yeni görevdir. Alt görev bitince üst görev kendiliğinden bitmez.
- **Güvenli paralel yazma:** proje başına bir kilit, kilit içinde taze okuma, sürüm denetimi; görev kaydının elle
  değiştirilmesi reddedilir, STATUS.md'deki görev bölümü elle bozulursa kayıttan yeniden oluşturulur. Her
  oturum yalnız kendisi olarak çalışır: başka bir oturumun kimliğiyle verilen `sg` komutu reddedilir.
- **Aynı konuşma, yeni kimlik:** Claude Code'un oturum kimliği değişebilir (masaüstü uygulamasının konuşmayı yeni
  kimliğe kopyaladığı görüldü; bulut makinesi yeniden kurulabilir). Session Guard konuşmayı mesaj kimliklerinden,
  hem bu bilgisayarda hem projenin kendi kaydında yeniden bulur; SESSION-ID, görevler, uzun mod ve kilitler korunur.
  Bulutta bu kayıtlar GitHub'da `sg-state` dalında da tutulur (Session Guard'ın kendi dalı: çalışma dalınıza ondan
  commit gelmez); böylece yeniden kurulan bulut makinesi onları yeniden bulur.
- **Oturum başı:** yalnız STATUS.md okunur. Claude önce devredilmiş ama henüz alınmamış işi bitirmeyi önerir
  (birden fazlaysa birini önerir, sen seçersin). Bekleyen iş yoksa ve yeni iş istersen: sıradaki görev
  (TASK_BACKLOG.md); sıra boşsa bir fikir (IDEA_TASKS.md), seninle sorularla tasarlanır, sıraya konur, sonra
  başlatılır. Backlog ve fikirler yalnız o zaman okunur; arşiv yalnız gerektiğinde, aranarak.
- **Devir olmadan iş alınmaz:** bir görev ancak sahibi onu devrettiyse alınabilir. Devreden oturum yeni görev
  almaz, devrettiği işi geri alamaz; devretmeden kapanan ya da kilitlenen oturumun görevleri, o konuşmayı açıp
  devrettirene kadar onda kalır (ya da devralmayı açıkça onaylarsınız).
- **Görevde kim çalıştı:** STATUS'ta her görevin altındaki "Agents" satırı, görevi sırayla alan bütün oturumları
  tarihleriyle ve devredildiği yerde "handed off" ile gösterir.
- **Devir notları:** işi devreden her oturum tek, kısa bir genel not yazar (`sg handoff note`): imzası, tarih, kota
  ölçümleri ve genel değerlendirmesi. Notlar STATUS'un en altında, oturum başına bir tane durur (birden fazla ajan:
  birden fazla not). Bir notun devrettiği işin hepsi alınıp daha yeni bir not yazılınca, eski not arşive tek satır
  olarak gider; böylece büyük projelerde de STATUS kısa kalır.
- **Biten görevler:** her görev tek cümlelik bir sonuçla kapanır (`sg task done <id> --summary "..."`);
  COMPLETED_TASKS.md'ye tek satır yazılır: tarih, kimlik, başlık, sonuç, ajanlar. Bütün görevleri biten ajan, devir
  notu yerine bu dosyadan bir "proje durumu" notu yazar.

## 7. İşi saklayan devirler
Her görevin kendi devir notu vardır: proje, görev ve oturum kimlikleri, oturumun üst ve alt oturumları, son
geliştirici, durum, yapılanlar, bekleyenler, sahip, dal, pull request, teknik notlar, kullanıcının komutu kelimesi
kelimesine (mola) ve **yarım kalan işin nerede olduğu**. Claude notu yazmadan önce elindeki küçük adımı bitirir;
yarım yazılmış hiçbir şey bırakmaz.

| | Bilgisayar | Bulut |
|---|---|---|
| İş nereye gider | proje klasöründe kalır; devir notu kaydedilmemiş dosyaları listeler | görevin kendi yan dalına (`sg/<görev-kimliği>`) commitlenip gönderilir, push doğrulanır |
| Yan dal | yalnız kullanıcı isterse ya da Claude'un önerisini onaylarsa | her zaman (bulut çalışma alanı oturumla birlikte gider) |
| Sonda kaydedilmemiş dosya | — | reddedilir: değişen her dosya bir göreve kaydedilmeden devir bitmez |
| Sonraki oturum | aynı klasörü açar, görevi alır | hangi yan dallarda devir olduğu söylenir, birini birleştirir (`sg handoff adopt`), görevi alır |

Yan dal birleştirilirken Session Guard'ın kendi dosyalarındaki çakışmalar kendiliğinden çözülür (görevler
birleştirilir; iki dalda aynı kimliği almış iki farklı göreve yeni kimlik verilir). Projenin kendi dosyalarında
çakışma olursa birleştirme durur ve hiçbir şey değişmez. `sg/<görev-kimliği>` yan dalları Session Guard'ın değil, projenin kendi yarım işini (kodunu) taşır. Git, birleştirilen dalı kendiliğinden silmez (GitHub, depo ayarlarında "Automatically delete head branches" açıksa birleştirilen pull request'in dalını siler); bulut oturumu ise hiç dal silemez (GitHub vekili silmeyi reddeder). Birleştirilen yan dalları GitHub'da elle silin. Öneri: GitHub'da depo Settings > General > Pull Requests > "Automatically delete head branches" ayarı açılırsa, pull request'i birleştirilen yan dal kendiliğinden silinir (GitHub belgesi: silinen dal, kapanmış pull request sayfasındaki "Restore branch" ile geri yüklenebilir). Session Guard her oturum başında hangi yan dalların birleştirildiğini ve silinebileceğini kullanıcıya söyler. Ana dalı geri almak (reset ya da revert) başka hiçbir dalı değiştirmez: git'te her dal kendi başına bir işaretçidir. Daha yeni bir oturum görevi alınca, açık görevi kalmayan
eski oturum kilitlenir. Alınan devir notları `STATUS_ARCHIVE.md`'ye geçer.

## 8. Proje dosyaları
| Dosya | Görevi |
|---|---|
| `CLAUDE.md` | kalıcı kurallar ve kararlar |
| `docs/STATUS.md` | güncel durum; görev bölümünü Session Guard üretir; satır sınırı yok (sınır görev sayısıdır) |
| `docs/STATUS_ARCHIVE.md` | geçmiş; yalnız eklenir; oturum başında okunmaz, yalnız aranır |
| `docs/TASK_BACKLOG.md` | sıradaki görevler (üretilir, her TASK-ID ayrı bölüm) |
| `docs/IDEA_TASKS.md` | fikirler (üretilir, her IDEA-ID ayrı bölüm) |
| `docs/COMPLETED_TASKS.md` | biten her görev için tek satır: tek cümle sonuç, ajanları |
| `.claude/session-guard2/store.json` | görev kaydı; projeyle birlikte commitlenir |

## 9. Bilgisayar ve bulut
Bir proje ya bilgisayarda ya bulutta yürütülür, ikisinde birden asla. Git kullanan bilgisayar projesinde, onun
bulut oturumlarını engelleyen küçük bir hook bulunur. Bulut projesi Session Guard'ın bir kopyasını
`.claude/session-guard2/bin/` içinde taşır ([CLOUD.md](CLOUD.md)); bilgisayarda eklenti onun oturumlarını kilitler.

| Yer | Çalışır mı | Not |
|---|---|---|
| Masaüstü uygulaması, terminal (bilgisayar) | evet | her şey; Windows'ta denendi |
| Remote Control (telefondan ya da tarayıcıdan bilgisayar oturumunu yönetmek) | evet | oturum bilgisayarda çalışır (belgede yazıyor) |
| Bulut oturumları (claude.ai/code; tarayıcı, telefon ya da masaüstü uygulamasından) | depo başına | depo kopyası; kota koruması yok; devirler için yan dallar |
| macOS / Linux | beklenir | onlar için yazıldı, denenmedi |

## 10. Kurulum, güncelleme, migration
- Kurulum ya da güncelleme yalnız `session-guard-executive` adlı Claude proje klasöründen ([INSTALL.md](INSTALL.md)).
- 2.0, 1.x ile aynı eklenti kimliğini kullanır; güncelleme 1.x'in yerini alır. 1.x kurulu kaldıkça 2.0 pasif
  bekler (asla iki koruyucu birden çalışmaz). 1.x'in kilitleri korunur; genel kural bölümü 2.0'ınkiyle değişir.
- Kurmadan önce bütünlük listesi (`manifest.json`: her dosyanın kimliği, sürümü, yolu, türü, SHA-256 özeti,
  bağımlılıkları ve `SESSION-GUARD: 2.0` imzası) denetlenir; 1.x'e ait olanla 2.0'ın değiştireceğini gösteren bir
  tablo çıkar. Tek onay.
- **Migration**, her seferinde bir proje, her adım onaylı: projeleri bulma (sonradan bulunanlar da), her birini
  inceleme (git durumu, talimat ve durum dosyaları, eski oturumlar), bir alt ajanın okuması, plan (en fazla 10
  aktif görev), geri dönüş noktası (git etiketi; her projede dosya kopyaları), uygulama, doğrulama ve yalnız
  Session Guard dosyalarının doğrulanmış push'u. Proje geçişinden önce başlamış konuşmalar kilitlenir.
- **Bütçe:** bir kurulum oturumu yaklaşık 800k token kullanabilir; %90'da güvenli bir noktada durur, kullanıcı
  yeni oturumda `continue installation` ile devam eder.
- Bildirimler kullanıcının dilinde gösterilebilir (`sg install language`).
- Eski görev listeleri adına bakılmadan içeriğinden bulunur (onay kutuları, "Yapılacak / Bekleyen / Biten"
  başlıkları). Açık maddeleri görev, sıradaki görev ya da fikir, bitenleri `COMPLETED_TASKS.md` satırı olur;
  kimlikler 2.0'dan önce olduklarını gösterir (`PRE20-DONE-xxx`, `PRE20-IDEA-xxx`, görevlerde "pre-2.0"). Kullanıcının
  onayıyla yalnız görev listeleyen dosya bütünüyle silinir (kopya tutulmaz), başka işe de yarayan dosyalardaki
  görev metni kesilip çıkarılır; böylece geçişten sonra hiçbir ajan eski listeye uymaz. CLAUDE.md'de bir durum ya
  da görev satırını tekrar eden satırlar listelenir; durum CLAUDE.md'den çıkar, kural ile görev birbirinin
  kopyası olmaz.

## 11. Kaldırma
Her adım kullanıcının onayıyla ([setup skill'i](plugins/session-guard/skills/setup/SKILL.md), "Uninstall").
- **Bilgisayar** (`session-guard-executive`'den): önce her projenin açık görevleri ve devirleri gösterilir. Proje
  başına `sg install remove-project`: STATUS.md'nin son görev listesi düz metin olarak, arşiv, bekleyen fikirler ve
  biten görevler listesi kalır; Session Guard'ın hook'ları, kopyası, skill kopyaları ve kurulum işareti kaldırılır
  (`--remove-store` görev kaydını da siler). Sonra `sg install uninstall` (kural bölümü, durum çubuğu;
  `--purge-state` makinedeki durum klasörünü de siler) ve `claude plugin uninstall session-guard@session-guard`,
  ardından Claude tamamen kapatılıp açılır.
- **Bulut deposu:** deponun bir oturumunda `sg install remove-project`, commit, push ya da pull request; eski
  bulut oturumları claude.ai/code'da arşivlenir.
- **Uyarı:** `sg/<görev-kimliği>` yan dalları Session Guard'ın değil, projenin kendi yarım işini (kodunu) taşır. Git, birleştirilen dalı kendiliğinden silmez (GitHub, depo ayarlarında "Automatically delete head branches" açıksa birleştirilen pull request'in dalını siler); bulut oturumu ise hiç dal silemez (GitHub vekili silmeyi reddeder). Birleştirilen yan dalları GitHub'da elle silin.

## 12. Kurallar betiğe dönüşür
CLAUDE.md'deki her kural satırı bir kimlik alır (`<!-- R-001 -->`). CLAUDE.md'ye yeni ya da değişmiş kural gelince
(ilk oluşturulduğunda da) Session Guard Claude'dan kural incelemesi ister: diğer kurallarla çakışma (güncelleme ya
da birleştirme, kullanıcıyla birlikte karar) ve bir betiğin ya da hook'un denetleyebileceği her kural için betik
önerisi (fikir olarak; fikir sınırına sayılmaz). Kararı kullanıcı verir; betik görevi ancak betiğin çalıştığı
doğrulanınca biter ve o zaman kural CLAUDE.md'den çıkar. Böylece denetim Claude'dan (her oturumda token) betiğe
(token yok) geçer. STATUS.md ve CLAUDE.md bölüm ve kimlikle düzenlenir, satır numarasıyla asla; bir düzenlemenin
bıraktığı boş satırlar ve boş maddeler silinir.

## 13. Komutlar
Kullanıcı: `start long mode`, `finish session`, `continue installation` (mesajın başında, ardından virgül ya da
nokta; ya da sonunda). Claude `sg task …`, `sg idea …`, `sg rules check|reviewed`,
`sg handoff children|write|note|publish|adopt`, `sg breaks`, `sg migrate …`, `sg install …`, `sg manifest verify` ve
`sg report` komutlarını kendisi kullanır. Tam liste bu dosyanın sonundadır (§16).

## 14. Sınırlar
- Session Guard'ın okuduğu oturum kaydı biçimi Anthropic tarafından belgelenmemiştir, değişebilir.
- Eski bulut oturumları bilgisayardan kilitlenemez; onları claude.ai/code'da arşivleyin.
- Git kullanmayan projelerde git geri dönüşü yoktur; yalnız dosya kopyaları tutulur.
- Kilitler hook'larla çalışır: hook'suz açılan oturum kilitlenmez.
- Yan dal ana dala ancak kullanıcı birleştirince (ya da bir pull request ile) geçer; o zamana kadar ana daldan
  açılan oturum o devri `sg handoff adopt` ile görür.
- Doğrulanamayanlar: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §8.

## 15. Testler
```bash
python -m unittest discover -s tests
```

## Lisans
MIT ([LICENSE](LICENSE)). Olduğu gibi, garantisiz sunulur.

## 16. Bütün komutlar
Kullanıcı komutları, mesaja yazılır (mesajın başında, ardından virgül ya da nokta; ya da sonunda):
- `start long mode` — bu oturum için uzun mod (kapatılamaz)
- `finish session` — devirler, oturum ölçümü, sonra oturum kilitlenir
- `continue installation` — duraklamış kurulumu sürdürür (yalnız `session-guard-executive`'de)

`sg` komutları. `sg`, `python "<plugin>/scripts/session_guard.py"` demektir; Claude tam komutu oturum başında alır
ve bunları kendisi çalıştırır. Herhangi birini Claude'dan isteyebilirsiniz.

Görevler ve fikirler
- `sg task list [--all]` — projenin görevleri
- `sg task show <TASK-ID>` — tek görev, devriyle birlikte
- `sg task check --title "..." [--title "..."]` — yeni işi mevcut bütün işlerle karşılaştırır
- `sg task new --title "..." --approval "..." [--priority P1|P2|P3] [--kind urgent --reason "..."] [--queue]` — ana görev
- `sg task new --title "..." --parent <TASK-ID>` — alt görev
- `sg task start <TASK-ID>` — sıradaki görev aktif olur
- `sg task claim <TASK-ID> [--takeover "<kullanıcının sözleri>"]` — görevi devralır
- `sg task note <TASK-ID> [--done ...] [--pending ...] [--resolved ...] [--status ...] [--text ...]` — görevi günceller
- `sg task done <TASK-ID> --summary "<sonuç, tek cümle>" [--verified ...] [--force]` / `sg task cancel <TASK-ID> --summary "<neden>"` — görevi kapatır
- `sg task reopen <TASK-ID> --title "..." [--relation reopens|extends]` — bağlı yeni görevle yeniden yapar ya da genişletir
- `sg task relate <TASK-ID> --other <TASK-ID> [--relation ...]` — iki görevi bağlar
- `sg idea add "<metin>" [--source ...] [--rule R-xxx] [--force ...]` — fikir kaydeder
- `sg idea promote <IDEA-ID> --approval "..." [--queue]` — fikir görev olur

Devirler
- `sg handoff children` — devirden önce: alt oturum ajanları ve bağlı olanlara gönderilecek mesaj
- `sg handoff write <TASK-ID> --technical "..." [--paths ...] [--no-files] [--side-branch] [--pr <bağlantı>] [--children-gone "..."]` — görevin devir notu
- `sg handoff note --text "..." [--project-state]` — oturumun genel devir notu (imza, tarih, kota eklenir); bütün görevleri bitince proje durumu
- `sg state-push --root <klasör> --claude-id <kimlik>` — yalnız bulut, hook'lar arka planda çalıştırır: oturum kaydını `sg-state` dalına yazar
- `sg handoff publish` — devirleri yayımlar (yan dallar push edilir ve doğrulanır; not yoksa reddedilir)
- `sg handoff adopt sg/<task-id>` — işi ve devriyle bir yan dalı birleştirir

Kurallar
- `sg rules check` — CLAUDE.md'de henüz incelenmemiş kural satırları
- `sg rules reviewed` — incelemeyi kaydeder (kural kimliklerini verir)
- `sg rules remove <R-ID> --approval "..."` — kural satırını kaldırır (kullanıcının onayıyla)
- `sg rules remove <R-ID> --conflict "<Session Guard kuralı>"` — Session Guard'la çelişen kuralı kaldırır

Mola ölçümü
- `sg breaks on` / `sg breaks off` / `sg breaks show`

Kurulum, migration, kaldırma (`session-guard-executive`'den, her biri kullanıcının onayıyla)
- `sg manifest verify` (`sg manifest build` yalnız geliştirici için)
- `sg install conflicts` · `sg install approve --executive "<klasör>"` · `sg install language [<kod>]` ·
  `sg install messages <dosya>`
- `sg install cloud "<depo>"` · `sg install home-guard "<klasör>"`
- `sg install remove-project "<klasör>" [--remove-store]` · `sg install uninstall [--purge-state]`
- `sg migrate discover [--add <klasör>]` · `sg migrate status` · `sg migrate survey "<klasör>"` ·
  `sg migrate plan-help` · `sg migrate apply "<klasör>" --plan <dosya>` · `sg migrate verify "<klasör>"` ·
  `sg migrate push "<klasör>" --approval "..."` · `sg migrate pause --reason ...` ·
  `sg migrate skip "<klasör>" --reason ...` · `sg migrate finish` · `sg migrate start --claude-session <kimlik>`

Oturum ölçümü
- `sg report "<proje klasörü>"` — "finish session" için tek satır

Ortak seçenekler: `--session <SESSION-ID>` (yalnız oturumun kendi kimliği), `--root <proje klasörü>`,
`--expect-rev <n>`.
