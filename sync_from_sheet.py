"""スプレッドシートの配当設定データからstocks.jsonを生成するスクリプト
GASのfixAllIssues実行ログから53銘柄データを同期する"""
import json, os

# スプレッドシートの配当設定+MF取込データ（手動コピー or GASエクスポートで更新）
# 以下は現在のスプレッドシート53銘柄の完全データ
stocks = [
    {"code":1343,"name":"NFJ-REIT","shares":25,"buy_price":1908,"cur_price":2062,"annual_div":50,"mid_div":25,"mid_month":7,"end_month":1,"nisa":"課税","sector":"不動産業"},
    {"code":1489,"name":"NF日経高配当50","shares":16,"buy_price":1967,"cur_price":3151,"annual_div":48,"mid_div":24,"mid_month":7,"end_month":1,"nisa":"課税","sector":"ETF"},
    {"code":1928,"name":"積水ハウス","shares":16,"buy_price":3355,"cur_price":3578,"annual_div":125,"mid_div":62,"mid_month":10,"end_month":4,"nisa":"課税","sector":"建設業"},
    {"code":1951,"name":"エクシオグループ","shares":20,"buy_price":1579,"cur_price":2793,"annual_div":62,"mid_div":28,"mid_month":12,"end_month":6,"nisa":"課税","sector":"建設業"},
    {"code":2003,"name":"日東富士","shares":24,"buy_price":1312,"cur_price":1857,"annual_div":185,"mid_div":88,"mid_month":12,"end_month":6,"nisa":"課税","sector":"食料品"},
    {"code":2169,"name":"CDS","shares":20,"buy_price":1788,"cur_price":1838,"annual_div":65,"mid_div":32,"mid_month":9,"end_month":3,"nisa":"課税","sector":"サービス業"},
    {"code":2393,"name":"日本ケア","shares":18,"buy_price":1899,"cur_price":2400,"annual_div":75,"mid_div":37,"mid_month":12,"end_month":6,"nisa":"課税","sector":"サービス業"},
    {"code":2593,"name":"伊藤園","shares":8,"buy_price":3735,"cur_price":3066,"annual_div":42,"mid_div":20,"mid_month":12,"end_month":7,"nisa":"課税","sector":"食料品"},
    {"code":2918,"name":"わらべや","shares":15,"buy_price":2352,"cur_price":3180,"annual_div":50,"mid_div":24,"mid_month":11,"end_month":5,"nisa":"課税","sector":"食料品"},
    {"code":3076,"name":"あいHD","shares":13,"buy_price":2452,"cur_price":2805,"annual_div":55,"mid_div":22,"mid_month":3,"end_month":9,"nisa":"課税","sector":"卸売業"},
    {"code":3817,"name":"SRAHD","shares":1,"buy_price":3785,"cur_price":4835,"annual_div":120,"mid_div":60,"mid_month":12,"end_month":6,"nisa":"課税","sector":"情報・通信業"},
    {"code":3834,"name":"朝日ネット","shares":50,"buy_price":632,"cur_price":656,"annual_div":26,"mid_div":10,"mid_month":12,"end_month":6,"nisa":"課税","sector":"情報・通信業"},
    {"code":4008,"name":"住友精化","shares":40,"buy_price":1000,"cur_price":1227,"annual_div":90,"mid_div":45,"mid_month":12,"end_month":6,"nisa":"課税","sector":"化学"},
    {"code":4042,"name":"東ソー","shares":20,"buy_price":2040,"cur_price":2364,"annual_div":80,"mid_div":40,"mid_month":12,"end_month":6,"nisa":"課税","sector":"化学"},
    {"code":4188,"name":"三菱ケミカルグループ","shares":35,"buy_price":849,"cur_price":920,"annual_div":32,"mid_div":16,"mid_month":12,"end_month":6,"nisa":"課税","sector":"化学"},
    {"code":4345,"name":"シーティーエス","shares":40,"buy_price":810,"cur_price":927,"annual_div":50,"mid_div":25,"mid_month":12,"end_month":6,"nisa":"課税","sector":"情報・通信業"},
    {"code":4743,"name":"アイティフォー","shares":30,"buy_price":1382,"cur_price":1703,"annual_div":28,"mid_div":14,"mid_month":12,"end_month":6,"nisa":"課税","sector":"情報・通信業"},
    {"code":5334,"name":"日特殊陶","shares":1,"buy_price":4076,"cur_price":7512,"annual_div":166,"mid_div":83,"mid_month":12,"end_month":6,"nisa":"課税","sector":"ガラス・土石"},
    {"code":5388,"name":"クニミネ工業","shares":30,"buy_price":1095,"cur_price":1275,"annual_div":40,"mid_div":20,"mid_month":12,"end_month":6,"nisa":"課税","sector":"ガラス・土石"},
    {"code":5464,"name":"モリ工業","shares":55,"buy_price":1160,"cur_price":971,"annual_div":100,"mid_div":50,"mid_month":12,"end_month":6,"nisa":"課税","sector":"鉄鋼"},
    {"code":6073,"name":"アサンテ","shares":21,"buy_price":1611,"cur_price":1519,"annual_div":62,"mid_div":31,"mid_month":12,"end_month":6,"nisa":"課税","sector":"サービス業"},
    {"code":6345,"name":"アイチ","shares":30,"buy_price":1105,"cur_price":1341,"annual_div":50,"mid_div":20,"mid_month":12,"end_month":6,"nisa":"課税","sector":"機械"},
    {"code":6365,"name":"電業社","shares":10,"buy_price":3738,"cur_price":5960,"annual_div":200,"mid_div":80,"mid_month":12,"end_month":6,"nisa":"課税","sector":"機械"},
    {"code":6432,"name":"竹内製作所","shares":6,"buy_price":5350,"cur_price":6340,"annual_div":230,"mid_div":115,"mid_month":11,"end_month":5,"nisa":"課税","sector":"機械"},
    {"code":6454,"name":"マックス","shares":48,"buy_price":775,"cur_price":1657,"annual_div":66,"mid_div":33,"mid_month":12,"end_month":6,"nisa":"課税","sector":"機械"},
    {"code":6539,"name":"MS-Japan","shares":8,"buy_price":1140,"cur_price":993,"annual_div":60,"mid_div":30,"mid_month":12,"end_month":6,"nisa":"課税","sector":"サービス業"},
    {"code":6652,"name":"IDEC","shares":8,"buy_price":2699,"cur_price":3155,"annual_div":55,"mid_div":25,"mid_month":12,"end_month":6,"nisa":"課税","sector":"電気機器"},
    {"code":6745,"name":"ホーチキ","shares":69,"buy_price":749,"cur_price":2055,"annual_div":70,"mid_div":30,"mid_month":12,"end_month":6,"nisa":"課税","sector":"電気機器"},
    {"code":7241,"name":"フタバ産","shares":42,"buy_price":974,"cur_price":995,"annual_div":25,"mid_div":0,"mid_month":12,"end_month":6,"nisa":"課税","sector":"輸送用機器"},
    {"code":7723,"name":"愛知時","shares":25,"buy_price":2725,"cur_price":2987,"annual_div":95,"mid_div":45,"mid_month":12,"end_month":6,"nisa":"課税","sector":"精密機器"},
    {"code":7820,"name":"ニホンフラッシュ","shares":33,"buy_price":938,"cur_price":820,"annual_div":30,"mid_div":15,"mid_month":12,"end_month":6,"nisa":"課税","sector":"その他製品"},
    {"code":7931,"name":"未来工業","shares":10,"buy_price":4043,"cur_price":3245,"annual_div":55,"mid_div":25,"mid_month":12,"end_month":6,"nisa":"課税","sector":"電気機器"},
    {"code":7994,"name":"オカムラ","shares":20,"buy_price":1753,"cur_price":2581,"annual_div":52,"mid_div":26,"mid_month":12,"end_month":6,"nisa":"課税","sector":"機械"},
    {"code":8058,"name":"三菱商事","shares":40,"buy_price":2656,"cur_price":5425,"annual_div":100,"mid_div":50,"mid_month":12,"end_month":6,"nisa":"課税","sector":"卸売業"},
    {"code":8130,"name":"サンゲツ","shares":12,"buy_price":2639,"cur_price":3065,"annual_div":66,"mid_div":33,"mid_month":12,"end_month":6,"nisa":"課税","sector":"卸売業"},
    {"code":8306,"name":"三菱UFJ","shares":15,"buy_price":2710,"cur_price":2811,"annual_div":50,"mid_div":25,"mid_month":12,"end_month":6,"nisa":"課税","sector":"銀行業"},
    {"code":8411,"name":"みずほ","shares":5,"buy_price":3242,"cur_price":6539,"annual_div":120,"mid_div":60,"mid_month":12,"end_month":6,"nisa":"課税","sector":"銀行業"},
    {"code":8584,"name":"ジャックス","shares":23,"buy_price":4777,"cur_price":4140,"annual_div":105,"mid_div":50,"mid_month":12,"end_month":6,"nisa":"課税","sector":"その他金融業"},
    {"code":8593,"name":"三菱HCキャピタル","shares":21,"buy_price":1106,"cur_price":1483,"annual_div":186,"mid_div":93,"mid_month":12,"end_month":6,"nisa":"課税","sector":"その他金融業"},
    {"code":8725,"name":"MS&AD","shares":10,"buy_price":3261,"cur_price":4089,"annual_div":300,"mid_div":150,"mid_month":12,"end_month":6,"nisa":"課税","sector":"保険業"},
    {"code":9057,"name":"遠州トラック","shares":15,"buy_price":2850,"cur_price":3475,"annual_div":50,"mid_div":0,"mid_month":12,"end_month":6,"nisa":"課税","sector":"倉庫・運輸"},
    {"code":9069,"name":"センコーグループHD","shares":33,"buy_price":1068,"cur_price":1873,"annual_div":48,"mid_div":24,"mid_month":12,"end_month":6,"nisa":"課税","sector":"倉庫・運輸"},
    {"code":9303,"name":"住友倉","shares":6,"buy_price":2545,"cur_price":4145,"annual_div":55,"mid_div":27,"mid_month":12,"end_month":6,"nisa":"課税","sector":"倉庫・運輸"},
    {"code":9368,"name":"キムラユニティー","shares":50,"buy_price":798,"cur_price":902,"annual_div":30,"mid_div":15,"mid_month":12,"end_month":6,"nisa":"課税","sector":"サービス業"},
    {"code":9381,"name":"エーアイテイー","shares":15,"buy_price":2059,"cur_price":2248,"annual_div":68,"mid_div":34,"mid_month":12,"end_month":6,"nisa":"課税","sector":"倉庫・運輸"},
    {"code":9432,"name":"NTT","shares":350,"buy_price":146,"cur_price":155,"annual_div":5.2,"mid_div":2.6,"mid_month":12,"end_month":6,"nisa":"課税","sector":"情報・通信業"},
    {"code":9433,"name":"KDDI","shares":14,"buy_price":2275,"cur_price":2703,"annual_div":140,"mid_div":70,"mid_month":12,"end_month":6,"nisa":"課税","sector":"情報・通信業"},
    {"code":9511,"name":"沖縄電力","shares":15,"buy_price":889,"cur_price":1043,"annual_div":40,"mid_div":20,"mid_month":12,"end_month":6,"nisa":"課税","sector":"電気・ガス"},
    {"code":9513,"name":"Jパワー","shares":22,"buy_price":2435,"cur_price":4274,"annual_div":100,"mid_div":50,"mid_month":12,"end_month":6,"nisa":"課税","sector":"電気・ガス"},
    {"code":9769,"name":"学究社","shares":1,"buy_price":2093,"cur_price":2407,"annual_div":60,"mid_div":30,"mid_month":12,"end_month":6,"nisa":"課税","sector":"サービス業"},
    {"code":9795,"name":"ステップ","shares":16,"buy_price":1959,"cur_price":2427,"annual_div":50,"mid_div":25,"mid_month":12,"end_month":6,"nisa":"課税","sector":"サービス業"},
    {"code":9882,"name":"イエローハット","shares":34,"buy_price":894,"cur_price":1605,"annual_div":66,"mid_div":32,"mid_month":12,"end_month":6,"nisa":"課税","sector":"小売業"},
    {"code":9986,"name":"蔵王産業","shares":28,"buy_price":2563,"cur_price":2669,"annual_div":42,"mid_div":21,"mid_month":12,"end_month":6,"nisa":"課税","sector":"卸売業"}
]

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(stocks, f, ensure_ascii=False, indent=2)
print(f"Done: {len(stocks)} stocks written to {out}")
