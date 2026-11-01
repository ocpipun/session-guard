# Session Guard

**Sürüm 1.4** · Açık kaynak, **MIT lisansı** ile yayımlandı · [English](README.md)

Session Guard, Claude Code için bir eklentidir. Claude'un kendi başına göremediği üç şeyi ölçer: oturumun ne kadar büyüdüğünü, ne kadar ara verdiğinizi ve kotanızdan ne kadar kaldığını. Bu ölçümlere göre Claude'a işi ne zaman devredeceğini ve ne zaman duracağını söyler. Biten oturumları kilitler; böylece sonradan yazılan hiçbir şey işi karıştıramaz.

---

## 1. Neden yazıldı

Claude Code oturumunda her mesaj, o ana kadarki bütün konuşmayı (*bağlamı*) yeniden gönderir. Oturum uzadıkça her mesaj daha pahalı olur. Birkaç yüz bin tokenı geçince tek bir mesaj, kısa bir oturumun tamamı kadar tutabilir.

Dört sorun bunu kötüleştirir:

1. **Claude kendi bağlam büyüklüğünü göremez.** Uygulama sayıyı size gösterir ama model bu sayıyı hiç almaz. `CLAUDE.md`'deki "300k'da devret" gibi bir kural Claude için yalnız bir tahmindir.
2. **Uzun aralar pahalıdır.** İstem önbelleğinin süresi yaklaşık bir saatte dolar. Uzun aradan sonraki ilk mesaj bütün bağlamı tam fiyatla yeniden işler.
3. **Kota bir adımın ortasında biter.** 5 saatlik ya da haftalık sınıra gelinince iş nerede kaldıysa orada durur.
4. **Eski oturumlar yenileriyle karışır.** Devir notu yazılıp yeni oturuma geçildikten sonra eski oturuma tekrar yazmak, aynı işin iki farklı sürümünü doğurur.

Session Guard bunları tahminle değil ölçümle çözer.

## 2. Ne işe yarar

### Bağlam eşikleri

| Durum | Normal mod | Uzun mod |
|---|---|---|
| 250k token | Bildirim | Bildirim |
| 300k | **Devir:** STATUS.md sadeleştirilir (eski görevler ve gereksiz ayrıntılar arşive taşınır), devir notu yazılır. Sonrasında: yeni görev yok, yalnız sorular | Yalnız bildirim |
| 350k | Hedef durma: yeni oturum açın | — |
| 400k | **Kesin durma**, oturum kilitlenir | — |
| 700k | — | Bildirim; iş devredilebilir hâle getirilir (Claude çalışmaya devam edebilir) |
| 800k | — | Yeni iş yok; yalnız basit sorular; son devir notu |
| 900k | — | **Kesin durma**, oturum kilitlenir |

- Claude bir eşikte **yalnız Session Guard söyleyince** harekete geçer. Erken uyarı yok, erken devir yok, sıradaki iş büyük görünüyor diye "yeni oturum açın" yok. Session Guard her araç çağrısından sonra ölçer ve 400k'da durdurur; plan her zaman geçerlidir.
- Her eşik size tek bir biçimde gelir, örneğin: "250k barajı geçildi: yalnız bilgilendirme. *Bu bir Session Guard bildirimidir."

### Molalar

| Durum | Normal mod | Uzun mod |
|---|---|---|
| 1 saatten uzun ara, bağlam 100k'nın üstünde | Mesajınız **çalıştırılmaz**. Claude devir notunu yazar, mesajınızı nota kelimesi kelimesine ekler, oturum kilitlenir | Mola kuralı kapalı |
| 1 saatten uzun ara, bağlam 100k ya da altında | Tek satır hatırlatma, sonra mesajınız çalışır | Mola kuralı kapalı |

Yalnız *sizin* yokluğunuz sayılır. Claude kendi başlattığı bir arka plan işini (render, uzun bir betik, alt ajan) bekliyorsa bu süre mola sayılmaz.

### Kota koruması (Session Follow, bilgisayarda)

- Session Guard **5 saatlik** ve **haftalık (tüm modeller)** kotayı izler.
- **Masaüstü uygulaması:** son okuma 20 dakikadan eskiyse Claude uygulamanın kullanım aracını okur (5 saatlikte %85 ya da haftalıkta %95'ten sonra her mesajda; uzun işte en çok 5 dakikada bir). Okuma başına yaklaşık 400 token (tahmin).
- **Terminal:** sayılar Session Guard'ın durum çubuğundan gelir, token harcamaz (başka bir durum çubuğunuz yoksa).
- **5 saatlikte %90 ya da haftalıkta %98'de**, sıfırlanmaya en az 45 dakika varsa: Claude yeni görev başlatmaz, mevcut işi 15 dakika içinde devredilebilir hâle getirir ve STATUS.md'ye tek satır yazar. Sonra oturum sıfırlanmaya kadar kilitlenir. Bu arada açılan oturumlar hemen kilitlenir. Sıfırlanmaya 45 dakikadan az varsa iş sürer.
- Sıfırlanınca kilit kendiliğinden kalkar.

### Kilitli oturumlar

- Kilitli oturum hiçbir mesajı Claude'a iletmez. Mesaj gönderilmeden durdurulur; kota harcanmaz, hiçbir şey değişmez.
- Kilit türleri ayrı tutulur: bağlam sınırı, finish session, mola, kota (sıfırlanınca kalkar), yeniden başlatma (bilgisayarda kurulumdan önce başlamış oturum).
- Masaüstü uygulaması eski bir sohbeti yeni bir kimlikle açarsa Session Guard onu tanır; kilit (ve uzun mod) korunur.

### Yaptığı diğer işler

- **Oturum başında** Claude kuralları ve `docs/STATUS.md` dosyanızı kendiliğinden alır. Bir daha "STATUS.md'yi oku" yazmazsınız.
- **Proje dosyaları:** her proje `CLAUDE.md` (kalıcı bilgiler ve kararlar), `docs/STATUS.md` (güncel durum, en fazla 50 satır) ve `docs/STATUS_ARCHIVE.md` (geçmiş) alır. STATUS.md her oturumun başında okunur, devirden sonraki yeni oturum dahil; arşiv okunmaz: Claude onu yalnız gerektiğinde arar ve sadece ilgili kısmı okur, böylece token harcanmaz. Var olan dosyaların üstüne asla yazılmaz.
- **STATUS kısa kalır (temel kural):** en fazla 50 satır. Her devirde ve güncellemede eski görevler ve gereksiz ayrıntılar arşive taşınır. STATUS 50 satırı geçecekse Claude yeni görev almaz, önce bekleyen görevleri bitirmenizi ister. Session Guard uzunluğu her mesajda denetler.
- **Aynı işi başka adla yapan dosyalar** (AGENTS.md, GEMINI.md, .cursorrules, HANDOFF*, NOTES*, TODO*, PROGRESS*, status dosyaları ve benzerleri) onayınızla incelenir: çakışan satırlar silinir, işe yarar içerik STATUS'a ya da arşive taşınır.
- **Tek kural seti:** Session Guard'la çakışan kurallar, skill'ler, betikler ve hook'lar kurulumda onayınızla kaldırılır.
- **Token tasarrufu yapan yazım biçimi:** sade kelimeler, kısa cümleler, her konu numaralı, yalnız gereken etiketler: SORUN / ÖNERİ / VARSAYIM / UYARI / DOĞRULANMADI (sizin dilinizde).
- **Bilgi bilgidir:** Claude'un kontrol etmediği şey DOĞRULANMADI diye işaretlenir; kendi önerileri ve varsayımları kendisinin olarak işaretlenir.
- **Bildirimler sizin dilinizde:** uygulamanın doğrudan gösterdiği bildirimler (kilitler, engellenen mesajlar, eşikler) kurulumda seçilen dilde gelir. Ayrıntısı 5. bölümde.

## 3. Nerelerde kullanılabilir

| Yer | Çalışır mı | Not |
|---|---|---|
| Claude masaüstü uygulaması (bilgisayar) | Evet | Kota koruması dahil bütün özellikler. Windows'ta denendi. |
| Terminalde Claude Code (bilgisayar) | Evet | Kota durum çubuğundan. Bildirimler İngilizce. |
| Bilgisayardaki oturuma bağlanan telefon ya da tarayıcı (Remote Control) | Evet | Oturum bilgisayarda çalışır, bilgisayar kuralları geçerlidir. |
| Bulut oturumları (claude.ai/code, telefondan ya da tarayıcıdan da) | Evet, depo başına | Kota koruması yok. Depoya kurulur. |
| macOS ve Linux masaüstü uygulaması | Beklenir | Onlar için de yazıldı, **denenmedi**. |

**Her projenin tek yeri var (kesin kural).** Bir proje **ya bilgisayarda ya bulutta yürütülür, ikisinde birden asla**, aynı anda da asla. Kota koruması yalnız bilgisayarda çalışır ve aynı STATUS.md'yi iki yerden yazmak birbirinden ayrılan iki sürüm doğurur.
- Git deposu olan bir bilgisayar projesi `.claude/settings.json` dosyasına tek satırlık bir hook alır. Bu hook bilgisayarda hiçbir şey yapmaz; o deponun bulut oturumunda her mesajı Claude'a ulaşmadan durdurur.
- Bulut projesi Session Guard'ın bulut kopyasını `.claude/session-guard/` içinde taşır. Bilgisayarda açılırsa her mesaj durdurulur.
- **Bilgisayar koruması hemen GitHub'a gider.** Bulut oturumlarını ancak GitHub'a ulaşınca engeller; bu yüzden kurulum onu her git projesinde hemen commitleyip pushlar (Session Guard dosyaları ve kurulumun orada değiştirdiği dosyalarla birlikte; diğer değişikliklerinize dokunulmaz). Yeni bir projenin koruması ilk oturumunda pushlanır.
- Daha önce hem bilgisayarda hem bulutta kullanılmış bir depo, kurulumda bilgisayar projesi olur (bulut kopyası kaldırılır, koruma pushlanır). Önceden açılmış bulut oturumları bilgisayardan kilitlenemez: onları claude.ai/code'da arşivleyin.
- Bilgisayarda GitHub deposu gerekmez: Session Guard `~/.claude` içinde durur, projeye yalnız üç proje dosyasını koyar. Bulut her zaman bir depo ister.

## 4. Kurulum

### Bilgisayarda

Gerekenler: Claude Code (masaüstü uygulaması ya da terminal) ve Python 3 (Windows'ta `python`, macOS/Linux'ta `python3`). Windows'ta Claude Code hook'lar için Git Bash kullanmalı (Git for Windows kuruluysa varsayılan budur).

**Kesin kural: kurulum ve güncellemeler yalnız `session-guard-executive` adlı bir Claude proje klasöründe yapılır.** Bu klasör olmadan Session Guard bilgisayar oturumlarında çalışmaz.

1. İstediğiniz yerde `session-guard-executive` adlı bir klasör oluşturun ve Claude'da açın.
2. Oradaki bir oturuma şu tek satırı yapıştırın:

   ```
   Install Session Guard: run "claude plugin marketplace add ocpipun/session-guard" (if it is already added: "claude plugin marketplace update session-guard"; if "claude" is not found, use the program in $CLAUDE_CODE_EXECPATH), then follow INSTALL.md in ~/.claude/plugins/marketplaces/session-guard
   ```

3. Claude Session Guard'ı indirir, genel `~/.claude` klasörünüzü ve Claude Code'un bildiği her projeyi tarar. **Tek liste** gösterir: çakışan kurallar, skill'ler, betikler ve hook'lar; proje dosyalarının işini başka adla yapan dosyalar; eski bir Session Guard'dan kalanlar. Aynı mesaj kurulumun başka ne yaptığını da söyler: `~/.claude/CLAUDE.md`'deki genel kurallar bloğu, eksik proje dosyaları, `~/.claude/settings.json`'daki durum çubuğu ve git projelerindeki bilgisayar koruması (hemen GitHub'a pushlanır). Uygulama dili okunamazsa şunu da sorar: "Which language would you like session-guard notices in?"
4. **Bir kez** cevap verirsiniz. Onaylanan maddeler kalıcı olarak silinir (yedek kopya yok). **Reddederseniz hiçbir şey kurulmaz.**
5. Claude eklentiyi kurar (eski bir sürüm varsa yalnız güncellenir; elle bir şey silmeniz gerekmez).
6. Son adım: **Claude'u tamamen kapatın** (masaüstü uygulamasında sistem tepsisinden de) **ve yeniden açın.** O zamana kadar açık oturumlar eskisi gibi çalışır. Yeniden açtıktan sonra hiçbir şey yazmazsınız, size hiçbir şey sorulmaz; aynı sohbetlerde devam edebilirsiniz.

Bundan sonra o bilgisayardaki her projede çalışır. Sonradan açılan ve kendi CLAUDE.md'si, benzer dosyaları, skill'leri ya da hook'ları olan bir proje, yalnız o proje için bir kez aynı incelemeden geçer; inceleme kendiliğinden başlar.

### Bulutta

Bulut oturumları eklenti kurmaz. Session Guard her depoya bir kez, o deponun bir bulut oturumundan kurulur.

**Başlamadan önce oturumun izin modunu Auto'dan "Accept edits" (düzenlemeleri kabul et) moduna alın.** Kurulum Claude'un kendi kurallarını (CLAUDE.md) ve hook'larını (`.claude/settings.json`) değiştirir; Auto modda güvenlik denetimi bunu, onay verseniz bile, kendini değiştirme (self-modification) sayıp engeller.

Sonra şunu yapıştırın:

```
Install Session Guard: git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard, then follow /tmp/session-guard/CLOUD.md
```

Claude deponun inceleme listesini gösterir, "Which language would you like session-guard notices in?" diye sorar ve tek onay sorusu sorar. Reddederseniz hiçbir şey kurulmaz. Onaylarsanız Claude Session Guard'ı `.claude/` içine kurar, eksik proje dosyalarını oluşturur ve hepsini doğrudan deponun ana dalına commit eder. **Aynı oturumda devam edersiniz:** Session Guard orada bir sonraki mesajınızdan itibaren, o deponun her yeni oturumunda da çalışır. Önceden açık olan oturumlar korumasız kalır. Bilgisayar projesine kurulmaz (her projenin tek yeri var).

## 5. Komutlar ve günlük kullanım

### Yazdığınız komutlar

Mesajın **sonuna** ya da **başına** (ardından virgül veya nokta ile) yazın. Başka bir yerde (cümle içinde, alıntıda, yapıştırılan metinde) bilerek yok sayılır.

| Komut | Ne yapar |
|---|---|
| `start long mode` | Yalnız bu oturum için uzun mod. Kapatılamaz; yeni oturum normal modda başlar. Uzun, karmaşık ya da gözetimsiz işler için. |
| `finish session` | Konuyu bitirir: devir notu ve kullanım ölçümü, sonra oturum kilitlenir. Bir konu bittiğinde kullanın; böylece bir oturum birden çok konuyu karıştırmaz. |

Örnekler: `Start long mode, render the remaining clips` · `Bugünlük bu kadar, finish session`

### Claude'dan istedikleriniz

| İstediğiniz | Yapacağınız |
|---|---|
| Session Guard'ı güncellemek (bilgisayar) | `session-guard-executive` içinde kurulum satırını yeniden yapıştırın |
| Session Guard'ı güncellemek (bulut) | O deponun bir bulut oturumunda bulut kurulum satırını yeniden yapıştırın |
| Kurulumu ya da incelemeyi yeniden çalıştırmak | `/session-guard:setup` yazın |
| Bildirimleri başka bir dilde almak | Claude'a söyleyin, örneğin "Session Guard bildirimleri Fransızca olsun". Claude çevirir; o zamana kadar İngilizce gelir |
| Kaldırmak | Claude'dan Session Guard'ı kaldırmasını isteyin (ayrıntısı aşağıda) |

### Bildirim dili

- Bilgisayarda Claude masaüstü uygulamasının ayarlarında seçili dil **kurulumda bir kez** okunur, bir daha kontrol edilmez. Türkçe eklentiyle gelir; diğer dilleri kurulumda Claude çevirir.
- Bulutta uygulama dili okunamaz; Claude kurulumda sorar.
- Terminalde bildirimler İngilizcedir.
- Üç bildirim her zaman İngilizcedir, çünkü kurulumdan önce ya da kurulumun dışında çıkabilirler: `session-guard-executive` klasörünün olmaması, bulut projesinin bilgisayarda açılması ve bilgisayar projesinin bulutta açılması.

### Kaldırma

Claude'dan Session Guard'ı kaldırmasını isteyin. Kendi durum çubuğunu kaldırır (`session_guard.py --uninstall-statusline`) ve şunu çalıştırır:

```bash
claude plugin uninstall session-guard@session-guard
```

Ardından `~/.claude/CLAUDE.md`'deki `<!-- session-guard:begin` ile `<!-- session-guard:end -->` arasındaki bloğu silin. Bulut için kurulmuş bir depoda Claude'dan `.claude/session-guard/`, `.claude/skills/session-guard/`, `.claude/skills/session-guard-setup/` klasörlerini ve `.claude/settings.json`'daki Session Guard hook'larını kaldırmasını isteyin.

## 6. Nasıl çalışır

- **Hook'lar** Session Guard'ın betiğini kendiliğinden çalıştırır: oturum başında, gönderdiğiniz her mesajda ve Claude'un kullandığı her araçtan sonra. Dördüncü bir hook arka planda 24 saat bekçisidir.
- **Betik** (`scripts/session_guard.py`, yalnız Python standart kütüphanesi) Claude Code'un diskinizde zaten tuttuğu oturum kaydını okur. Bağlam büyüklüğü Claude'un son yanıtının token kullanımından, ara son yanıttan bu yana geçen süreden alınır. Bir eşikte ölçülen sayılarla talimat ekler; normal bir mesaj hiçbir şey almaz, token harcamaz.
- **Skill'ler** prosedürleri tutar (kurulum ve inceleme, devir, mola devri, finish session). Claude onları yalnız gerektiğinde yükler.
- **Durum**, Session Guard'ın `~/.claude/session_guard/` içindeki kendi küçük kaydıdır: oturum başına uzun mod, kilitler ve bildirimler; ortak kota okumaları; kurulum kaydı, bildirim dili ve kendi çevirileriniz. Orada hiçbir proje dosyası ya da sohbet tutulmaz.

**Hiçbir yere bir şey gönderilmez.** Betik yalnız yerel dosyaları okur ve kendi dosyalarını yazar.

## 7. Sınırlamalar

- Bağlam sayısı *son yanıtın* büyüklüğüdür, bir adım geriden gelebilir. Eşikler her araç çağrısından sonra yeniden kontrol edilir.
- **Bulutta kota koruması yok:** orada hiçbir hook kotayı okuyamaz (belgelere bakıldı, denendi).
- Masaüstünde her kota okuması token harcar (yaklaşık 400, tahmin); terminal durum çubuğu harcamaz.
- Uygulama dili masaüstü uygulamasının kendi ayar dosyasından okunur (`config.json`, anahtar `locale`). Bu dosya belgelenmemiştir. Windows'ta ölçüldü; macOS (`~/Library/Application Support/Claude/`) ve Linux'ta (`~/.config/Claude/`) uygulamanın kodundan çıkarıldı, **denenmedi**. Bulunamazsa Claude sorar.
- Bilgisayarda kurulumdan önce başlamış oturumlar ancak uygulama yeniden başlatılınca korunur.
- Bir proje ya bilgisayarda ya bulutta durur, ikisinde birden değil (3. bölüm).
- "Bitti" haberi hiç gelmeyen bir arka plan işi en fazla 24 saat "sürüyor" sayılır. Sonra, ya da Claude tek bir mesaj üzerinde 24 saatten uzun çalışırsa, Session Guard bunun bir hata olabileceğini söyler. 24 saat zamanlayıcısı yerel bir kontroldür, kota harcamaz. Bulutta, bulut boşta kalan oturumu daha önce kapatırsa zamanlayıcı da durur.
- "Her proje", Claude Code'un oturumu olan her projedir. Claude Code'da hiç açılmamış klasörler ilk açıldıklarında incelenir.
- Bir şeyin çakışıp çakışmadığına Claude karar verir, siz onaylarsınız; betik yalnız adayları bulur.
- Eşikler betikte sabittir (`NORMAL_STEPS`, `LONG_STEPS`, `STOP_AT`). Çalışma değerleridir, kanıtlanmış kota tasarrufu değildir.
- v1.4'ün masaüstü kurulumu henüz gerçek bir kurulumda denenmedi; bulut kurulumu ve güncellemesi denendi (v1.2).

## 8. Testler

```bash
python -m unittest discover -s tests
```

## 9. Lisans: açık kaynak, MIT

Session Guard **açık kaynaklıdır** ve **MIT lisansı** ile yayımlanmıştır ([LICENSE](LICENSE)). Ticari kullanım dahil özgürce kullanabilir, kopyalayabilir, değiştirebilir ve paylaşabilirsiniz. **Olduğu gibi, hiçbir garanti olmadan** sunulur. Bir şey kurulumunuza uymuyorsa (başka bir dil, başka bir işletim sistemi, başka eşikler) kendiniz değiştirmekte özgürsünüz.
