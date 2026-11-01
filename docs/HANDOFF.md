# 浜ゆ帴鏂囨。 路 Book2Real

> 鏇存柊浜?2026-11-01銆備粨搴擄細`C:\Users\DUANboyang\Desktop\Book2Real`
> 鐘舵€侊細**鐪熺粯鏈凡鎺ュ叆骞跺嚭鐗囷紱QC 宸叉寜鐪熶功閲嶆爣锛?1/21 閫氳繃锛夛紱缂撳瓨鍛戒腑宸查獙璇?*锛涚湡 I2V 鏈帴銆?
---

## 0 涓€鍒嗛挓鐜扮姸

```
python scripts/import_book.py --src assets/book2 --book charlie_lola \
       --title "鎴戠壒鍒簡瑙ｈ睔榧? --subtitle "鏌ョ悊涓庡姵鎷?路 缃椾鸡路涔斿皵寰?
python -m book2real plan --book books/charlie_lola
python -m book2real run  --book books/charlie_lola --out runs --jobs 2
python -m book2real run  --book books/guoqing      --out runs --jobs 1
python -m book2real ui   --runs runs --open       # 娴忚鍣ㄧ湅鐗?+ 璐ㄦ鐪嬫澘
```

瀹炴祴锛堟湰鏈?RTX 5070 Laptop 8G / i9-14900HX / ffmpeg 8.1.1锛夛細

| 椤?| book2锛堟煡鐞嗕笌鍔虫媺锛?1 椤碉級 | book1锛堣繋鍥藉簡锛? 椤碉級 |
|---|---|---|
| 鎴愮墖 | `runs/charlie_lola/<run>/film.mp4`锛?*114.0 s / 1280脳720 / 24fps** | `runs/guoqing/<run>/film.mp4`锛?*12.0 s** |
| 闀滃ご | 21 涓紝椤甸潰瑕嗙洊 **100%**锛岄檷绾?0 娆?| 1 涓?|
| 棣栬窇鑰楁椂 | **195.4 s**锛坄--jobs 2`锛?| **20.5 s** |
| 澶嶈窇锛堝叏缂撳瓨锛?| **15.8 s**锛宍cache_hits = 21/21` | 鍚屼笂锛堝璺戜粛浼氶噸缂栫爜锛?|
| 骞冲潎璐ㄦ鍒?| **0.911**锛?*21/21 `ok`** | 0.877锛?/1 `ok` |

**缁撹**锛氳繖鍙扮瑪璁版湰鑳借窇瀹屼竴鏈?21 椤电湡涔︼紝3 鍒?15 绉掗璺戙€?6 绉掑璺戯紝杩滀綆浜?15 鍒嗛挓棰勭畻銆?
**鍏抽敭鍙栬垗**锛氱湡涔﹂〉闈㈠甫鍗板埛鏂囧瓧锛屽垎灞備細璁╂枃瀛楄鎷嗗埌涓嶅悓灞傘€佸嚭鐜伴噸褰憋紙鏃х増瀹炴祴鍙锛夈€?涓ゆ湰鐪熶功鍥犳鍦?`book.json` 閲屽啓 `"layers": false`锛氭暣椤典綔涓轰竴鍧楀垰鎬ф澘鍋氳繍闀滐紙鎺?鎽?缂╂斁锛夛紝
涓嶅啀鍋氬墠鏅宸€?*鐢昏川浼樺厛浜庡垎灞傜偒鎶€**锛涙枃瀛?mask + inpaint 鎺ラ€氬墠锛屼笉瑕佸甯﹀瓧椤靛紑鍒嗗眰銆?鏍峰紶 `books/moon_rabbit` 浠嶈蛋 2鈥? 灞傝宸紝鐢ㄦ潵楠岃瘉鍒嗗眰閾捐矾銆?
---

## 1 宸查獙璇?/ 鏈獙璇侊紙鍒妸鏈獙璇佸綋瀹屾垚锛?
| 鑳藉姏 | 鐘舵€?| 璇佹嵁 |
|---|---|---|
| 鐪熶功瀵煎叆 + 鎸夊垱寤烘椂闂存帓搴?| 鉁?楠岃瘉 | `scripts/import_book.py`锛?1 椤?`ORDER.csv` 鍙拷婧?|
| 骞抽潰鏉胯繍闀滐紙`layers:false`锛?| 鉁?楠岃瘉 | 114 s 鎴愮墖锛岄€愰暅甯ф鏌ユ枃瀛楁棤閲嶅奖 |
| 甯?鈫?mp4锛坒fmpeg 绠￠亾锛?| 鉁?楠岃瘉 | `ffprobe`: h264 1280脳720 24fps 114.0s |
| 鐗囧ご / 灏惧崱 / 鎷兼帴 | 鉁?楠岃瘉 | 涓枃鏍囬涓庡熬鍗℃甯革紙鏃х増 mojibake 宸蹭慨锛?|
| 缂撳瓨閿?+ 鍛戒腑 | 鉁?楠岃瘉 | 澶嶈窇 `cache_hits=21`锛?95.4 s 鈫?15.8 s |
| QC 鍒ゆ嵁锛堟寜鐪熶功閲嶆爣锛?| 鉁?楠岃瘉 | 21/21 accept锛泂tatic/black/wrong-page 涓変釜璐熸帶鍏ㄩ儴鎷掔粷 |
| `fill` 鐢诲箙锛堣法椤碉級 | 鉁?楠岃瘉 | 12 涓法椤靛叏骞呮棤榛戣竟锛堜慨浜?`fit` 鎸夊崱鐗囧昂瀵哥畻鐨?bug锛?|
| 闄嶇骇閾?| 鈿狅笍 鏈Е鍙?| 鏃?fallback 璁板綍锛堟病鏈夊潖闀滃ご锛屽睘棰勬湡锛?|
| 棰勭畻瀹堥棬锛坉eadline锛?| 鈿狅笍 鏈Е鍙?| 鏈窇鍒?900 s |
| Web 鎾斁鍣?| 鈿狅笍 閮ㄥ垎楠岃瘉 | `/` 200銆乣/api/manifest` 21 chapters銆乣/media` Range 206锛涙祻瑙堝櫒 JS 鏈偣 |
| 鐪?I2V锛圵an/Kling锛?| 鉂?鏈帴 | 浠?`pregen` 鎺ュ彛 |
| 甯﹀瓧椤佃宸?| 鉂?涓诲姩鍏抽棴 | 瑙佷笂锛涢渶瑕佹枃瀛?mask + 鍒嗗眰鍥哄畾鏂囧瓧鍚庢墠鑳藉紑 |


---

## 2 鐩綍鍦板浘

```
Book2Real/
鈹溾攢 book2real/                 # 鍖咃紙绾?2400 琛岋級
鈹? 鈹溾攢 contracts.py            # 鈽?鏁版嵁濂戠害锛歋hotSpec / ClipOut / Manifest / QCVerdict
鈹? 鈹溾攢 config.py               # 鐗囬暱銆佺敾骞呫€丵C 闃堝€笺€侀绠?鈹? 鈹溾攢 storyboard.py           # 鈽?book.json 鈫?ShotSpec[]锛堥€夎建銆佸畾鏃堕暱銆佸瓧骞曪級
鈹? 鈹溾攢 layers.py               # 鈽?鍥?搴曞垎绂?+ 琛ユ礊 鈫?2鈥? 涓?RGBA 灞?鈹? 鈹溾攢 imgops.py               # 绾?numpy+Pillow 鐨勫浘鍍忓師璇紙鏃?OpenCV锛?鈹? 鈹溾攢 camera.py               # 杩愬姩杞ㄨ抗 + 姣忓眰瑙嗗樊绯绘暟
鈹? 鈹溾攢 render.py               # 鈽?灞?鐩告満 鈫?1280脳720 甯э紙鍗＄墖鏋勫浘锛?鈹? 鈹溾攢 backends.py             # 鈽?鍥涗釜鍚庣锛歱arallax / procedural / pregen / static
鈹? 鈹溾攢 encode.py               # 甯р啋mp4锛沵p4鈫掑皬甯э紙缁?QC锛?鈹? 鈹溾攢 qc.py                   # 鈽?6 椤规寚鏍?+ accept/retry 鍒ゅ畾
鈹? 鈹溾攢 assemble.py             # 鐗囧ご/灏惧崱/ASS 瀛楀箷/鎷兼帴/灏佽
鈹? 鈹溾攢 pipeline.py             # 鈽?缂栨帓锛氱紦瀛樸€佸苟琛屻€侀绠椼€侀檷绾с€乵anifest
鈹? 鈹溾攢 cache.py                # 鍐呭瀵诲潃 + shot 缂撳瓨閿?鈹? 鈹溾攢 cli.py / __main__.py    # plan / run / ui / sample / doctor
鈹? 鈹溾攢 webapp.py + web/        # 鏍囧噯搴?HTTP + 鎾斁鍣紙Range 璇锋眰宸插疄鐜帮級
鈹? 鈹斺攢 util.py                 # 鍝堝笇銆佸師瀛愬啓銆佷簨浠舵棩蹇椼€乫fmpeg 灏佽
鈹溾攢 books/charlie_lola/        # 鐪熶功鈶狅細21 椤碉紙鍚?12 涓法椤碉級+ book.json + ORDER.csv
鈹溾攢 books/guoqing/             # 鐪熶功鈶★細鍗曢〉銆婅繋鍥藉簡銆?鈹溾攢 books/moon_rabbit/         # 鍚堟垚鏍峰紶锛? 椤碉紝璧板垎灞傝宸級
鈹溾攢 runs/<id>/<run>/           # 浜х墿锛歠ilm.mp4銆乵anifest.json銆乼imeline.json銆乪vents.jsonl銆乼humbs/
鈹溾攢 configs/default.json       # 闃堝€奸兘鍦ㄨ繖閲岋紝鏀硅繖閲屼笉鐢ㄦ敼浠ｇ爜
鈹溾攢 samples/make_book.py       # 鏍峰紶鐢熸垚鍣紙鍙綋鐢婚妯℃澘锛?鈹溾攢 scripts/                   # setup_env.ps1 / run_demo.ps1 / import_book.py
鈹溾攢 tests/test_smoke.py        # 9 涓函 CPU 鍐掔儫娴嬭瘯锛堟棤闇€ ffmpeg锛?鈹斺攢 docs/ARCHITECTURE.md       # 璁捐鐞嗙敱涓庡彇鑸?```

鈽?= 鏀逛唬鐮佹椂鏈€甯稿姩鐨勬枃浠躲€?
---

## 3 涓変釜鏍稿績姒傚康

1. **A 杞?vs B 杞?*
   - A `parallax`锛氬垎灞?+ 铏氭嫙鐩告満銆?*纭畾鎬с€佸揩銆佸儚绱犲繝瀹?*锛岃礋璐ｈ鐩栫巼銆?     `layers:false` 鏃堕€€鍖栦负鍗曞潡鍒氭€ф澘鐨勮繍闀滐紙鐪熶功甯﹀瓧椤电殑榛樿锛夛紝浠嶅睘 A 杞ㄣ€?   - B `procedural`锛氬崟灞?+ 鍏夊奖/绮掑瓙/鍛煎惛寰姩銆傝礋璐?鏈夌敓姘?銆?   - B `pregen`锛氬閮ㄧ敓鎴愮殑鐗囨锛圵an/鍙伒/Seedance锛夛紝鎸?`clips/<shot_id>.mp4` 鏀惧叆鍗冲彲鎺ュ叆銆?2. **濂戠害鍏堣**锛歚ShotSpec` 杩涖€乣ClipOut` 鍑恒€備换浣曞悗绔彧瑕侀伒瀹堣繖涓ゅ紶琛ㄥ氨鑳芥彃杩涙潵銆?3. **缂撳瓨閿?*锛歚sha256(shot 瑙勬牸 + 椤靛浘鍝堝笇 + 杞ㄩ亾 + 鐢诲箙鍙傛暟)`銆傚悓涓€椤垫案杩滃嚭鍚屼竴娈碉紝浜屾杩愯 0 绉掋€?
---

## 4 鎹竴鏈湡瀹炵粯鏈紙涓夋锛?
1. **瀵煎叆椤甸潰**锛堣嚜鍔ㄦ寜鏂囦欢鍒涘缓鏃堕棿鎺掑簭锛岄€傚悎浠庣浉鍐?鑱婂ぉ璁板綍椤哄簭澶嶅埗鍑烘潵鐨勫浘锛夛細
   ```
   python scripts/import_book.py --src assets/book2 --book mybook \
          --title "涔﹀悕" --subtitle "鍓爣棰?
   ```
   浜х墿锛歚books/<id>/pages/p01.jpg鈥銆乣book.json`銆乣ORDER.csv`锛堥〉鐮?鈫?婧愭枃浠?鈫?鍒涘缓鏃堕棿锛夈€?2. 鎸夐渶鏀?`book.json` 鐨?`move` / `duration` / `mode` / `layers`锛?
```jsonc
{
  "book_id": "mybook", "title": "涔﹀悕", "fps": 24,
  "layers": false,                 // 甯﹀嵃鍒锋枃瀛楃殑鎵弿椤碉細蹇呴』 false锛岄伩鍏嶆枃瀛楅噸褰?  "pages": [
    { "file": "p01.jpg", "move": "dolly_in", "mode": "card", "duration": 3.5 },
    { "file": "p06.jpg", "move": "pan_lr",   "mode": "fill", "duration": 6.0 }
    // 璺ㄩ〉锛堝楂樻瘮 鈮?.2锛夌敤 fill 婊″箙锛涘崟椤电敤 card 鍗＄墖
  ]
}
```

3. `python -m book2real plan --book books/mybook` 鍏堢湅鍒嗛暅琛紝鍐?`run`銆?
**鎷嶇収椤靛缓璁?*锛氭瀵规媿鎽勩€佸洓杈圭暀 5% 鐧借竟銆侀伩鍏嶅弽鍏夊拰鎵嬫寚銆傚垎灞傜畻娉曢潬"杈规涓昏壊"鍒よ儗鏅紝姝潃鎷嶄細鎺夌簿搴︺€?
---

## 5 甯歌鏀规硶

| 鎯虫敼浠€涔?| 鏀瑰摢閲?|
|---|---|
| 杩愰暅绉嶇被/骞呭害 | `book.json` 鐨?`move`/`amount`锛屾垨 `camera.py:pose()` 鍔犲垎鏀?|
| 鐢诲箙/鍗＄墖澶у皬/鑳屾櫙鍘嬫殫 | `configs/default.json` 鐨?`film.*` |
| 寰姩寮哄害 | `book.json` 鐨?`motion: {light, dust, breathe}`锛?鈥?锛?|
| 鍒嗗眰鐏垫晱搴?| `layers.py:decompose()` 鐨?`min_area_frac` / Otsu 鍓嶆潈閲?0.68/0.32 |
| QC 鍒ゆ嵁 | `configs/default.json` 鐨?`qc.*`锛堝綋鍓嶅€煎凡鎸変袱鏈湡涔﹂噸鏍囷紝瑙?搂6.1锛?|
| 鏄惁鍒嗗眰 | `book.json` 鐨?`layers`锛堥粯璁?true锛涘甫瀛楁壂鎻忛〉璁?false锛?|
| 鍔犱竴涓柊鍚庣 | 缁ф壙 `backends.Backend`锛屽疄鐜?`render()`锛屾敞鍐岃繘 `REGISTRY` |

---

## 6 宸茬煡闂锛堟寜"鏈€渚垮疁鍏堜慨"鎺掑簭锛?
### 6.1 QC 宸查噸鏍囷紙2026-11-01锛屼袱鏈湡涔?+ 涓変釜璐熸帶锛?
鏃х増 8/8 璇垽锛屾牴鍥犱笌鏂板垽鎹細

| 瑙勫垯 | 鏃ч棶棰?| 鏂板仛娉?| 鐪熶功瀹炴祴 |
|---|---|---|---|
| `still_ratio` | `螖<0.0015` 鎶婃參杩愰暅褰撻潤姝?| 鍙粺璁?杩炵画 鈮? 甯?螖<1e-5"鐨勫喕缁撴 | 0.000鈥?.012 |
| `sharpness_ratio` | 鏁村抚(鍚儗鏅?姣旀暣椤碉紝涓嶅彲姣?| 鍙祴鍗＄墖/婊″箙鍖哄煙锛屽師椤电缉鍒板悓涓€灏哄 | 0.788鈥?.931 |
| `drift_ncc` | 鎺?鎽囪棣栧熬甯уぉ鐒朵笉鍚?| **涓嶅啀浣滀负鍒ゆ嵁**锛屼粎鐣欎綔璇婃柇 | 0.36鈥?.97 |
| `color_sim`锛堟柊澧烇級 | 鈥?| 鍗＄墖鍖哄煙涓庢簮椤电殑 RGB 鐩存柟鍥句氦闆?| 0.801鈥?.963 |

21/21 accept锛屽潎鍒?0.911銆傝礋鎺э紙2 s / 24fps锛宍runs/_qc_*.mp4` 鍙鐜帮級锛?
| 璐熸帶 | 缁撴灉 | 瑙﹀彂鐞嗙敱 |
|---|---|---|
| 闈欐€佸抚閲嶅 | 鎷掔粷 | `motion 0.0001`銆乣still 0.70` |
| 鍏ㄩ粦 | 鎷掔粷 | black 1.00 + 4 鏉?|
| 鐢?p12 鐨勭墖姣?p17 鐨勯〉 | 鎷掔粷 | `color 0.17`銆乣sharpness 0.46` |

澶嶇幇璐熸帶鐨勮剼鏈病鏈夊叆搴擄紙涓€娆℃€ч獙璇侊級锛沗tests/test_smoke.py` 瑕嗙洊浜?`still` 涓?`color_sim` 鐨勭函鍑芥暟閫昏緫銆?
### 6.2 鎬ц兘锛堝崟椤靛钩闈㈡澘 8鈥?1 s锛岄璺?195 s / 21 椤碉級

- 鐪熶功鐢ㄥ钩闈㈡澘锛堝崟灞傦級锛屽崟闀滃ご 3鈥? s 鍐呭瑕?8鈥?1 s 娓叉煋锛沗--jobs 2` 涓嬫暣鏈?195 s銆?- 璺ㄩ〉 `fill` 鐢?1280 瀹界殑鏉匡紝姣斿崱鐗囨ā寮忔參绾?30%锛坧13 18.5 s vs p05 14.2 s锛夈€?- B 杞?`procedural` 浠嶆槸 35鈥?8 s/闀滐紱鐪熶功娌＄敤 B 杞紝鐣欎綔楂樺厜闀滃ご銆?- 澶嶈窇鍛戒腑缂撳瓨鍙 15.8 s锛堝惈鏁寸墖閲嶇紪鐮侊級銆?
### 6.3 鍏朵綑缂哄彛

| 缂哄彛 | 褰卞搷 | 寤鸿 |
|---|---|---|
| 甯﹀瓧椤典笉鑳藉紑鍒嗗眰 | 鐩墠鏁撮〉鍒氭€ц繍闀滐紝娌℃湁鍓嶆櫙瑙嗗樊 | 鍏堝仛鏂囧瓧 mask锛堣妫€娴嬶級+ 鎶婃枃瀛楀浐瀹氬埌 far 灞傦紝鍐嶆寜缁勪欢鎺掗櫎鍚瓧鍓嶆櫙锛沗decompose()` 閲屽凡鏈夐€愮粍浠惰繃婊ょ殑浣嶇疆 |
| 鏃犲瓧骞?鏃佺櫧 | 椤典笂宸叉湁鍗板埛瀛楋紝鏈啀鍙犲瓧骞曪紱鎴愮墖鏃犻煶杞?| 鎺?TTS 鍑?wav 鈫?`assemble.finish(audio=...)`锛涘瓧骞曞彟璧蜂竴灞傦紝鍒噸澶嶉〉涓婃枃瀛?|
| 鏈帴瑙掕壊涓€鑷存€у害閲?| 鏃?璺ㄩ〉涓€鑷寸巼"璇佹嵁 | 鍔?DINO/CLIP 宓屽叆锛堥渶 torch锛夛紝鎴栧厛鐢ㄩ鑹茬洿鏂瑰浘鍗犱綅 |
| Web 鏈湪娴忚鍣ㄧ偣杩?| JS 鍙兘鎶ラ敊 | 璧?`ui` 鍚庡紑 F12 鐪?console锛圓PI/Range 宸叉祴閫氾級 |


---

## 7 鎺ョ湡 I2V 鐨勪袱鏉¤矾

**璺嚎 1锛堥浂浠ｇ爜锛屼粖澶╁氨鑳界敤锛?*锛歚pregen` 鍚庣
1. 鍦ㄤ换鎰忔湁鍗?鏈夐搴︾殑鍦版柟璺?Wan / 鍙伒 / Seedance锛屽緱鍒?`p06.mp4` 绛夛紱
2. 鏀捐繘 `books/<id>/clips/`锛屾枃浠跺悕 = `shot_id`锛?3. `book.json` 閲屾妸璇ラ〉鍐欐垚 `"track": "pregen"`锛?4. `run` 浼氳嚜鍔ㄦ妸瀹冨綊涓€鍖栧埌 1280脳720/24fps銆佽窇 QC銆佸弬涓庢嫾鎺ャ€?
**璺嚎 2锛堝啓鍚庣锛?*锛歚backends.py` 閲屽姞 `WanLocalBackend`锛宍produces="video"`锛岃繑鍥?`ShotRender(video_path=...)`銆?鎺ュ彛宸茬粡鑳藉悓鏃跺悆"閫愬抚"鍜?鏁存鏂囦欢"锛屼笉闇€瑕佹敼缂栨帓銆?
> 鏈満 8 GB VRAM 璺?14B 绾?I2V 涓嶇幇瀹烇紝寤鸿棰勭敓鎴?+ 缂撳瓨锛堢幇鍦烘柇缃戜篃鏈夌墖鍙斁锛夈€?
---

## 8 鎺掗殰

| 鐥囩姸 | 鍘熷洜 | 澶勭悊 |
|---|---|---|
| `ffmpeg not found` | 涓嶅湪 PATH | `winget install Gyan.FFmpeg` 鎴栬 `BOOK2REAL_FFMPEG` |
| 瀛楀箷/涓枃鍙樻柟鍧?| 缂哄瓧浣?| 瑁呭井杞泤榛戯紱`assemble._font()` 浼氬洖閫€ simhei |
| 鎵€鏈夐暅澶撮兘 `static` | 棰勭畻鍒扮偣 | 鐪?`events.jsonl` 鐨?`budget.exceeded`锛岃皟澶?`--budget` |
| 鍒嗗眰娌℃晥鏋滐紙杩樻槸鏁村紶鍔級 | 璇ラ〉/璇ヤ功 `layers:false`锛屾垨鍓嶆櫙涓庤儗鏅悓鑹?| 鐪熶功甯﹀瓧椤垫槸**鏁呮剰**鍏抽棴鐨勶紱瑕佸紑鍏堣В鍐虫枃瀛?mask锛埪?锛?|
| 鐢婚潰杈圭紭鏈夋嫋褰?| 鍒嗗眰琛ユ礊鐨勬墿鏁ｅ～鍏?| 灞炲凡鐭ヤ唬浠凤紝楂橀鍖哄煙寤鸿鎹?patch-match |
| `plan` 閲屾椂闀块兘鏄?6.0s | 鏂囨闀垮害鏅亶 >15 瀛楋紝琚?clamp 鍒?6.2 | `storyboard.plan()` 閲屾敼 `clamp(3.2+0.20*len, 3.6, 6.2)` |

---

## 9 寤鸿鐨勪笅涓€姝ワ紙涓€鍛ㄥ唴锛?
1. **甯﹀瓧椤电殑瑙嗗樊鎭㈠**锛?鈥? 澶╋級锛氬湪 `layers.py` 鍔犳枃瀛楄妫€娴嬶紙姘村钩杩為€氬煙锛夛紝鎶婃枃瀛楅拤鍒?far 灞傚苟鍦ㄨ繎灞?alpha 涓婃寲娲烇紱鍏堟嬁 book2 鐨?p06/p13 涓ら〉璇曪紝閫愬抚鐪嬫湁娌℃湁閲嶅奖銆傝繖鏄洰鍓嶅敮涓€鎸′綇"鐪熶功涔熻兘鍒嗗眰"鐨勯棶棰樸€?2. **鏃佺櫧 + 瀛楀箷**锛? 澶╋級锛歍TS 鍑?wav 鈫?`assemble.finish(audio=...)`锛涘瓧骞曞彧鏀鹃〉澶?搴曢儴锛屽埆澶嶈堪椤典笂鍗板埛瀛椼€?3. **Web 鎾斁鍣ㄥ疄娴?*锛堝崐澶╋級锛歚python -m book2real ui --runs runs --open`锛岀偣寮€ 21 闀滅殑 timeline锛孎12 鐪?console銆?4. **I2V 楂樺厜闀滃ご**锛氬厛 `pregen` 璺戦€?4 涓法椤碉紝鍐嶈€冭檻鏈湴鍚庣銆?5. **鎬ц兘**锛氬彧鏈夐噸鍚?B 杞ㄦ椂鎵嶉渶瑕侊紙鍏夊奖钂欑増闄嶉噰鏍枫€乣jobs=2` 鍥哄畾锛夈€?
---

## 10 涓€鍙ヨ瘽鎻愰啋

杩欏涓滆タ鐨?*鍗栫偣涓嶆槸"鑳界敓鎴愯棰?锛屾槸"鏁存湰涔﹂兘鍦ㄥ姩涓斾笉浼氬穿"**銆?浠讳綍璁╄鐩栫巼涓嬮檷銆佽鐢婚潰鍑虹幇浼瓧鐨勬敼鍔紝閮芥瘮"澶氫竴涓偒鎶€闀滃ご"鏇翠簭銆?
