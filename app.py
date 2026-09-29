from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin, urlencode
from functools import wraps
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
import uuid

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'NTT123': 'toukai'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

AREA_CODE = "0220100"  # 青森市

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
SHELTER_REGISTRATIONS_FILE = os.path.join(APP_DIR, 'data', 'shelter_registrations.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
DAMAGE_REPORTS_FILE = os.path.join(APP_DIR, 'data', 'damage_reports.json')
DAMAGE_TYPES = [
    '火災', '浸水', '建物の被害', '道路・土砂', 'けが・救助', 'ライフライン', 'その他'
]

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
shelter_registrations = load_json(SHELTER_REGISTRATIONS_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])
damage_reports = load_json(DAMAGE_REPORTS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    results = []
    seen_names = set()
    for shelter in shelters + shelter_registrations:
        name = shelter.get('name')
        if name in seen_names:
            continue
        if not district or shelter.get('district') == district:
            results.append(shelter)
            if name:
                seen_names.add(name)
    return results


def get_valid_coordinates(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def geocode_damage_location(location):
    """青森市内の入力住所をOpenStreetMapの座標に変換する"""
    query = urlencode({
        'q': f'{location}, 青森市, 青森県, 日本',
        'format': 'jsonv2',
        'limit': 1,
        'countrycodes': 'jp',
        'viewbox': '140.4,41.0,141.0,40.6',
        'bounded': 1
    })
    req = urllib.request.Request(
        f'https://nominatim.openstreetmap.org/search?{query}',
        headers={'User-Agent': 'BousaiApp/1.0 (damage report map)'}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            results = json.loads(response.read())
        if not results:
            return None
        return get_valid_coordinates(results[0].get('lat'), results[0].get('lon'))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_damage_reports():
    with open(DAMAGE_REPORTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(damage_reports, f, ensure_ascii=False, indent=2)


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        # 認証チェック
        if ADMIN_CREDENTIALS.get(username) == password:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template(
            'login.html', error=True,
            message='IDまたはパスワードが正しくありません。', next=next_url
        )

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 市民からの被害通報
@app.route('/damage_report', methods=['GET', 'POST'])
def damage_report():
    form_data = {
        'occurred_at': datetime.now(JST).strftime('%Y-%m-%dT%H:%M'),
        'location': '',
        'latitude': '',
        'longitude': '',
        'damage_type': '',
        'details': '',
        'reporter': ''
    }

    if request.method == 'POST':
        form_data = {
            'occurred_at': request.form.get('occurred_at', '').strip(),
            'location': request.form.get('location', '').strip(),
            'latitude': request.form.get('latitude', '').strip(),
            'longitude': request.form.get('longitude', '').strip(),
            'damage_type': request.form.get('damage_type', '').strip(),
            'details': request.form.get('details', '').strip(),
            'reporter': request.form.get('reporter', '').strip()
        }
        if (
            not all(form_data[field] for field in (
                'occurred_at', 'location', 'damage_type', 'details', 'reporter'
            ))
            or form_data['damage_type'] not in DAMAGE_TYPES
        ):
            return render_template(
                'damage_report.html', error=True,
                message='必須項目をすべて入力してください。',
                damage_types=DAMAGE_TYPES, form_data=form_data
            )

        coordinates = get_valid_coordinates(
            form_data['latitude'], form_data['longitude']
        ) or geocode_damage_location(form_data['location'])
        if not coordinates:
            return render_template(
                'damage_report.html', error=True,
                message='場所を地図上で確認できませんでした。現在地を取得するか、住所を入力してください。',
                damage_types=DAMAGE_TYPES, form_data=form_data
            )
        form_data['latitude'], form_data['longitude'] = coordinates

        report = {
            'id': uuid.uuid4().hex[:10].upper(),
            **form_data,
            'submitted_at': datetime.now(JST).isoformat(timespec='seconds')
        }
        damage_reports.insert(0, report)
        try:
            save_damage_reports()
        except OSError:
            damage_reports.pop(0)
            return render_template(
                'damage_report.html', error=True,
                message='送信できませんでした。時間をおいて再度お試しください。',
                damage_types=DAMAGE_TYPES, form_data=form_data
            )

        form_data = {
            'occurred_at': datetime.now(JST).strftime('%Y-%m-%dT%H:%M'),
            'location': '',
            'latitude': '',
            'longitude': '',
            'damage_type': '',
            'details': '',
            'reporter': ''
        }
        return render_template(
            'damage_report.html', success=True, report=report,
            damage_types=DAMAGE_TYPES, form_data=form_data
        )

    return render_template(
        'damage_report.html', damage_types=DAMAGE_TYPES, form_data=form_data
    )

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return render_template(
                'shelter_register.html', error=True, message='施設名を入力してください。'
            )

        if any(shelter.get('name') == name for shelter in filter_shelters()):
            return render_template(
                'shelter_register.html',
                success=True,
                message='この施設はすでに登録されています。'
            )

        shelter_registrations.append({
            'name': name,
            'registered_at': datetime.now(JST).isoformat(timespec='seconds')
        })
        try:
            with open(SHELTER_REGISTRATIONS_FILE, 'w', encoding='utf-8') as f:
                json.dump(shelter_registrations, f, ensure_ascii=False, indent=2)
        except OSError:
            shelter_registrations.pop()
            return render_template(
                'shelter_register.html',
                error=True,
                message='登録に失敗しました。時間をおいて再度お試しください。'
            )

        return render_template(
            'shelter_register.html', success=True, message='施設の登録が完了しました。'
        )

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=filter_shelters())


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template(
        'board.html', instructions=resident_instructions,
        damage_reports=damage_reports
    )


@app.route('/api/damage_reports')
def get_damage_reports():
    active_reports = [
        {
            'id': report.get('id'),
            'location': report.get('location'),
            'latitude': report.get('latitude'),
            'longitude': report.get('longitude'),
            'damage_type': report.get('damage_type'),
            'details': report.get('details'),
            'occurred_at': report.get('occurred_at')
        }
        for report in damage_reports
        if not report.get('resolved')
        and get_valid_coordinates(report.get('latitude'), report.get('longitude'))
    ]
    return jsonify({
        'reports': active_reports,
        'can_resolve': bool(session.get('logged_in'))
    })


@app.route('/api/damage_reports/<report_id>/resolve', methods=['POST'])
@login_required
def resolve_damage_report(report_id):
    report = next(
        (item for item in damage_reports if item.get('id') == report_id), None
    )
    if not report:
        return jsonify({'error': '通報が見つかりません。'}), 404
    if report.get('resolved'):
        return jsonify({'resolved': True})

    report['resolved'] = True
    report['resolved_at'] = datetime.now(JST).isoformat(timespec='seconds')
    report['resolved_by'] = session.get('username', '')
    try:
        save_damage_reports()
    except OSError:
        report.pop('resolved', None)
        report.pop('resolved_at', None)
        report.pop('resolved_by', None)
        return jsonify({'error': '対応状況を保存できませんでした。'}), 500
    return jsonify({'resolved': True})

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
