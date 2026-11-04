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
- bağlam 100k üstündeyse mesaj **çalıştırılmaz**. Session Guard mesajı kelimesi kelimesine her görevin devir
  notuna yazar, Claude devreder, oturum kilitlenir;
- 100k ya da altındaysa tek satır hatırlatma, sonra mesaj çalışır.

Claude'un kendi arka plan işini beklemek mola sayılmaz. Uzun modda mola kuralı yoktur.

## 3. Oturumu bitirme
`finish session` (mesajın başında, ardından virgül ya da nokta; ya da mesajın sonunda): Claude başka hiçbir şey
yapmaz, her görevi devredilebilir hâle getirir, devir notlarını yazar, oturum ölçümünü alır (Session Guard'ın
`report` satırı; uygulama okuyabiliyorsa 5 saatlik ve haftalık kota) ve oturum kilitlenir. Devirler yazılıp
yayımlanana kadar Claude çalışmaya devam ettirilir (en fazla iki kez).

## 4. Kota (bilgisayar)
5 saatlik kota %90'a ya da haftalık kota %98'e geldiyse ve sıfırlanmaya 45 dakika ya da daha fazla varsa: Claude
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
- **Sınırlar:** en fazla 3 normal ana görev; 3'ü doluysa gerekçeli ve kullanıcı onaylı 1 acil görev; alt
  görevlerde sınır yok; kurulum ve migration görevleri muaf. Yeni ana görev kullanıcının onayını ister; ilgisiz
  işler bekleyen fikirlere gider (`docs/TASK_BACKLOG.md`).
- **Gerektiğinde yeni kimlik:** kaldığı yerden devam aynı kimlikle; hatalı biten işi yeniden yapmak ya da biten
  işe özellik eklemek eskisine bağlı yeni görevdir. Alt görev bitince üst görev kendiliğinden bitmez.
- **Güvenli paralel yazma:** proje başına bir kilit, kilit içinde taze okuma, sürüm denetimi; görev kaydının elle
  değiştirilmesi reddedilir, STATUS.md'deki görev bölümü elle bozulursa kayıttan yeniden oluşturulur.
- **Oturum başı:** Claude kimliklerini, görevlerini ve devralabileceği görevleri alır; birden fazlası varsa hangisi
  olduğunu kullanıcıya sorar.

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
çakışma olursa birleştirme durur ve hiçbir şey değişmez. Daha yeni bir oturum görevi alınca, açık görevi kalmayan
eski oturum kilitlenir. Alınan devir notları `STATUS_ARCHIVE.md`'ye geçer.

## 8. Proje dosyaları
| Dosya | Görevi |
|---|---|
| `CLAUDE.md` | kalıcı kurallar ve kararlar |
| `docs/STATUS.md` | güncel durum; görev bölümünü Session Guard üretir; en fazla 50 satır (üstündeyse yeni görev yok) |
| `docs/STATUS_ARCHIVE.md` | geçmiş; yalnız eklenir; oturum başında okunmaz, yalnız aranır |
| `docs/TASK_BACKLOG.md` | bekleyen fikirler |
| `docs/COMPLETED_TASKS.md` | biten her görev için tek satır |
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
  inceleme (git durumu, talimat ve durum dosyaları, eski oturumlar), bir alt ajanın okuması, plan (en fazla 3
  aktif görev), geri dönüş noktası (git etiketi; her projede dosya kopyaları), uygulama, doğrulama ve yalnız
  Session Guard dosyalarının doğrulanmış push'u. Proje geçişinden önce başlamış konuşmalar kilitlenir.
- **Bütçe:** bir kurulum oturumu yaklaşık 800k token kullanabilir; %90'da güvenli bir noktada durur, kullanıcı
  yeni oturumda `continue installation` ile devam eder.
- Bildirimler kullanıcının dilinde gösterilebilir (`sg install language`).
- Eski görev listeleri adına bakılmadan içeriğinden bulunur (onay kutuları, "Yapılacak / Bekleyen / Biten"
  başlıkları): açık maddeleri görev ya da fikir, bitenleri `COMPLETED_TASKS.md` satırı olur; eski dosyalar
  kullanıcının onayıyla kaldırılır (kopyası saklanır). CLAUDE.md'de bir durum ya da görev satırını tekrar eden
  satırlar listelenir; durum CLAUDE.md'den çıkar, böylece bir kural ile bir görev birbirinin kopyası olmaz.

## 11. Kaldırma
Her adım kullanıcının onayıyla ([setup skill'i](plugins/session-guard/skills/setup/SKILL.md), "Uninstall").
- **Bilgisayar** (`session-guard-executive`'den): önce her projenin açık görevleri ve devirleri gösterilir. Proje
  başına `sg install remove-project`: STATUS.md'nin son görev listesi düz metin olarak, arşiv, bekleyen fikirler ve
  biten görevler listesi kalır; Session Guard'ın hook'ları, kopyası, skill kopyaları ve kurulum işareti kaldırılır
  (`--remove-store` görev kaydını da siler). Sonra `sg install uninstall` (kural bölümü, durum çubuğu;
  `--purge-state` makinedeki durum klasörünü de siler) ve `claude plugin uninstall session-guard@session-guard`,
  ardından Claude tamamen kapatılıp açılır.
- **Bulut deposu:** deponun bir oturumunda `sg install remove-project`, commit, push ya da pull request. Bulut dal
  silemez; kalan `sg/*` dallarını kullanıcı GitHub'da siler; eski bulut oturumları claude.ai/code'da arşivlenir.

## 12. Kurallar betiğe dönüşür
Projenin CLAUDE.md dosyasına yeni ya da değişmiş bir kural satırı geldiğinde (ilk oluşturulduğunda da) Session Guard
Claude'dan kural incelemesi ister: bir betiğin ya da hook'un denetleyebileceği her kural için Claude betiği
bekleyen fikir olarak önerir, kararı kullanıcı verir. Böylece denetim Claude'dan (her oturumda token) betiğe
(token yok) geçer. STATUS.md bölüm bölüm düzenlenir, satır numarasıyla asla; bir düzenlemenin bıraktığı boş
satırlar ve boş maddeler silinir.

## 13. Komutlar
Kullanıcı: `start long mode`, `finish session`, `continue installation` (mesajın başında, ardından virgül ya da
nokta; ya da sonunda). Claude `sg task …`, `sg idea …`, `sg rules check|reviewed`,
`sg handoff write|publish|adopt`, `sg migrate …`, `sg install …`, `sg manifest verify` ve `sg report` komutlarını
kendisi kullanır.

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
