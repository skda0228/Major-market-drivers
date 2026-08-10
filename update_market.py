import json
import os
import datetime
import zoneinfo
import subprocess
import re
import sys

# 스크립트가 실행된 위치와 상관없이, 무조건 스크립트가 있는 폴더로 작업 경로 강제 변경
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

env_file = os.path.join(script_dir, ".env")
if os.path.exists(env_file):
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ[k.strip()] = v.strip().strip("'\"")

try:
    import yfinance as yf
except ImportError:
    print("yfinance 라이브러리가 필요합니다. pip install yfinance 로 설치해주세요.")
    sys.exit(1)

try:
    import feedparser
except ImportError:
    import urllib.request
    import xml.etree.ElementTree as ET
    feedparser = None

try:
    from google import genai
except ImportError:
    genai = None

# 1. 티커 매핑
TICKER_MAP = {
    "KOSPI": "^KS11",
    "SPX": "^GSPC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
    "N225": "^N225",
    "US10Y": "^TNX",
    "VIX": "^VIX",
    "USD/KRW": "KRW=X",
    "WTI": "CL=F",
    "XAU/USD": "GC=F",
    "SHCOMP": "000001.SS",
    "HSI": "^HSI",
    "VNI": "^VNINDEX.VN",
    "SX5E": "^STOXX50E",
    "UKX": "^FTSE",
    "DAX": "^GDAXI",
    "SENSEX": "^BSESN"
}

def get_latest_price(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="1d")
        if df.empty:
            return None, None, None, None
        
        actual_date_str = df.index[-1].strftime("%Y-%m-%d")
        close_price = df['Close'].iloc[-1]
        prev_close = ticker.info.get('previousClose')
        
        if prev_close is None:
            df2 = ticker.history(period="5d")
            if len(df2) > 1:
                prev_close = df2['Close'].iloc[-2]
            else:
                prev_close = close_price
                
        change = close_price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0
        
        return close_price, change, change_pct, actual_date_str
    except Exception as e:
        print(f"Error fetching {ticker_symbol}: {e}")
        return None, None, None, None

def format_value(code, value):
    if code in ["US10Y"]:
        return f"{value:.3f}%"
    elif code in ["VIX", "WTI"]:
        return f"{value:.2f}"
    else:
        return f"{value:,.2f}"

def get_dir(code, change_pct):
    if code in ["US10Y", "VIX", "WTI", "XAU/USD", "USD/KRW"]:
        return "nt"
    if change_pct > 0:
        return "up"
    elif change_pct < 0:
        return "dn"
    return "nt"

def generate_ai_analysis(market_name, change_pct):
    if not genai or not os.environ.get("GEMINI_API_KEY"):
        return f"AI 분석 생략 (API 키 없음). {market_name} 변동: {change_pct:.2f}%"
    
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        prompt = f"오늘 {market_name} 지수가 {change_pct:.2f}% 급변했습니다. 금융 뉴스 헤드라인을 참고해서 원인을 1문장으로 요약해주세요."
        response = client.models.generate_content(
            model='gemini-1.5-pro-latest',
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        return f"AI 분석 실패: {e}"

def fetch_google_news(query):
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
    
    news_items = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            for item in root.findall('.//item')[:15]:
                title = item.find('title').text if item.find('title') is not None else ''
                link = item.find('link').text if item.find('link') is not None else ''
                pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ''
                source = item.find('source').text if item.find('source') is not None else ''
                news_items.append({'title': title, 'link': link, 'date': pub_date, 'source': source})
    except Exception as e:
        print(f"RSS 가져오기 실패: {e}")
    return news_items

def update_semiconductor_page():
    if not genai or not os.environ.get("GEMINI_API_KEY"):
        print("💡 제미나이 모듈이 없거나 API 키가 없어 반도체 AI 업데이트를 건너뜁니다.")
        return
        
    print("=== 반도체 페이지 AI 업데이트 시작 ===")
    
    # 1. 뉴스 긁어오기
    kr_news = fetch_google_news("한국 반도체 삼성전자 SK하이닉스")
    us_news = fetch_google_news("미국 반도체 엔비디아 인텔 AMD")
    global_news = fetch_google_news("글로벌 반도체 주식 시황")
    
    all_news = "한국 반도체 관련:\n" + "\n".join([f"- {n['title']} ({n['source']})" for n in kr_news[:7]]) + "\n"
    all_news += "미국 반도체 관련:\n" + "\n".join([f"- {n['title']} ({n['source']})" for n in us_news[:7]]) + "\n"
    all_news += "글로벌 시황:\n" + "\n".join([f"- {n['title']} ({n['source']})" for n in global_news[:7]])
    
    # 2. 제미나이에게 프롬프트 요청
    prompt = f"""다음은 최신 반도체 관련 뉴스 헤드라인입니다.

{all_news}

위 내용을 바탕으로 다음을 작성해주세요:
1. 오늘의 인사이트: 글로벌 반도체 시장의 핵심 트렌드와 흐름을 3~4문장으로 전문가적인 시각에서 요약. 중요한 내용은 <b> 태그로 굵게 표시.
2. KR(한국), US(미국), CN(중국), JP(일본), EU(유럽) 5개 지역별로 주요 뉴스를 분류하여 HTML 코드로 작성. (관련 뉴스가 없으면 기존 흐름이나 대략적인 상황을 1개라도 적어줄 것)

반드시 아래와 같은 형태의 엄격한 JSON 형식으로 출력해주세요 (마크다운 코드블록 안 써도 됨):
{{
  "insight": "<p>내용...</p>",
  "KR": "<div class=\\"newslist\\">\\n  <div class=\\"nitem\\">\\n    <div class=\\"nd\\">08.10</div>\\n    <div><div class=\\"nt\\">뉴스 제목</div>\\n      <div class=\\"nw\\">뉴스 내용 요약 및 <b>강조</b></div>\\n      <a class=\\"nsrc\\" href=\\"링크\\" target=\\"_blank\\" rel=\\"noopener\\">언론사명 →</a></div>\\n  </div>\\n</div>",
  "US": "<div class=\\"newslist\\">...</div>",
  "CN": "<div class=\\"newslist\\">...</div>",
  "JP": "<div class=\\"newslist\\">...</div>",
  "EU": "<div class=\\"newslist\\">...</div>"
}}
"""
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        response = client.models.generate_content(
            model='gemini-1.5-pro-latest',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        res_json = json.loads(response.text)
        
        # 3. HTML 파일 교체
        html_file = "semiconductor.html"
        if not os.path.exists(html_file):
            return
            
        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # 정규식으로 영역 교체
        def replace_block(text, marker, new_html):
            pattern = f"<!-- {marker}_START -->.*?<!-- {marker}_END -->"
            repl = f"<!-- {marker}_START -->\n{new_html}\n<!-- {marker}_END -->"
            return re.sub(pattern, repl, text, flags=re.DOTALL)
            
        content = replace_block(content, "INSIGHT", res_json.get("insight", ""))
        for region in ["KR", "US", "CN", "JP", "EU"]:
            content = replace_block(content, f"{region}_NEWS", res_json.get(region, ""))
            
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(content)
        print("💡 반도체 페이지 AI 업데이트 완료!")
        return True
    except Exception as e:
        print(f"반도체 AI 업데이트 중 오류 발생: {e}")
        return False

def update_html_dates(kst, last_trade_full, last_trade_str):
    html_files = ["index.html", "semiconductor.html", "credit-balance.html"]
    today_dt = datetime.datetime.now(kst).date()
    today_dot = datetime.datetime.now(kst).strftime("%Y.%m.%d %H:%M")
    today_just_date = today_dot[:10]
    
    # [잔여 이슈 2 해결] D-Day 자동 계산 로직
    def replace_dday(match):
        target_date_str = match.group(1) # "08.13" 등
        try:
            year = today_dt.year
            target_dt = datetime.datetime.strptime(f"{year}.{target_date_str}", "%Y.%m.%d").date()
            delta = (target_dt - today_dt).days
            if delta > 0:
                d_str = f"D-{delta}"
            elif delta == 0:
                d_str = "D-Day"
            else:
                d_str = f"D+{abs(delta)}"
            return f'{target_date_str}</span> <span class="d">{d_str}</span>'
        except Exception:
            return match.group(0)
    
    for html_file in html_files:
        if os.path.exists(html_file):
            with open(html_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = content
            new_content = re.sub(r'<span class="rd">\d{4}\.\d{2}\.\d{2}(?: \d{2}:\d{2})?</span>', f'<span class="rd">{today_dot}</span>', new_content)
            new_content = re.sub(r'마지막 거래일 <b>\d{4}\.\d{2}\.\d{2}\([월화수목금토일]\)</b>', f'마지막 거래일 <b>{last_trade_full}</b>', new_content)
            new_content = re.sub(r'<div class="ds">\d{4}\.\d{2}\.\d{2} CLOSE', f'<div class="ds">{last_trade_str} CLOSE', new_content)
            new_content = re.sub(r'다음 관문 · \d{4}\.\d{2}\.\d{2} 기준', f'다음 관문 · {today_just_date} 기준', new_content)
            
            # 카운트다운 정규식 갱신
            new_content = re.sub(r'(\d{2}\.\d{2})</span> <span class="d">D[-+A-Za-z0-9]+</span>', replace_dday, new_content)

            if new_content != content:
                with open(html_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"{html_file} 날짜/D-Day 연동 업데이트 완료!")

def main():
    json_file = "indicators.json"
    if not os.path.exists(json_file):
        print(f"{json_file} 파일을 찾을 수 없습니다.")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    kst = zoneinfo.ZoneInfo("Asia/Seoul")
    today_str = datetime.datetime.now(kst).strftime("%Y-%m-%d")

    big_change_notes = []
    any_updates_made = False
    asof_candidates = [data.get("asOf", "2000-01-01")]

    print(f"=== 실행 시작 (서버시간: {today_str}) ===")

    # 1. 핵심 지표 업데이트
    for item in data.get("items", []):
        code = item.get("code")
        symbol = TICKER_MAP.get(code)
        if not symbol:
            continue
            
        print(f"[{code}] 확인 중...")
        price, change, change_pct, actual_date = get_latest_price(symbol)
        
        if price is None or actual_date is None:
            continue
            
        last_saved_date = item.get("lastDate", "2000-01-01")
        
        if actual_date > last_saved_date:
            print(f"  -> 🟢 새로운 거래일 감지 ({last_saved_date} -> {actual_date})! 갱신 진행.")
            is_new_day = True
        elif actual_date == last_saved_date:
            print(f"  -> 🟡 같은 거래일({actual_date}). 장중 갱신(마지막 값 덮어쓰기)만 수행.")
            is_new_day = False
        else:
            print(f"  -> 🔴 과거 거래일({actual_date} <= {last_saved_date}). 업데이트 완전히 스킵.")
            continue 
            
        any_updates_made = True
        
        # [잔여 이슈 1 해결] WTI, 금, 환율 등 연속거래 자산은 전체 asOf 후보에서 제외
        if code not in ["USD/KRW", "WTI", "XAU/USD"]:
            asof_candidates.append(actual_date)
            
        item["lastDate"] = actual_date
        
        if abs(change_pct) >= 3.0:
            print(f"!!! {item['name']} 급변 감지 ({change_pct:.2f}%) -> AI 분석 호출")
            note = generate_ai_analysis(item['name'], change_pct)
            item["note"] = note
            big_change_notes.append(f"{item['name']}: {note}")

        item["value"] = format_value(code, price)
        sym = "▲" if change > 0 else "▼" if change < 0 else "-"
        val_fmt = f"{abs(change):.3f}%p" if code == "US10Y" else f"{abs(change):.2f}"
        item["change"] = f"{sym} {val_fmt} ({change_pct:+.2f}%)"
        item["dir"] = get_dir(code, change_pct)
        
        val = float(round(price, 2))
        
        if "q" in item:
            if is_new_day or len(item["q"]) == 0:
                item["q"].append(val)
                while len(item["q"]) > 23:
                    item["q"].pop(0)
            else:
                item["q"][-1] = val 
                
        if "y" in item:
            actual_month = actual_date[:7] 
            last_saved_month = last_saved_date[:7]
            
            if actual_month > last_saved_month or len(item["y"]) == 0:
                item["y"].append(val)
                while len(item["y"]) > 12:
                    item["y"].pop(0)
            else:
                item["y"][-1] = val

    # 2. 기타 지표 업데이트
    for other in data.get("others", []):
        code = other.get("code")
        symbol = TICKER_MAP.get(code)
        if not symbol:
            continue
            
        price, change, change_pct, actual_date = get_latest_price(symbol)
        if price is None or actual_date is None:
            continue
            
        last_saved_date = other.get("lastDate", "2000-01-01")
        if actual_date >= last_saved_date:
            if actual_date > last_saved_date:
                # 기타 지표(유럽/아시아 증시 등)는 주식과 같이 명확한 장 마감이 있으므로 asOf 포함
                asof_candidates.append(actual_date)
            other["lastDate"] = actual_date
            other["value"] = format_value(code, price)
            other["change"] = f"{change_pct:+.2f}%"
            other["dir"] = get_dir(code, change_pct)
            any_updates_made = True

    if not any_updates_made:
        print("💡 새롭게 갱신된 데이터가 없습니다. (주말/휴장일) - JSON 및 HTML 파일 수정 없이 종료합니다.")
        return

    final_asof = max(asof_candidates)
    data["asOf"] = final_asof

    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        
    print(f"JSON 업데이트 완료! (asOf: {final_asof})")
    
    last_trade_str = final_asof.replace("-", ".")
    try:
        dt = datetime.datetime.strptime(final_asof, "%Y-%m-%d")
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        last_trade_day = weekdays[dt.weekday()]
        last_trade_full = f"{last_trade_str}({last_trade_day})"
    except Exception:
        last_trade_full = last_trade_str

    update_semiconductor_page()
    update_html_dates(kst, last_trade_full, last_trade_str)

    # 3. Git 자동 커밋 & 푸시
    try:
        subprocess.run(["git", "add", "indicators.json", "index.html", "semiconductor.html", "credit-balance.html"], check=True)
        result = subprocess.run(["git", "diff", "--staged"], capture_output=True, text=True)
        if result.stdout.strip():
            commit_msg = f"Auto update: {final_asof}"
            if big_change_notes:
                commit_msg += "\n\n" + "\n".join(big_change_notes)
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)
            subprocess.run(["git", "push"], check=True)
            print(f"Git 푸시까지 완벽하게 완료되었습니다! ({final_asof})")
        else:
            print("Git 변경사항이 없어 커밋을 건너뜁니다.")
    except Exception as e:
        print(f"Git 작업 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
